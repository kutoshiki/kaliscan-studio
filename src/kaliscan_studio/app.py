"""Ventana principal de KaliScan Studio."""

from __future__ import annotations

import json
import html
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from .catalog import TOOLS
from .models import Plan, ScanOptions
from .planner import PlanError, make_plan
from .projects import Project, recent_projects
from .runner import ScanEngine
from .suggestions import Suggestion, propose
from .ui import EnvironmentDialog, PlanForm


DARK = """
QWidget { background:#111827; color:#e5e7eb; font-size:13px; }
QPushButton, QComboBox, QLineEdit, QTextEdit, QListWidget, QTableWidget, QTabWidget::pane {
  background:#1f2937; color:#f9fafb; border:1px solid #374151; border-radius:6px; padding:5px;
}
QPushButton:hover { background:#374151; }
QPushButton:disabled { color:#6b7280; }
QGroupBox { border:1px solid #374151; border-radius:8px; margin-top:12px; padding:10px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
QTabBar::tab { background:#1f2937; color:#e5e7eb; padding:9px 14px; margin-right:2px; }
QTabBar::tab:selected { background:#2563eb; }
QHeaderView::section { background:#374151; color:#fff; padding:6px; }
"""

LIGHT = """
QWidget { background:#f8fafc; color:#111827; font-size:13px; }
QPushButton, QComboBox, QLineEdit, QTextEdit, QListWidget, QTableWidget, QTabWidget::pane {
  background:#fff; color:#111827; border:1px solid #cbd5e1; border-radius:6px; padding:5px;
}
QPushButton:hover { background:#e2e8f0; }
QPushButton:disabled { color:#94a3b8; }
QGroupBox { border:1px solid #cbd5e1; border-radius:8px; margin-top:12px; padding:10px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
QTabBar::tab { background:#e2e8f0; color:#111827; padding:9px 14px; margin-right:2px; }
QTabBar::tab:selected { background:#bfdbfe; }
QHeaderView::section { background:#e2e8f0; color:#111827; padding:6px; }
"""


