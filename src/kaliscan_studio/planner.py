"""Validación de opciones y construcción determinista del plan."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

from .models import Plan, PlannedTask, ScanOptions


class PlanError(ValueError):
    pass


WEB_TOOLS = {"subfinder", "httpx", "whatweb", "ffuf", "nuclei", "testssl", "nikto"}
CONDITIONAL_TOOLS = {"enum4linux", "testssl", "whatweb"}
SCRIPT_CATEGORIES = {"default", "safe", "discovery", "vuln"}
SCRIPT_NAME = re.compile(r"^[a-zA-Z0-9_.-]+$")
HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$")
PORT_LIST = re.compile(r"^\d{1,5}(?:-\d{1,5})?(?:,\d{1,5}(?:-\d{1,5})?)*$")


def normalize_targets(raw: str | list[str], mode: str) -> list[str]:
    parts = re.split(r"[\s,;]+", raw if isinstance(raw, str) else "\n".join(raw))
    targets: list[str] = []
    for part in parts:
        if not part:
            continue
        if mode == "ip":
            try:
                normalized = str(ipaddress.ip_address(part))
            except ValueError as exc:
                raise PlanError(f"IP inválida: {part}") from exc
        elif mode == "web":
            parsed = urlsplit(part if "://" in part else "https://" + part)
            host = parsed.hostname or ""
            if parsed.scheme not in {"http", "https"} or not HOSTNAME.fullmatch(host) or parsed.username or parsed.password:
                raise PlanError(f"Dominio o URL inválida: {part}")
            if parsed.query or parsed.fragment:
                raise PlanError(f"Usa una URL base sin parámetros ni fragmentos: {part}")
            try:
                port = parsed.port
            except ValueError as exc:
                raise PlanError(f"Puerto inválido en: {part}") from exc
            if port is not None and not 1 <= port <= 65535:
                raise PlanError(f"Puerto inválido en: {part}")
            normalized = part.rstrip("/")
        else:
            raise PlanError("El tipo de objetivo debe ser IP o web.")
        if normalized not in targets:
            targets.append(normalized)
    if not targets:
        raise PlanError("Agrega al menos un objetivo.")
    if len(targets) > 200:
        raise PlanError("Máximo 200 objetivos por ejecución.")
    return targets


def _validate_ports(text: str) -> str:
    value = text.replace(" ", "")
    if not PORT_LIST.fullmatch(value):
        raise PlanError("Usa puertos como 22,80,443 o rangos como 8000-8100.")
    for item in value.split(","):
        low, _, high = item.partition("-")
        if not 1 <= int(low) <= 65535 or high and (not 1 <= int(high) <= 65535 or int(low) > int(high)):
            raise PlanError(f"Puerto fuera de rango: {item}")
    return value


def validate_options(options: ScanOptions) -> ScanOptions:
    options.targets = normalize_targets(options.targets, options.target_mode)
    if options.exclusions:
        options.exclusions = normalize_targets(options.exclusions, options.target_mode)
        if options.target_mode == "ip":
            excluded = set(options.exclusions)
            options.targets = [target for target in options.targets if target not in excluded]
        else:
            excluded_hosts = {urlsplit(item if "://" in item else "https://" + item).hostname for item in options.exclusions}
            options.targets = [target for target in options.targets if urlsplit(target if "://" in target else "https://" + target).hostname not in excluded_hosts]
        if not options.targets:
            raise PlanError("Todas las entradas quedaron excluidas. Revisa el alcance.")
    if options.timing not in {1, 2, 3, 4, 5}:
        raise PlanError("La velocidad Nmap debe estar entre T1 y T5.")
    if options.port_mode not in {"top100", "all", "custom"}:
        raise PlanError("Selecciona un solo alcance de puertos.")
    if options.port_mode == "custom":
        options.custom_ports = _validate_ports(options.custom_ports)
    if not set(options.script_categories) <= SCRIPT_CATEGORIES:
        raise PlanError("Hay una categoría NSE desconocida.")
    if any(not SCRIPT_NAME.fullmatch(name) for name in options.script_names):
        raise PlanError("Un nombre de script NSE contiene caracteres inválidos.")
    if not set(options.web_tools) <= WEB_TOOLS:
        raise PlanError("Hay una herramienta web desconocida.")
    if not set(options.conditional) <= CONDITIONAL_TOOLS or any(
        mode not in {"off", "ask", "auto"} for mode in options.conditional.values()
    ):
        raise PlanError("Hay una condición desconocida.")
    if options.target_mode == "web" and not options.web_tools:
        raise PlanError("Selecciona al menos una herramienta web.")
    if options.target_mode == "web" and (options.script_categories or options.script_names):
        raise PlanError("Los scripts NSE están disponibles en el plan de IP.")
    if options.target_mode == "web" and any(mode != "off" for mode in options.conditional.values()):
        raise PlanError("Los pasos condicionales de puertos requieren un objetivo IP.")
    if options.target_mode == "ip" and options.web_tools:
        raise PlanError("Las pruebas web directas requieren el tipo de objetivo web.")
    if not set(options.formats) <= {"txt", "xml", "json", "jsonl", "csv", "html"}:
        raise PlanError("Formato de salida desconocido.")
    if "ffuf" in options.web_tools and not Path(options.wordlist).is_file():
        raise PlanError("La lista de palabras de ffuf no existe. Selecciona un archivo válido.")
    return options


def make_plan(options: ScanOptions) -> Plan:
    options = validate_options(options)
    tasks: list[PlannedTask] = []
    possible: list[PlannedTask] = []
    warnings: list[str] = []
    if options.target_mode == "ip":
        for target in options.targets:
            tasks.append(PlannedTask("nmap", target, "Mapeo inicial de puertos", outputs=("xml", "txt")))
            for tool in CONDITIONAL_TOOLS:
                if options.conditional.get(tool, "off") in {"ask", "auto"}:
                    output = ("json",) if tool in {"testssl", "whatweb"} else ("txt",)
                    possible.append(PlannedTask(tool, target, "Solo si se detecta el servicio correspondiente", True, output))
        if "vuln" in options.script_categories:
            warnings.append("NSE vuln puede incluir scripts intrusivos o de explotación. Revisa la selección antes de iniciar.")
        if options.port_mode == "all" and options.timing >= 4:
            warnings.append("Todos los puertos con T4/T5 puede generar mucha carga o respuestas incompletas.")
    else:
        for target in options.targets:
            selected = list(dict.fromkeys(options.web_tools))
            if any(tool not in {"subfinder", "httpx"} for tool in selected) and "httpx" not in selected:
                selected.insert(0, "httpx")
                warnings.append("Se añadió httpx-toolkit para identificar la URL activa antes de las pruebas web.")
            selected.sort(key=lambda key: {"subfinder": 0, "httpx": 1}.get(key, 2))
            for tool in selected:
                if tool == "subfinder":
                    host = urlsplit(target if "://" in target else "https://" + target).hostname or target
                    tasks.append(PlannedTask(tool, host, "Descubrimiento pasivo de subdominios"))
                else:
                    output = ("jsonl",) if tool in {"httpx", "nuclei"} else (("json",) if tool in {"ffuf", "whatweb", "testssl"} else ("txt",))
                    tasks.append(PlannedTask(tool, target, "Prueba web seleccionada", outputs=output))
        if "ffuf" in options.web_tools:
            warnings.append("ffuf puede generar numerosas peticiones; se ejecutará una tarea a la vez.")
    return Plan(options, tasks, possible, warnings)
