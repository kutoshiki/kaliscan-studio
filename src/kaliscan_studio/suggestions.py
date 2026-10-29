"""Recomendaciones basadas en evidencia; nunca ejecutan por sí solas."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .commands import host_in_url
from .projects import Project


@dataclass(frozen=True)
class Suggestion:
    tool: str
    target: str
    label: str
    reason: str
    ports: tuple[int, ...] = ()


def _service_url(host: str, port: int, secure: bool = False) -> str:
    scheme = "https" if secure or port in {443, 8443, 9443} else "http"
    suffix = "" if (scheme == "http" and port == 80) or (scheme == "https" and port == 443) else f":{port}"
    return f"{scheme}://{host_in_url(host)}{suffix}"


def propose(project: Project) -> list[Suggestion]:
    jobs = project.jobs()
    completed = {(row["tool"], row["target"]) for row in jobs if row["status"] == "complete"}
    dismissed = set(project.meta.get("dismissed_suggestions", []))
    latest_maps: dict[str, tuple[int, dict]] = {}
    latest_httpx: dict[str, str] = {}
    for index, job in enumerate(jobs):
        if job["status"] != "complete":
            continue
        if job["tool"] in {"nmap", "nmap_versions"}:
            latest_maps[job["target"]] = (index, job)
        elif job["tool"] == "httpx":
            latest_httpx[job["target"]] = job["id"]
    map_job_ids = {job["id"] for _index, job in latest_maps.values()}
    httpx_job_ids = set(latest_httpx.values())
    by_target: dict[str, dict[int, dict]] = {}
    web_urls: set[str] = set()
    for row in project.observations():
        if row["kind"] == "web_url" and row["job_id"] in httpx_job_ids:
            web_urls.add(row["value"])
        if row["kind"] != "port" or row["job_id"] not in map_job_ids:
            continue
        detail = json.loads(row["detail_json"])
        port = detail.get("port")
        if isinstance(port, int):
            by_target.setdefault(row["target"], {})[port] = detail
    suggestions: list[Suggestion] = []
    for target, ports in by_target.items():
        map_index, map_job = latest_maps[target]
        later_jobs = [job for job in jobs[map_index:] if job["target"] == target and job["status"] == "complete"]
        has_version_scan = map_job["tool"] == "nmap_versions" or "-sV" in json.loads(map_job["command_json"])
        has_version_scan = has_version_scan or any(job["tool"] == "nmap_versions" for job in later_jobs)
        if not has_version_scan:
            suggestions.append(Suggestion(
                "nmap_versions", target, "Identificar servicios y versiones",
                f"Se encontraron {len(ports)} puertos abiertos sin detección de versiones.", tuple(sorted(ports))
            ))
        has_vuln_scan = "vuln" in ",".join(json.loads(map_job["command_json"])) or any(
            job["tool"] == "nmap_vuln" for job in later_jobs
        )
        if has_version_scan and not has_vuln_scan:
            suggestions.append(Suggestion(
                "nmap_vuln", target, "Ejecutar scripts NSE vuln",
                "Ya se identificaron servicios. La categoría vuln incluye comprobaciones que pueden ser intrusivas o explotar fallos; revisa los scripts antes de ejecutarla.",
                tuple(sorted(ports)),
            ))
        if any(port in {139, 445} or "smb" in info.get("service", "") for port, info in ports.items()):
            if ("enum4linux", target) not in completed:
                suggestions.append(Suggestion("enum4linux", target, "Enumerar SMB", "Se detectó un puerto o servicio SMB."))
        for port, info in ports.items():
            name = info.get("service", "").lower()
            is_web = port in {80, 443, 8000, 8080, 8443, 9443} or "http" in name
            is_tls = port in {443, 465, 636, 8443, 9443, 993, 995} or "https" in name or "ssl" in name
            url = _service_url(target, port, is_tls)
            if is_web and ("whatweb", url) not in completed:
                suggestions.append(Suggestion("whatweb", url, "Identificar tecnologías web", f"Hay HTTP en el puerto {port}."))
            if is_web and ("nuclei", url) not in completed:
                suggestions.append(Suggestion("nuclei", url, "Comprobar la web con Nuclei", f"Hay HTTP en el puerto {port}."))
            if is_tls and ("testssl", url) not in completed:
                suggestions.append(Suggestion("testssl", url, "Revisar TLS", f"Hay TLS en el puerto {port}."))
    for url in web_urls:
        for tool, label in (("whatweb", "Identificar tecnologías web"), ("nuclei", "Comprobar la web con Nuclei")):
            if (tool, url) not in completed:
                suggestions.append(Suggestion(tool, url, label, "El sitio respondió a la comprobación HTTP."))
    unique: dict[tuple[str, str], Suggestion] = {}
    for item in suggestions:
        key = (item.tool, item.target)
        if f"{item.tool}|{item.target}" not in dismissed:
            unique[key] = item
    return list(unique.values())