class ScanWorker(QThread):
    event = Signal(str, str)
    finished_scan = Signal(str)

    def __init__(self, project: Project, paths: dict[str, str], plan: Plan | None = None,
                 suggestion: Suggestion | None = None, options: ScanOptions | None = None,
                 resume_run_id: str | None = None):
        super().__init__()
        self.engine = ScanEngine(project, paths, lambda kind, message: self.event.emit(kind, message))
        self.plan = plan
        self.suggestion = suggestion
        self.options = options
        self.resume_run_id = resume_run_id

    def run(self) -> None:
        try:
            if self.plan:
                run_id = self.engine.run_plan(self.plan, self.resume_run_id)
            elif self.suggestion and self.options:
                run_id = self.engine.run_suggestion(self.suggestion, self.options)
            else:
                raise RuntimeError("No hay un plan para ejecutar.")
            self.finished_scan.emit(run_id)
        except Exception as exc:  # El error debe quedar visible; la ventana no debe cerrarse.
            self.event.emit("error", f"Error del motor: {exc}")
            self.finished_scan.emit("")

    def cancel(self) -> None:
        self.engine.cancel()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("KaliScan Studio", "KaliScan Studio")
        self.project: Project | None = None
        self.scan_worker: ScanWorker | None = None
        self.setWindowTitle("KaliScan Studio")
        self.resize(1120, 760)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.home_button = QPushButton("Inicio")
        self.home_button.clicked.connect(self.go_home)
        header.addWidget(self.home_button)
        header.addWidget(QLabel("<b>KaliScan Studio</b>"))
        header.addStretch()
        tools_button = QPushButton("Herramientas de Kali")
        tools_button.clicked.connect(self.show_tools)
        header.addWidget(tools_button)
        header.addWidget(QLabel("Tema:"))
        self.theme = QComboBox()
        self.theme.addItem("Sistema", "system")
        self.theme.addItem("Claro", "light")
        self.theme.addItem("Oscuro", "dark")
        current = self.settings.value("theme", "system")
        self.theme.setCurrentIndex(max(0, self.theme.findData(current)))
        self.theme.currentIndexChanged.connect(self.apply_theme)
        header.addWidget(self.theme)
        layout.addLayout(header)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.home = self._build_home()
        self.stack.addWidget(self.home)
        self.project_page: QWidget | None = None
        self.apply_theme()
        self.refresh_home()
        QTimer.singleShot(150, self.show_tools)

    def apply_theme(self) -> None:
        mode = self.theme.currentData()
        self.settings.setValue("theme", mode)
        QApplication.instance().setStyleSheet(DARK if mode == "dark" else LIGHT if mode == "light" else "")

    def _build_home(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("<h1>Tus proyectos</h1>"))
        layout.addWidget(QLabel("Crea un proyecto o continúa un análisis anterior."))
        actions = QHBoxLayout()
        create = QPushButton("+ Crear proyecto")
        open_existing = QPushButton("Abrir carpeta de proyecto")
        create.clicked.connect(self.create_project)
        open_existing.clicked.connect(self.open_existing)
        actions.addWidget(create)
        actions.addWidget(open_existing)
        actions.addStretch()
        layout.addLayout(actions)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar proyectos…")
        self.search.textChanged.connect(self.refresh_home)
        layout.addWidget(self.search)
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(self.open_recent)
        layout.addWidget(self.recent)
        return widget

    def refresh_home(self) -> None:
        self.recent.clear()
        projects: list[tuple[str, Project, str]] = []
        for item in recent_projects():
            try:
                project = Project(Path(item["path"]))
                runs = project.runs()
                last_run = project.meta.get("last_executed_at", "")
                sort_value = last_run or project.meta.get("created_at", "")
                projects.append((sort_value, project, last_run))
            except (OSError, ValueError, KeyError):
                continue
        for _sort, project, last_run in sorted(projects, key=lambda item: item[0], reverse=True):
            if self.search.text().casefold() not in project.meta["name"].casefold():
                continue
            runs = project.runs()
            targets: set[str] = set()
            for run in runs:
                try:
                    targets.update(json.loads(run["options_json"]).get("targets", []))
                except ValueError:
                    pass
            last_status = runs[0]["status"] if runs else "sin ejecuciones"
            display = f"{project.meta['name']}\n{last_run or 'Todavía sin escaneos'} · {len(targets)} objetivos · {last_status}\n{project.root}"
            row = QListWidgetItem(display)
            row.setData(Qt.UserRole, str(project.root))
            self.recent.addItem(row)
        if not self.recent.count():
            self.recent.addItem("No hay proyectos recientes. Crea el primero con el botón superior.")

    def create_project(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, okay = QInputDialog.getText(self, "Nuevo proyecto", "Nombre del proyecto:")
        if not okay or not name.strip():
            return
        parent = QFileDialog.getExistingDirectory(self, "Carpeta donde crear el proyecto", str(Path.home()))
        if not parent:
            return
        try:
            project = Project.create(Path(parent), name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Proyecto", str(exc))
            return
        self.open_project(project.root)

    def open_existing(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleccionar proyecto")
        if path:
            self.open_project(Path(path))

    def open_recent(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.open_project(Path(path))

    def open_project(self, path: Path) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            QMessageBox.information(self, "Escaneo en curso", "Espera a que termine o cancela el escaneo actual.")
            return
        try:
            self.project = Project(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Abrir proyecto", str(exc))
            return
        if self.project_page:
            self.stack.removeWidget(self.project_page)
            self.project_page.deleteLater()
        self.project_page = self._build_project()
        self.stack.addWidget(self.project_page)
        self.stack.setCurrentWidget(self.project_page)
        self.refresh_project()

    def _build_project(self) -> QWidget:
        assert self.project
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel(f"<h1>{html.escape(self.project.meta['name'])}</h1>"))
        layout.addWidget(QLabel(str(self.project.root)))
        self.resume_button = QPushButton("Reanudar tareas pendientes de la última ejecución")
        self.resume_button.clicked.connect(self.resume_latest)
        layout.addWidget(self.resume_button)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.plan_form = PlanForm(self.project, self.settings)
        self.plan_form.scan_requested.connect(self.start_plan)
        self.plan_form.environment_requested.connect(self.show_tools)
        self.tabs.addTab(self.plan_form, "Nuevo escaneo")

        progress_page = QWidget()
        progress = QVBoxLayout(progress_page)
        self.progress_label = QLabel("No hay un escaneo en curso.")
        progress.addWidget(self.progress_label)
        self.progress_log = QTextEdit()
        self.progress_log.setReadOnly(True)
        progress.addWidget(self.progress_log)
        self.cancel_button = QPushButton("Cancelar ejecución")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_scan)
        progress.addWidget(self.cancel_button)
        self.tabs.addTab(progress_page, "Progreso")

        results_page = QWidget()
        results = QVBoxLayout(results_page)
        results.addWidget(QLabel("Doble clic en un archivo para abrirlo."))
        self.results_table = QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(["Fecha / ejecución", "Herramienta", "Objetivo", "Estado", "Archivo o comando"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.itemDoubleClicked.connect(self.open_result_file)
        results.addWidget(self.results_table)
        self.tabs.addTab(results_page, "Resultados")

        changes_page = QWidget()
        changes_layout = QVBoxLayout(changes_page)
        changes_layout.addWidget(QLabel("Cambios entre las dos ejecuciones más recientes de la misma herramienta y objetivo."))
        self.changes_text = QTextEdit()
        self.changes_text.setReadOnly(True)
        changes_layout.addWidget(self.changes_text)
        self.tabs.addTab(changes_page, "Cambios")

        next_page = QWidget()
        next_layout = QVBoxLayout(next_page)
        next_layout.addWidget(QLabel("Elige qué paso realizar a partir de los resultados. Ninguna propuesta se ejecuta por sí sola."))
        self.suggestions_list = QListWidget()
        next_layout.addWidget(self.suggestions_list)
        next_actions = QHBoxLayout()
        run = QPushButton("Ejecutar paso elegido")
        dismiss = QPushButton("Descartar propuesta")
        run.clicked.connect(self.start_suggestion)
        dismiss.clicked.connect(self.dismiss_suggestion)
        next_actions.addWidget(run)
        next_actions.addWidget(dismiss)
        next_actions.addStretch()
        next_layout.addLayout(next_actions)
        self.tabs.addTab(next_page, "Siguientes pasos")

        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        report_layout.addWidget(QLabel("Revisa las observaciones y registra si cada una es un indicio, está comprobada o fue descartada."))
        self.report_table = QTableWidget(0, 5)
        self.report_table.setHorizontalHeaderLabels(["Objetivo", "Tipo", "Observación", "Estado", "Nota"])
        self.report_table.horizontalHeader().setStretchLastSection(True)
        report_layout.addWidget(self.report_table)
        report_actions = QHBoxLayout()
        save = QPushButton("Guardar revisión")
        export = QPushButton("Exportar informe HTML")
        save.clicked.connect(self.save_reviews)
        export.clicked.connect(self.export_report)
        report_actions.addWidget(save)
        report_actions.addWidget(export)
        report_actions.addStretch()
        report_layout.addLayout(report_actions)
        self.tabs.addTab(report_page, "Informe")
        return widget

    def refresh_project(self) -> None:
        if not self.project or not self.project_page:
            return
        runs = self.project.runs()
        self.resume_button.setEnabled(bool(runs and runs[0]["kind"] == "plan" and runs[0]["status"] in {"running", "partial", "cancelled", "failed"}))
        self.results_table.setRowCount(0)
        artifacts = self.project.artifacts()
        for artifact in artifacts:
            job = next((row for row in self.project.jobs() if row["id"] == artifact["job_id"]), None)
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            values = (artifact["run_id"], artifact["tool"], artifact["target"], job["status"] if job else "", artifact["path"])
            for col, value in enumerate(values):
                self.results_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.results_table.resizeColumnsToContents()
        changes = self.project.compare_jobs()
        if changes:
            lines = []
            for change in changes:
                lines.append(f"{change['tool']} · {change['target']}\n{change['old_run']} → {change['new_run']}")
                lines.extend(f"+ {kind}: {value}" for kind, value in change["added"])
                lines.extend(f"− {kind}: {value}" for kind, value in change["removed"])
                lines.append("")
            self.changes_text.setPlainText("\n".join(lines))
        else:
            self.changes_text.setPlainText("Todavía no hay cambios comparables.")
        self.suggestions_list.clear()
        for suggestion in propose(self.project):
            item = QListWidgetItem(f"{suggestion.label} · {suggestion.target}\n{suggestion.reason}")
            item.setData(Qt.UserRole, suggestion)
            self.suggestions_list.addItem(item)
        if not self.suggestions_list.count():
            self.suggestions_list.addItem("No hay nuevos pasos propuestos con la evidencia actual.")
        self.report_table.setRowCount(0)
        reviews = self.project.reviews()
        for observation in self.project.observations():
            row = self.report_table.rowCount()
            self.report_table.insertRow(row)
            for col, value in enumerate((observation["target"], observation["kind"], observation["value"])):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.report_table.setItem(row, col, item)
            combo = QComboBox()
            for state in ("indicio", "comprobado", "descartado"):
                combo.addItem(state)
            combo.setCurrentText(reviews.get(observation["id"], {}).get("status", "indicio"))
            self.report_table.setCellWidget(row, 3, combo)
            note = QTableWidgetItem(reviews.get(observation["id"], {}).get("note", ""))
            note.setData(Qt.UserRole, observation["id"])
            self.report_table.setItem(row, 4, note)
        self.report_table.resizeColumnsToContents()

    def open_result_file(self, item: QTableWidgetItem) -> None:
        if not self.project:
            return
        row = item.row()
        file_item = self.results_table.item(row, 4)
        if file_item:
            path = (self.project.root / file_item.text()).resolve()
            if path.is_relative_to(self.project.root) and path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _tool_paths(self) -> dict[str, str]:
        return {key: str(self.settings.value(f"tool/{key}/path", "")) for key in TOOLS}

    def start_plan(self, plan: Plan) -> None:
        if not self.project or self._busy():
            return
        self._launch(ScanWorker(self.project, self._tool_paths(), plan=plan))

    def resume_latest(self) -> None:
        if not self.project or self._busy():
            return
        runs = self.project.runs()
        if not runs or runs[0]["kind"] != "plan" or runs[0]["status"] == "complete":
            return
        try:
            options = ScanOptions.from_dict(json.loads(runs[0]["options_json"]))
            plan = make_plan(options)
        except (ValueError, PlanError) as exc:
            QMessageBox.warning(self, "Reanudar", f"No se pudo reconstruir el plan: {exc}")
            return
        missing = self.plan_form._missing(plan)
        if missing:
            QMessageBox.warning(self, "Herramientas faltantes", "Faltan: " + ", ".join(missing))
            return
        if QMessageBox.question(self, "Reanudar", "Se ejecutarán solo las tareas pendientes o fallidas de la última ejecución. ¿Continuar?") != QMessageBox.Yes:
            return
        self._launch(ScanWorker(self.project, self._tool_paths(), plan=plan, resume_run_id=runs[0]["id"]))

    def start_suggestion(self) -> None:
        if not self.project or self._busy():
            return
        item = self.suggestions_list.currentItem()
        suggestion: Suggestion | None = item.data(Qt.UserRole) if item else None
        if not suggestion:
            QMessageBox.information(self, "Siguiente paso", "Selecciona una propuesta.")
            return
        key = "nmap" if suggestion.tool in {"nmap_versions", "nmap_vuln"} else suggestion.tool
        configured = self._tool_paths().get(key)
        if not (configured and Path(configured).is_file() and os.access(configured, os.X_OK)) and not any(shutil.which(binary) for binary in TOOLS[key].binaries):
            QMessageBox.warning(self, "Herramienta faltante", f"Falta {TOOLS[key].label}. Abre Herramientas de Kali para instalarla.")
            return
        options = self.project.latest_options()
        if not options:
            return
        if suggestion.tool == "nmap_vuln":
            self.plan_form.show_script_help(ScanOptions(target_mode="ip", targets=[suggestion.target], script_categories=["vuln"]))
        if QMessageBox.question(self, "Ejecutar siguiente paso", f"{suggestion.label}\nObjetivo: {suggestion.target}\n\n{suggestion.reason}\n\n¿Ejecutar?") != QMessageBox.Yes:
            return
        self._launch(ScanWorker(self.project, self._tool_paths(), suggestion=suggestion, options=options))

    def dismiss_suggestion(self) -> None:
        if not self.project:
            return
        item = self.suggestions_list.currentItem()
        suggestion: Suggestion | None = item.data(Qt.UserRole) if item else None
        if suggestion:
            self.project.dismiss_suggestion(suggestion.tool, suggestion.target)
            self.refresh_project()

    def _busy(self) -> bool:
        return bool(self.scan_worker and self.scan_worker.isRunning())

    def _launch(self, worker: ScanWorker) -> None:
        self.scan_worker = worker
        worker.event.connect(self.scan_event)
        worker.finished_scan.connect(self.scan_finished)
        self.progress_log.clear()
        self.progress_label.setText("Escaneo en curso…")
        self.cancel_button.setEnabled(True)
        self.plan_form.setEnabled(False)
        self.tabs.setCurrentIndex(1)
        worker.start()

    def scan_event(self, kind: str, message: str) -> None:
        self.progress_log.append(f"[{kind}] {message}")
        if kind in {"start", "done", "error"}:
            self.progress_label.setText(message)

    def scan_finished(self, run_id: str) -> None:
        self.cancel_button.setEnabled(False)
        self.plan_form.setEnabled(True)
        self.progress_label.setText("Ejecución terminada." if run_id else "La ejecución terminó con un error del motor.")
        self.refresh_project()
        self.refresh_home()

    def cancel_scan(self) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            self.progress_label.setText("Cancelando la tarea actual…")

    def save_reviews(self) -> None:
        if not self.project:
            return
        for row in range(self.report_table.rowCount()):
            note = self.report_table.item(row, 4)
            combo = self.report_table.cellWidget(row, 3)
            if note and isinstance(combo, QComboBox):
                self.project.save_review(int(note.data(Qt.UserRole)), combo.currentText(), note.text())
        QMessageBox.information(self, "Informe", "Revisión guardada.")

    def export_report(self) -> None:
        if not self.project:
            return
        for row in range(self.report_table.rowCount()):
            note = self.report_table.item(row, 4)
            combo = self.report_table.cellWidget(row, 3)
            if note and isinstance(combo, QComboBox):
                self.project.save_review(int(note.data(Qt.UserRole)), combo.currentText(), note.text())
        path = self.project.export_report()
        QMessageBox.information(self, "Informe exportado", str(path))

    def show_tools(self) -> None:
        dialog = EnvironmentDialog(self.settings, self)
        dialog.exec()

    def go_home(self) -> None:
        self.refresh_home()
        self.stack.setCurrentWidget(self.home)

    def closeEvent(self, event) -> None:
        if self._busy():
            QMessageBox.information(self, "Escaneo en curso", "Cancela la ejecución y espera a que termine antes de cerrar.")
            event.ignore()
        else:
            event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("KaliScan Studio")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
