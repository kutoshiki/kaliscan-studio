"""Adaptadores CLI. Nunca se construye un comando de shell como texto."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from .models import ScanOptions
from .planner import PlanError


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    files: dict[str, Path]


def web_url(target: str) -> str:
    return target if "://" in target else "https://" + target


def web_host(target: str) -> str:
    return urlsplit(web_url(target)).hostname or target


def host_in_url(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def build_command(
    tool: str,
    binary: str,
    target: str,
    folder: Path,
    stem: str,
    options: ScanOptions,
    ports: list[int] | None = None,
) -> CommandSpec:
    base = folder / stem
    if tool in {"nmap", "nmap_versions", "nmap_vuln"}:
        xml = base.with_suffix(".xml")
        normal = base.with_suffix(".txt")
        args = [binary, "-sT", f"-T{options.timing}"]
        if ipaddress.ip_address(target).version == 6:
            args.append("-6")
        if tool in {"nmap_versions", "nmap_vuln"}:
            if not ports:
                raise PlanError("No hay puertos abiertos para detectar versiones.")
            args.extend(["-sV", "-p", ",".join(map(str, sorted(set(ports))))])
            if tool == "nmap_vuln":
                args.extend(["--script", "vuln"])
        else:
            if options.port_mode == "top100":
                args.extend(["--top-ports", "100"])
            elif options.port_mode == "all":
                args.append("-p-")
            else:
                args.extend(["-p", options.custom_ports])
            if options.version_detection:
                args.append("-sV")
            scripts = list(dict.fromkeys(options.script_categories + options.script_names))
            if scripts:
                args.extend(["--script", ",".join(scripts)])
        args.extend(["-oX", str(xml), "-oN", str(normal), target])
        return CommandSpec(tuple(args), {"xml": xml, "txt": normal})
    if tool == "subfinder":
        output = base.with_suffix(".txt")
        return CommandSpec((binary, "-d", web_host(target), "-o", str(output)), {"txt": output})
    if tool == "httpx":
        output = base.with_suffix(".jsonl")
        return CommandSpec((binary, "-u", target, "-j", "-rl", "20", "-o", str(output)), {"jsonl": output})
    if tool == "whatweb":
        output = base.with_suffix(".json")
        return CommandSpec((binary, "--color=never", f"--log-json={output}", web_url(target)), {"json": output})
    if tool == "ffuf":
        output = base.with_suffix(".json")
        url = web_url(target).rstrip("/") + "/FUZZ"
        return CommandSpec((binary, "-w", options.wordlist, "-u", url, "-rate", "20", "-t", "10", "-of", "json", "-o", str(output)), {"json": output})
    if tool == "nuclei":
        output = base.with_suffix(".jsonl")
        return CommandSpec((binary, "-u", web_url(target), "-j", "-rl", "20", "-etags", "dos,exploit,bruteforce", "-o", str(output)), {"jsonl": output})
    if tool == "testssl":
        output = base.with_suffix(".json")
        parsed = urlsplit(web_url(target))
        host = parsed.hostname or target
        port = parsed.port if parsed.scheme == "https" else 443
        uri = f"{host_in_url(host)}:{port}" if port and port != 443 else host_in_url(host)
        return CommandSpec((binary, "--jsonfile-pretty", str(output), uri), {"json": output})
    if tool == "enum4linux":
        return CommandSpec((binary, "-A", target), {"txt": base.with_suffix(".txt")})
    if tool == "nikto":
        return CommandSpec((binary, "-h", web_url(target)), {"txt": base.with_suffix(".txt")})
    raise PlanError(f"Herramienta no implementada: {tool}")
