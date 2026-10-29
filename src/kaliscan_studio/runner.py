"""Ejecución secuencial con salida persistente y cancelación."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from .catalog import TOOLS
from .commands import build_command, host_in_url
from .models import Plan, PlannedTask, ScanOptions
from .parsers import parse_tool
from .projects import Project, safe_name
from .suggestions import Suggestion


EventCallback = Callable[[str, str], None]


class ScanEngine:
    def __init__(self, project: Project, tool_paths: dict[str, str] | None = None, event: EventCallback | None = None):
        self.project = project
        self.tool_paths = tool_paths or {}
        self.event = event or (lambda _kind, _message: None)
        self.cancelled = threading.Event()
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _binary(self, tool: str) -> str | None:
        key = "nmap" if tool in {"nmap_versions", "nmap_vuln"} else tool
        configured = self.tool_paths.get(key)
        if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured
        return next((path for name in TOOLS[key].binaries if (path := shutil.which(name))), None)

    def _run_task(self, run_id: str, task: PlannedTask, options: ScanOptions, ports: list[int] | None = None) -> list[tuple[str, str, dict]]:
        binary = self._binary(task.tool)
        if not binary:
            self.event("error", f"Falta {TOOLS['nmap' if task.tool in {'nmap_versions', 'nmap_vuln'} else task.tool].label}; se omitió {task.target}.")
            return []
        folder = self.project.output_dir(run_id, task.tool)
        stem = safe_name(task.target) + "-" + uuid.uuid4().hex[:6]
        try:
            spec = build_command(task.tool, binary, task.target, folder, stem, options, ports)
        except (OSError, ValueError) as exc:
            self.event("error", f"Plan inválido para {task.tool}: {exc}")
            return []
        job_id = self.project.start_job(run_id, task.tool, task.target, task.reason, list(spec.argv))
        stdout = spec.files.get("txt") if task.tool in {"enum4linux", "nikto"} else folder / f"{stem}.stdout.log"
        stderr = folder / f"{stem}.stderr.log"
        self.event("start", f"{task.tool}: {task.target}")
        exit_code: int | None = None
        status = "failed"
        try:
            with stdout.open("wb") as out_file, stderr.open("wb") as err_file:
                self._process = subprocess.Popen(list(spec.argv), stdout=out_file, stderr=err_file, cwd=folder, shell=False)
                deadline = time.monotonic() + 1800
                while True:
                    try:
                        exit_code = self._process.wait(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        if self.cancelled.is_set() or time.monotonic() > deadline:
                            self._process.terminate()
                            try:
                                self._process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                self._process.kill()
                                self._process.wait()
                            break
            status = "cancelled" if self.cancelled.is_set() else "complete" if exit_code == 0 else "failed"
        except OSError as exc:
            stderr.write_text(str(exc), encoding="utf-8")
        finally:
            self._process = None
        self.project.end_job(job_id, status, exit_code, stdout, stderr)
        for file in spec.files.values():
            if status == "complete" and not file.exists():
                file.touch()
            if file.exists():
                self.project.add_artifact(job_id, file)
        if stdout.exists() and stdout not in spec.files.values():
            self.project.add_artifact(job_id, stdout)
        if stderr.exists() and stderr.stat().st_size:
            self.project.add_artifact(job_id, stderr)
        observations: list[tuple[str, str, dict]] = []
        if status == "complete":
            try:
                observations = parse_tool(task.tool, spec.files, task.target)
                for kind, value, detail in observations:
                    self.project.add_observation(run_id, job_id, task.target, kind, value, detail)
            except (OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
                self.event("error", f"No se pudo interpretar {task.tool}: {exc}")
        self.event("done" if status == "complete" else "error", f"{task.tool}: {status} ({task.target})")
        return observations

    def run_plan(self, plan: Plan, resume_run_id: str | None = None) -> str:
        if resume_run_id:
            self.project.reopen_run(resume_run_id)
            run_id = resume_run_id
        else:
            run_id = self.project.new_run(plan.options)
        self.event("run", run_id)
        errors = 0
        fatal = False
        active_urls: dict[str, str] = {}
        try:
            for task in plan.tasks:
                if self.cancelled.is_set():
                    break
                original_target = task.target
                if resume_run_id and task.tool == "httpx":
                    prior_urls = [row["value"] for row in self.project.observations_for_run(run_id)
                                  if row["kind"] == "web_url" and row["target"] == original_target]
                    if prior_urls:
                        active_urls[original_target] = prior_urls[-1]
                if task.tool not in {"subfinder", "httpx", "nmap"} and original_target in active_urls:
                    task = PlannedTask(task.tool, active_urls[original_target], task.reason, task.conditional, task.outputs)
                completed = {(row["tool"], row["target"]) for row in self.project.jobs(run_id) if row["status"] == "complete"}
                if resume_run_id and (task.tool, task.target) in completed:
                    self.event("skip", f"Ya completado: {task.tool} · {task.target}")
                    if task.tool == "nmap":
                        prior = [(row["kind"], row["value"], json.loads(row["detail_json"]))
                                 for row in self.project.observations_for_run(run_id)
                                 if row["target"] == task.target]
                        errors += self._run_conditionals(run_id, task.target, prior, plan.options)
                    continue
                before = len(self.project.jobs(run_id))
                observations = self._run_task(run_id, task, plan.options)
                if task.tool == "httpx":
                    live = next((value for kind, value, _detail in observations if kind == "web_url"), None)
                    if live:
                        active_urls[original_target] = live
                jobs = self.project.jobs(run_id)
                if len(jobs) == before or jobs[-1]["status"] != "complete":
                    errors += 1
                if task.tool == "nmap" and observations and not self.cancelled.is_set():
                    errors += self._run_conditionals(run_id, task.target, observations, plan.options)
            if not self.cancelled.is_set():
                self._export_summary(run_id, plan.options.formats)
        except Exception:
            fatal = True
            raise
        finally:
            status = "cancelled" if self.cancelled.is_set() else "failed" if fatal else "partial" if errors else "complete"
            self.project.end_run(run_id, status)
            self.event("finished", status)
        return run_id

    def _export_summary(self, run_id: str, formats: list[str]) -> None:
        if not {"json", "csv", "html"} & set(formats):
            return
        folder = self.project.output_dir(run_id, "resumen")
        job_id = self.project.start_job(run_id, "resumen", "proyecto", "Exportación de observaciones", ["interno", "exportar"])
        try:
            paths = self.project.export_summary(run_id, formats)
            for path in paths:
                self.project.add_artifact(job_id, path)
            status = "complete"
        except OSError as exc:
            self.event("error", f"No se pudo exportar el resumen: {exc}")
            status = "failed"
        self.project.end_job(job_id, status, 0 if status == "complete" else 1, folder / "resumen.stdout.log", folder / "resumen.stderr.log")

    def _run_conditionals(self, run_id: str, host: str, observations: list[tuple[str, str, dict]], options: ScanOptions) -> int:
        ports = {int(detail["port"]): detail for kind, _, detail in observations if kind == "port" and "port" in detail}
        tasks: list[PlannedTask] = []
        if options.conditional.get("enum4linux") == "auto" and any(
            p in {139, 445} or "smb" in detail.get("service", "").lower() for p, detail in ports.items()
        ):
            tasks.append(PlannedTask("enum4linux", host, "SMB detectado", True))
        if options.conditional.get("testssl") == "auto":
            for port in sorted(p for p, detail in ports.items() if p in {443, 465, 636, 8443, 9443, 993, 995}
                               or "ssl" in detail.get("service", "").lower() or "https" in detail.get("service", "").lower()):
                target = f"https://{host_in_url(host)}" + (f":{port}" if port != 443 else "")
                tasks.append(PlannedTask("testssl", target, f"TLS detectado en {port}", True))
        if options.conditional.get("whatweb") == "auto":
            for port in sorted(p for p, detail in ports.items() if p in {80, 443, 8000, 8080, 8443, 9443}
                               or "http" in detail.get("service", "").lower()):
                service = ports[port].get("service", "").lower()
                scheme = "https" if port in {443, 8443, 9443} or "https" in service or "ssl" in service else "http"
                suffix = "" if (scheme == "http" and port == 80) or (scheme == "https" and port == 443) else f":{port}"
                target = f"{scheme}://{host_in_url(host)}{suffix}"
                tasks.append(PlannedTask("whatweb", target, f"HTTP detectado en {port}", True))
        errors = 0
        completed = {(row["tool"], row["target"]) for row in self.project.jobs(run_id) if row["status"] == "complete"}
        for task in tasks:
            if self.cancelled.is_set():
                break
            if (task.tool, task.target) in completed:
                continue
            before = len(self.project.jobs(run_id))
            self._run_task(run_id, task, options)
            jobs = self.project.jobs(run_id)
            if len(jobs) == before or jobs[-1]["status"] != "complete":
                errors += 1
        return errors

    def run_suggestion(self, suggestion: Suggestion, base_options: ScanOptions) -> str:
        options = ScanOptions.from_dict(base_options.to_dict())
        options.targets = [suggestion.target]
        run_id = self.project.new_run(options, kind="suggestion")
        self.event("run", run_id)
        task = PlannedTask(suggestion.tool, suggestion.target, suggestion.reason)
        status = "failed"
        try:
            self._run_task(run_id, task, options, list(suggestion.ports))
            status = self.project.jobs(run_id)[-1]["status"] if self.project.jobs(run_id) else "failed"
            self._export_summary(run_id, options.formats)
        finally:
            self.project.end_run(run_id, status)
            self.event("finished", status)
        return run_id
