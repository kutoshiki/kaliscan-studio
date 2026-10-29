"""Componentes de la interfaz de escritorio."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from .catalog import TOOLS, ToolStatus, inspect_all
from .commands import build_command
from .models import Plan, ScanOptions
from .planner import PlanError, make_plan
from .projects import Project


class InventoryWorker(QThread):
    result = Signal(object)

    def __init__(self, overrides: dict[str, str]):
        super().__init__()
        self.overrides = overrides

    def run(self) -> None:
        self.result.emit(inspect_all(self.overrides))


class PackageWorker(QThread):
    result = Signal(bool, str)

    def __init__(self, packages: list[str], update_only: bool):
        super().__init__()
        self.packages = packages
        self.update_only = update_only

    def run(self) -> None:
        if not sys.platform.startswith("linux"):
            self.result.emit(False, "La instalación de paquetes está disponible en Kali Linux.")
            return
        pkexec = shutil.which("pkexec")
        apt = shutil.which("apt-get")
        if not pkexec or not apt:
            self.result.emit(False, "Falta pkexec o apt-get. Instala los paquetes desde una terminal de Kali.")
            return
        try:
            refresh = subprocess.run([pkexec, apt, "update"], capture_output=True, text=True, timeout=600, check=False)
            if refresh.returncode:
                self.result.emit(False, (refresh.stderr or refresh.stdout)[-1500:])
                return
            if not self.packages:
                self.result.emit(True, "Índice de paquetes actualizado.")
                return
            argv = [pkexec, apt, "install", "-y"]
            if self.update_only:
                argv.append("--only-upgrade")
            argv.extend(self.packages)
            install = subprocess.run(argv, capture_output=True, text=True, timeout=1800, check=False)
            self.result.emit(install.returncode == 0, (install.stderr or install.stdout)[-1500:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.result.emit(False, str(exc))


class EnvironmentDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.statuses: dict[str, ToolStatus] = {}
        self.inventory_worker: InventoryWorker | None = None
        self.package_worker: PackageWorker | None = None
        self.setWindowTitle("Herramientas de Kali")
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Herramientas detectadas en Kali. Puedes elegir una fila para indicar una ruta manual."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Herramienta", "Ruta", "Versión", "Paquete apt", "Estado"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        self.message = QLabel("Comprobando herramientas…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Comprobar ahora")
        self.online_button = QPushButton("Buscar actualizaciones en línea")
        self.path_button = QPushButton("Elegir ruta manual")
        self.install_button = QPushButton("Instalar faltantes")
        self.update_button = QPushButton("Actualizar disponibles")
        self.close_button = QPushButton("Continuar")
        for button in (self.refresh_button, self.online_button, self.path_button, self.install_button, self.update_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh_button.clicked.connect(self.refresh)
        self.online_button.clicked.connect(self.refresh_apt_index)
        self.path_button.clicked.connect(self.choose_path)
        self.install_button.clicked.connect(lambda: self.change_packages(False))
        self.update_button.clicked.connect(lambda: self.change_packages(True))
        self.close_button.clicked.connect(self.accept)
        self.refresh()

    def _overrides(self) -> dict[str, str]:
        return {key: str(self.settings.value(f"tool/{key}/path", "")) for key in TOOLS}

    def refresh(self) -> None:
        if self.inventory_worker and self.inventory_worker.isRunning():
            return
        self.message.setText("Comprobando ejecutables, rutas y versiones…")
        self.refresh_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.inventory_worker = InventoryWorker(self._overrides())
        self.inventory_worker.result.connect(self.show_inventory)
        self.inventory_worker.finished.connect(lambda: self.close_button.setEnabled(True))
        self.inventory_worker.start()

    def show_inventory(self, statuses: dict[str, ToolStatus]) -> None:
        self.statuses = statuses
        self.table.setRowCount(0)
        for key, status in statuses.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            state = "Disponible" if status.installed else "Falta"
            if status.update_available:
                state = "Actualización disponible"
            for col, value in enumerate((TOOLS[key].label, status.path or "—", status.version or "—", status.package_version or "—", state)):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.message.setText("Selecciona una fila para usar una ruta manual. Las actualizaciones se consultan con el índice local de apt.")
        self.refresh_button.setEnabled(True)

    def choose_path(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Ruta", "Selecciona primero una herramienta.")
            return
        key = list(TOOLS)[row]
        path, _ = QFileDialog.getOpenFileName(self, f"Ejecutable de {TOOLS[key].label}")
        if path:
            self.settings.setValue(f"tool/{key}/path", path)
            self.refresh()

    def change_packages(self, update_only: bool) -> None:
        if self.package_worker and self.package_worker.isRunning():
            return
        keys = [key for key, status in self.statuses.items() if status.update_available] if update_only else [
            key for key, status in self.statuses.items() if not status.installed
        ]
        packages = sorted({TOOLS[key].package for key in keys})
        if not packages:
            QMessageBox.information(self, "Herramientas", "No hay paquetes en esta categoría según la comprobación actual.")
            return
        action = "actualizar" if update_only else "instalar"
        answer = QMessageBox.question(self, "Confirmar paquetes", f"Se van a {action} estos paquetes de Kali:\n\n" + "\n".join(packages))
        if answer != QMessageBox.Yes:
            return
        self.message.setText(f"Usando apt para {action} paquetes. Kali puede pedir la contraseña de administración…")
        self.install_button.setEnabled(False)
        self.update_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.package_worker = PackageWorker(packages, update_only)
        self.package_worker.result.connect(self.packages_done)
        self.package_worker.finished.connect(lambda: self.close_button.setEnabled(True))
        self.package_worker.start()

    def refresh_apt_index(self) -> None:
        if self.package_worker and self.package_worker.isRunning():
            return
        self.message.setText("Actualizando el índice de paquetes de Kali…")
        self.online_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.package_worker = PackageWorker([], False)
        self.package_worker.result.connect(self.packages_done)
        self.package_worker.finished.connect(lambda: self.close_button.setEnabled(True))
        self.package_worker.start()

    def packages_done(self, success: bool, output: str) -> None:
        self.install_button.setEnabled(True)
        self.update_button.setEnabled(True)
        self.online_button.setEnabled(True)
        QMessageBox.information(self, "Paquetes", ("Operación completada." if success else "No se completó la operación.") + "\n\n" + output)
        self.refresh()

    def closeEvent(self, event) -> None:
        if (self.inventory_worker and self.inventory_worker.isRunning()) or (self.package_worker and self.package_worker.isRunning()):
            event.ignore()
        else:
            event.accept()


def info_button(parent: QWidget, title: str, detail: str) -> QToolButton:
    button = QToolButton(parent)
    button.setText("ⓘ")
    button.setToolTip("Ver explicación")
    button.clicked.connect(lambda: QMessageBox.information(parent, title, detail))
    return button


class PlanForm(QWidget):
    scan_requested = Signal(object)
    environment_requested = Signal()

    def __init__(self, project: Project, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.settings = settings
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel("<h2>Nuevo escaneo</h2>"))
        layout.addWidget(QLabel("Elige los objetivos y las comprobaciones. Podrás revisar el plan completo antes de ejecutarlo."))

        target_group = QGroupBox("1. Objetivos")
        target_layout = QVBoxLayout(target_group)
        self.target_mode = QComboBox()
        self.target_mode.addItem("Direcciones IP", "ip")
        self.target_mode.addItem("Dominios o sitios web", "web")
        target_layout.addWidget(self.target_mode)
        self.targets = QTextEdit()
        self.targets.setPlaceholderText("Una IP o dominio por línea. También puedes separarlos con comas.")
        self.targets.setFixedHeight(90)
        target_layout.addWidget(self.targets)
        self.exclusions = QLineEdit()
        self.exclusions.setPlaceholderText("Excluir IP o dominios de esta ejecución (opcional; separados por comas)")
        target_layout.addWidget(self.exclusions)
        layout.addWidget(target_group)

        self.ip_group = QGroupBox("2. Opciones de Nmap para IP")
        ip = QVBoxLayout(self.ip_group)
        ip.addWidget(QLabel("Alcance de puertos (solo una opción):"))
        self.port_group = QButtonGroup(self)
        self.top100 = QRadioButton("100 puertos más comunes")
        self.all_ports = QRadioButton("Todos los puertos TCP")
        self.custom = QRadioButton("Puertos personalizados")
        for index, radio in enumerate((self.top100, self.all_ports, self.custom)):
            self.port_group.addButton(radio, index)
            row = QHBoxLayout()
            row.addWidget(radio)
            button = QToolButton(self)
            button.setText("ⓘ")
            button.setToolTip("Ver explicación y bloqueos")
            button.clicked.connect(lambda _checked=False, selected=radio: self.show_port_info(selected))
            row.addWidget(button)
            row.addStretch()
            ip.addLayout(row)
        self.top100.setChecked(True)
        self.port_blocker = QLabel("Las otras opciones de puertos quedan excluidas por «100 puertos más comunes».")
        self.port_blocker.setWordWrap(True)
        ip.addWidget(self.port_blocker)
        self.port_group.buttonClicked.connect(self.update_port_blocker)
        self.custom_ports = QLineEdit()
        self.custom_ports.setPlaceholderText("Ejemplo: 22,80,443,8000-8100")
        self.custom_ports.setEnabled(False)
        self.custom.toggled.connect(self.custom_ports.setEnabled)
        ip.addWidget(self.custom_ports)
        speed = QHBoxLayout()
        speed.addWidget(QLabel("Velocidad Nmap:"))
        self.timing = QComboBox()
        for t in range(1, 6):
            self.timing.addItem(f"T{t}" + (" · normal" if t == 3 else ""), t)
        self.timing.setCurrentIndex(2)
        speed.addWidget(self.timing)
        speed.addWidget(info_button(self, "Velocidad Nmap", "Solo se puede elegir una velocidad T1–T5. T3 es el valor normal; T5 puede perder respuestas en algunas redes."))
        speed.addStretch()
        ip.addLayout(speed)
        self.version = QCheckBox("Identificar servicios y versiones durante el mapeo")
        ip.addWidget(self.version)
        ip.addWidget(QLabel("Scripts NSE:"))
        self.script_checks: dict[str, QCheckBox] = {}
        for name, description in (
            ("default", "Scripts predeterminados de Nmap."),
            ("safe", "Scripts clasificados como seguros por Nmap."),
            ("discovery", "Scripts de descubrimiento."),
            ("vuln", "Categoría completa --script vuln. Puede incluir scripts intrusivos o de explotación; revisa el plan."),
        ):
            check = QCheckBox(name)
            self.script_checks[name] = check
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(info_button(self, f"NSE {name}", description))
            row.addStretch()
            ip.addLayout(row)
        self.script_names = QLineEdit()
        self.script_names.setPlaceholderText("Scripts individuales, separados por comas (opcional)")
        ip.addWidget(self.script_names)
        layout.addWidget(self.ip_group)

        self.conditional_group = QGroupBox("3. Pasos condicionales después de Nmap")
        conditional = QFormLayout(self.conditional_group)
        self.conditionals: dict[str, QComboBox] = {}
        for key, label in (("enum4linux", "SMB → enum4linux-ng"), ("testssl", "TLS → testssl.sh"), ("whatweb", "HTTP → WhatWeb")):
            combo = QComboBox()
            combo.addItem("Desactivado", "off")
            combo.addItem("Proponer después", "ask")
            combo.addItem("Ejecutar si se detecta", "auto")
            combo.setCurrentIndex(1)
            self.conditionals[key] = combo
            conditional.addRow(label, combo)
        layout.addWidget(self.conditional_group)

        self.web_group = QGroupBox("2. Herramientas web")
        web = QVBoxLayout(self.web_group)
        self.web_checks: dict[str, QCheckBox] = {}
        for key in ("subfinder", "httpx", "whatweb", "ffuf", "nuclei", "testssl", "nikto"):
            check = QCheckBox(TOOLS[key].label)
            check.setChecked(key in {"httpx", "whatweb"})
            self.web_checks[key] = check
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(info_button(self, TOOLS[key].label, TOOLS[key].description))
            row.addStretch()
            web.addLayout(row)
        wordlist_row = QHBoxLayout()
        self.wordlist = QLineEdit("/usr/share/wordlists/dirb/common.txt")
        choose_wordlist = QPushButton("Elegir lista para ffuf")
        choose_wordlist.clicked.connect(self.pick_wordlist)
        wordlist_row.addWidget(self.wordlist)
        wordlist_row.addWidget(choose_wordlist)
        web.addLayout(wordlist_row)
        layout.addWidget(self.web_group)

        export_group = QGroupBox("4. Resumen exportado")
        export_layout = QHBoxLayout(export_group)
        export_layout.addWidget(QLabel("Los archivos originales siempre se conservan."))
        self.export_checks: dict[str, QCheckBox] = {}
        for fmt in ("json", "csv", "html"):
            check = QCheckBox(fmt.upper())
            check.setChecked(fmt == "json")
            self.export_checks[fmt] = check
            export_layout.addWidget(check)
        layout.addWidget(export_group)
        self.target_mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        buttons = QHBoxLayout()
        preview = QPushButton("Revisar plan")
        start = QPushButton("Iniciar escaneo")
        preview.clicked.connect(self.preview)
        start.clicked.connect(self.start_scan)
        buttons.addWidget(preview)
        buttons.addWidget(start)
        layout.addLayout(buttons)
        layout.addStretch()

    def update_mode(self) -> None:
        ip = self.target_mode.currentData() == "ip"
        self.ip_group.setVisible(ip)
        self.conditional_group.setVisible(ip)
        self.web_group.setVisible(not ip)

    def update_port_blocker(self, _button=None) -> None:
        selected = self.port_group.checkedButton()
        if selected:
            self.port_blocker.setText(f"Las otras opciones de puertos quedan excluidas por «{selected.text()}». Solo puede haber un alcance por tarea Nmap.")

    def show_port_info(self, option: QRadioButton) -> None:
        selected = self.port_group.checkedButton()
        if selected is option:
            blocked = [radio.text() for radio in (self.top100, self.all_ports, self.custom) if radio is not option]
            footer = "Bloquea: " + ", ".join(blocked) + "."
        else:
            footer = f"Bloqueada por: {selected.text() if selected else 'ninguna opción'}."
        QMessageBox.information(
            self, option.text(),
            f"Selecciona el alcance de puertos que usará Nmap.\n\n{footer}\nMotivo: una tarea Nmap usa un solo alcance. Puedes cambiarlo seleccionando otra opción."
        )

    def pick_wordlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Lista de palabras para ffuf", self.wordlist.text())
        if path:
            self.wordlist.setText(path)

    def options(self) -> ScanOptions:
        ip = self.target_mode.currentData() == "ip"
        return ScanOptions(
            target_mode="ip" if ip else "web",
            targets=self.targets.toPlainText().splitlines(),
            exclusions=[entry.strip() for entry in self.exclusions.text().split(",") if entry.strip()],
            port_mode=("top100", "all", "custom")[self.port_group.checkedId()],
            custom_ports=self.custom_ports.text(),
            timing=int(self.timing.currentData()),
            version_detection=self.version.isChecked(),
            script_categories=[key for key, check in self.script_checks.items() if check.isChecked()] if ip else [],
            script_names=[name.strip() for name in self.script_names.text().split(",") if name.strip()] if ip else [],
            web_tools=[key for key, check in self.web_checks.items() if check.isChecked()] if not ip else [],
            conditional={key: str(combo.currentData()) for key, combo in self.conditionals.items()} if ip else {},
            formats=["txt"] + [fmt for fmt, check in self.export_checks.items() if check.isChecked()],
            wordlist=self.wordlist.text().strip(),
        )

    def current_plan(self) -> Plan | None:
        try:
            return make_plan(self.options())
        except (PlanError, ValueError) as exc:
            QMessageBox.warning(self, "Revisar opciones", str(exc))
            return None

    def _missing(self, plan: Plan) -> list[str]:
        keys = {task.tool for task in plan.tasks}
        keys.update(task.tool for task in plan.possible_tasks if plan.options.conditional.get(task.tool) == "auto")
        missing = []
        for key in sorted(keys):
            configured = str(self.settings.value(f"tool/{key}/path", ""))
            if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
                continue
            if not any(shutil.which(binary) for binary in TOOLS[key].binaries):
                missing.append(TOOLS[key].label)
        return missing

    def preview(self) -> None:
        plan = self.current_plan()
        if not plan:
            return
        lines = [f"Archivos de resultados previstos: {plan.planned_files}",
                 f"Archivos condicionales estimados: {plan.possible_files} (pueden variar según los servicios detectados)",
                 "Formatos de resumen: " + ", ".join(fmt.upper() for fmt in plan.options.formats if fmt in {"json", "csv", "html"}),
                 "Exclusiones: " + (", ".join(plan.options.exclusions) if plan.options.exclusions else "ninguna"),
                 "", "Tareas:"]
        lines.extend(f"• {task.tool} → {task.target}: {task.reason}" for task in plan.tasks)
        lines.extend(["", "Comandos previstos (las rutas finales tendrán un ID de ejecución):"])
        for task in plan.tasks:
            key = "nmap" if task.tool in {"nmap_versions", "nmap_vuln"} else task.tool
            binary = str(self.settings.value(f"tool/{key}/path", "")) or TOOLS[key].binaries[0]
            try:
                spec = build_command(task.tool, binary, task.target, self.project.root / "FECHA" / task.tool / "EJECUCION", "objetivo", plan.options)
                lines.append(shlex.join(spec.argv))
            except PlanError as exc:
                lines.append(f"{task.tool}: {exc}")
        if plan.possible_tasks:
            lines.extend(["", "Pasos condicionales:"])
            lines.extend(f"• {task.tool} → {task.target}" for task in plan.possible_tasks)
        if plan.warnings:
            lines.extend(["", "Avisos:", *[f"• {warning}" for warning in dict.fromkeys(plan.warnings)]])
        missing = self._missing(plan)
        if missing:
            lines.extend(["", "Herramientas faltantes: " + ", ".join(missing)])
        details = QDialog(self)
        details.setWindowTitle("Revisar plan")
        details.resize(750, 520)
        box = QVBoxLayout(details)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(lines))
        box.addWidget(text)
        if plan.options.target_mode == "ip" and (plan.options.script_categories or plan.options.script_names):
            script_help = QPushButton("Ver scripts NSE seleccionados")
            script_help.clicked.connect(lambda: self.show_script_help(plan.options))
            box.addWidget(script_help)
        close = QPushButton("Cerrar")
        close.clicked.connect(details.accept)
        box.addWidget(close)
        details.exec()

    def show_script_help(self, options: ScanOptions) -> None:
        binary = str(self.settings.value("tool/nmap/path", "")) or shutil.which("nmap")
        if not binary:
            QMessageBox.warning(self, "Scripts NSE", "Nmap no está disponible.")
            return
        selectors = ",".join(dict.fromkeys(options.script_categories + options.script_names))
        try:
            result = subprocess.run([binary, "--script-help", selectors], capture_output=True, text=True, timeout=20, check=False)
            output = (result.stdout + "\n" + result.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            output = str(exc)
        dialog = QDialog(self)
        dialog.setWindowTitle("Scripts NSE que coinciden con la selección")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("La categoría vuln puede incluir scripts intrusivos o de explotación."))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(output or "No se obtuvo una lista de scripts.")
        layout.addWidget(text)
        close = QPushButton("Cerrar")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def start_scan(self) -> None:
        plan = self.current_plan()
        if not plan:
            return
        missing = self._missing(plan)
        if missing:
            answer = QMessageBox.question(self, "Herramientas faltantes", "Faltan: " + ", ".join(missing) + "\n\n¿Abrir la comprobación de herramientas?")
            if answer == QMessageBox.Yes:
                self.environment_requested.emit()
            return
        self.preview()
        if QMessageBox.question(self, "Iniciar", "¿Ejecutar el plan revisado sobre los objetivos indicados?") == QMessageBox.Yes:
            self.scan_requested.emit(plan)
