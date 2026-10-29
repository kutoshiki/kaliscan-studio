"""Actualización de la aplicación desde el origen registrado por pipx."""

from __future__ import annotations

import shutil
import subprocess
import sys


def reinstall_app(timeout: int = 1800) -> tuple[bool, str]:
    if not sys.platform.startswith("linux"):
        return False, "La actualización desde la interfaz está disponible en Kali Linux."
    pipx = shutil.which("pipx")
    if not pipx:
        return False, "No se encontró pipx. Instálalo con: sudo apt install pipx"
    try:
        result = subprocess.run(
            [pipx, "reinstall", "kaliscan-studio"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode == 0, output[-5000:] or f"pipx terminó con código {result.returncode}."
