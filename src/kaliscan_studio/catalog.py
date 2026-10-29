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
    try:
        installed_result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package],
            capture_output=True, text=True, timeout=3, check=False,
        )
        installed = installed_result.stdout.strip() if installed_result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        installed = ""
    try:
        policy_result = subprocess.run(
            ["apt-cache", "policy", package], capture_output=True, text=True,
            timeout=3, check=False, env={**os.environ, "LC_ALL": "C"},
        )
        policy = policy_result.stdout if policy_result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        policy = ""
    match = re.search(r"^\s*Candidate:\s*(\S+)", policy, re.MULTILINE)
    candidate = match.group(1) if match and match.group(1) != "(none)" else None
    update = False
    if installed and candidate:
        try:
            update = subprocess.run(
                ["dpkg", "--compare-versions", installed, "lt", candidate],
                capture_output=True, check=False,
            ).returncode == 0
        except OSError:
            pass
    return installed or None, candidate, update


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _version_label(output: str, package_version: str | None) -> str:
    lines = [_ANSI.sub("", line).strip() for line in output.replace("\r", "\n").splitlines()]
    lines = [re.sub(r"^\[(?:INF|INFO|WRN)\]\s*", "", line) for line in lines if line]
    version_line = next((line for line in lines if re.search(r"version", line, re.I) and re.search(r"\d", line)), None)
    version_line = version_line or next((line for line in lines if re.search(r"\d+\.\d+", line)), None)
    return (version_line[:160] if version_line else package_version) or "Versión no disponible"


def inspect_tool(key: str, override: str | None = None) -> ToolStatus:
    tool = TOOLS[key]
    path = override if override and os.path.isfile(override) and os.access(override, os.X_OK) else next(
        (p for binary in tool.binaries if (p := shutil.which(binary))), None
    )
    installed, candidate, update = _package_versions(tool.package)
    version = _version_label(_output([path, *tool.version_args]), installed) if path else ""
    return ToolStatus(key, path, version, installed, candidate, update)


def inspect_all(overrides: dict[str, str] | None = None) -> dict[str, ToolStatus]:
    overrides = overrides or {}
    return {key: inspect_tool(key, overrides.get(key)) for key in TOOLS}
