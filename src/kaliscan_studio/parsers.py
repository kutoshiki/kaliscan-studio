"""Extrae observaciones sin alterar la evidencia original."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_nmap(path: Path, target: str) -> list[tuple[str, str, dict]]:
    root = ET.parse(path).getroot()
    observations: list[tuple[str, str, dict]] = []
    for host in root.findall("host"):
        addresses = [node.get("addr", "") for node in host.findall("address")]
        host_target = addresses[0] if addresses else target
        for port in host.findall("./ports/port"):
            state = port.find("state")
            if state is None or state.get("state") != "open":
                continue
            number = int(port.get("portid", "0"))
            protocol = port.get("protocol", "tcp")
            service = port.find("service")
            service_name = service.get("name", "") if service is not None else ""
            detail = {"host": host_target, "port": number, "protocol": protocol, "service": service_name}
            observations.append(("port", f"{number}/{protocol}", detail))
            if service_name:
                observations.append(("service", service_name, detail))
            if service is not None:
                product = service.get("product", "")
                version = service.get("version", "")
                if product or version:
                    observations.append(("version", " ".join(filter(None, [product, version])), detail))
            for script in port.findall("script"):
                observations.append(("script", script.get("id", ""), {**detail, "output": script.get("output", "")}))
        for script in host.findall("./hostscript/script"):
            observations.append(("script", script.get("id", ""), {"host": host_target, "output": script.get("output", "")}))
    return observations


def parse_jsonl(path: Path, tool: str) -> list[tuple[str, str, dict]]:
    observations: list[tuple[str, str, dict]] = []
    with path.open(encoding="utf-8", errors="replace") as file:
        for line in file:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if tool == "httpx":
                url = item.get("url") or item.get("input")
                if url:
                    observations.append(("web_url", url, {"status_code": item.get("status_code"), "title": item.get("title")}))
                for tech in item.get("tech", []) or []:
                    observations.append(("technology", str(tech), {"url": url}))
            elif tool == "nuclei":
                template = item.get("template-id") or item.get("templateID") or "comprobación"
                info = item.get("info") or {}
                observations.append(("finding", str(template), {
                    "severity": info.get("severity", "unknown"),
                    "matched_at": item.get("matched-at") or item.get("matched"),
                    "name": info.get("name", ""),
                }))
    return observations


def parse_tool(tool: str, files: dict[str, Path], target: str) -> list[tuple[str, str, dict]]:
    if tool in {"nmap", "nmap_versions", "nmap_vuln"} and files.get("xml", Path("missing")).is_file():
        return parse_nmap(files["xml"], target)
    if tool in {"httpx", "nuclei"} and files.get("jsonl", Path("missing")).is_file():
        return parse_jsonl(files["jsonl"], tool)
    if tool == "subfinder" and files.get("txt", Path("missing")).is_file():
        return [("subdomain", line.strip(), {}) for line in files["txt"].read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if tool == "ffuf" and files.get("json", Path("missing")).is_file():
        try:
            data = json.loads(files["json"].read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return []
        return [("web_path", item.get("url", ""), {"status": item.get("status")}) for item in data.get("results", []) if item.get("url")]
    return []
