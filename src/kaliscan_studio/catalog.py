"""Catálogo de herramientas CLI y descubrimiento del entorno Kali."""

from __future__ import annotations

import re
import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    package: str
    binaries: tuple[str, ...]
    version_args: tuple[str, ...] = ("--version",)
    description: str = ""


TOOLS: dict[str, Tool] = {
    "nmap": Tool("nmap", "Nmap", "nmap", ("nmap",), ("--version",), "Puertos, servicios y scripts NSE."),
    "subfinder": Tool("subfinder", "Subfinder", "subfinder", ("subfinder",), ("-version",), "Subdominios por fuentes pasivas."),
    "httpx": Tool("httpx", "httpx-toolkit", "httpx-toolkit", ("httpx-toolkit",), ("-version",), "Comprueba servicios HTTP y HTTPS."),
    "whatweb": Tool("whatweb", "WhatWeb", "whatweb", ("whatweb",), ("--version",), "Identifica tecnologías web."),
    "ffuf": Tool("ffuf", "ffuf", "ffuf", ("ffuf",), ("-V",), "Descubre rutas web con una lista de palabras."),
    "nuclei": Tool("nuclei", "Nuclei", "nuclei", ("nuclei",), ("-version",), "Comprobaciones mediante plantillas."),
    "testssl": Tool("testssl", "testssl.sh", "testssl.sh", ("testssl", "testssl.sh"), ("--version",), "Configuración TLS."),
    "enum4linux": Tool("enum4linux", "enum4linux-ng", "enum4linux-ng", ("enum4linux-ng",), ("-h",), "Enumeración de SMB y Samba."),
    "nikto": Tool("nikto", "Nikto", "nikto", ("nikto",), ("-Version",), "Comprobaciones de servidor web."),
}


@dataclass(frozen=True)
class ToolStatus:
    key: str
    path: str | None
    version: str
    package_version: str | None
    candidate_version: str | None
    update_available: bool

    @property
    def installed(self) -> bool:
        return self.path is not None


def _output(args: list[str], timeout: int = 3) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return (proc.stdout + "\n" + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _package_versions(package: str) -> tuple[str | None, str | None, bool]:
    if not shutil.which("dpkg-query") or not shutil.which("apt-cache"):
        return None, None, False
    installed = _output(["dpkg-query", "-W", "-f=${Version}", package])
    if not installed or "no packages found" in installed.lower():
        installed = ""
    policy = _output(["apt-cache", "policy", package])
    match = re.search(r"^\s*Candidate:\s*(\S+)", policy, re.MULTILINE)
    candidate = match.group(1) if match and match.group(1) != "(none)" else None
    update = False
    if installed and candidate:
        update = subprocess.run(
            ["dpkg", "--compare-versions", installed, "lt", candidate],
            capture_output=True,
            check=False,
        ).returncode == 0
    return installed or None, candidate, update


def inspect_tool(key: str, override: str | None = None) -> ToolStatus:
    tool = TOOLS[key]
    path = override if override and os.path.isfile(override) and os.access(override, os.X_OK) else next(
        (p for binary in tool.binaries if (p := shutil.which(binary))), None
    )
    version = ""
    if path:
        version = _output([path, *tool.version_args]).splitlines()[0:1]
        version = version[0][:160] if version else "Versión no disponible"
    installed, candidate, update = _package_versions(tool.package)
    return ToolStatus(key, path, version, installed, candidate, update)


def inspect_all(overrides: dict[str, str] | None = None) -> dict[str, ToolStatus]:
    overrides = overrides or {}
    return {key: inspect_tool(key, overrides.get(key)) for key in TOOLS}
