from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from PySide6.QtWidgets import QApplication, QPushButton
    from kaliscan_studio.app import MainWindow
except ImportError:
    QApplication = None
    MainWindow = None


@unittest.skipUnless(QApplication is not None, "PySide6 no está instalado en este equipo")
class UISmokeTests(unittest.TestCase):
    def test_project_can_be_renamed_and_sent_to_trash_from_home(self):
        from kaliscan_studio.projects import Project
        import tempfile
        import uuid
        import shutil

        base = Path(os.environ.get("KALISCAN_TEST_TMP", tempfile.gettempdir())).resolve()
        root = base / ("kaliscan-ui-test-" + uuid.uuid4().hex)
        root.mkdir()
        self.addCleanup(lambda: shutil.rmtree(root) if root.is_relative_to(base) else None)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(root), "XDG_CONFIG_HOME": str(root)}):
            app = QApplication.instance() or QApplication([])
            with mock.patch("kaliscan_studio.app.QTimer.singleShot"):
                window = MainWindow()
            project = Project.create(root, "Original")
            window.open_project(project.root)
            window.go_home()
            window.recent.setCurrentRow(0)
            self.assertTrue(window.rename_project_button.isEnabled())
            with mock.patch("kaliscan_studio.app.QInputDialog.getText", return_value=("Renombrado", True)):
                window.rename_selected_project()
            self.assertEqual(Project(project.root).meta["name"], "Renombrado")
            self.assertIn("Renombrado", window.project_title.text())

            trash_path = root / "en-papelera"
            from PySide6.QtWidgets import QMessageBox
            with mock.patch("kaliscan_studio.app.QMessageBox.question", return_value=QMessageBox.Yes), \
                 mock.patch("kaliscan_studio.projects.send2trash", side_effect=lambda path: Path(path).rename(trash_path)):
                window.delete_selected_project()
            self.assertFalse(project.root.exists())
            self.assertIsNone(window.project)
            self.assertFalse(window.delete_project_button.isEnabled())
            window.close()
            app.processEvents()

    def test_window_and_project_tabs_construct(self):
        from kaliscan_studio.projects import Project
        import tempfile
        import uuid
        import shutil

        base = Path(os.environ.get("KALISCAN_TEST_TMP", tempfile.gettempdir())).resolve()
        root = base / ("kaliscan-ui-test-" + uuid.uuid4().hex)
        root.mkdir()
        self.addCleanup(lambda: shutil.rmtree(root) if root.is_relative_to(base) else None)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(root), "XDG_CONFIG_HOME": str(root)}):
            app = QApplication.instance() or QApplication([])
            with mock.patch("kaliscan_studio.app.QTimer.singleShot"):
                window = MainWindow()
            project = Project.create(root, "UI")
            window.open_project(project.root)
            self.assertEqual(window.tabs.count(), 6)
            self.assertEqual(window.plan_form.target_mode.currentData(), "ip")
            button_labels = {button.text() for button in window.findChildren(QPushButton)}
            self.assertIn("Actualizar KaliScan", button_labels)
            self.assertIn("Ver revisiones guardadas", button_labels)
            window.close()
            app.processEvents()

    def test_active_scan_stays_visible_after_switching_projects_and_result_opens(self):
        from kaliscan_studio.models import ScanOptions
        from kaliscan_studio.projects import Project

        import tempfile
        import uuid
        import shutil

        base = Path(os.environ.get("KALISCAN_TEST_TMP", tempfile.gettempdir())).resolve()
        root = base / ("kaliscan-ui-test-" + uuid.uuid4().hex)
        root.mkdir()
        self.addCleanup(lambda: shutil.rmtree(root) if root.is_relative_to(base) else None)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(root), "XDG_CONFIG_HOME": str(root)}):
            app = QApplication.instance() or QApplication([])
            with mock.patch("kaliscan_studio.app.QTimer.singleShot"):
                window = MainWindow()
            project = Project.create(root, "Primero")
            other = Project.create(root, "Segundo")
            window.open_project(project.root)
            run_id = project.new_run(ScanOptions(target_mode="ip", targets=["192.0.2.10"]))
            project.add_run_event(run_id, "start", "nmap: 192.0.2.10")
            job_id = project.start_job(run_id, "nmap", "192.0.2.10", "Puertos", ["nmap"])
            result = project.output_dir(run_id, "nmap") / "scan.txt"
            result.write_text("resultado", encoding="utf-8")
            project.add_artifact(job_id, result)
            project.add_observation(run_id, job_id, "192.0.2.10", "port", "80/tcp")
            project.add_observation(run_id, job_id, "192.0.2.10", "port", "80/tcp")
            worker = mock.Mock()
            worker.project = project
            worker.isRunning.return_value = True
            window.scan_worker = worker

            window.go_home()
            window.open_project(other.root)
            window.open_project(project.root)
            self.assertEqual(window.tabs.currentIndex(), 1)
            self.assertIn("Escaneo en curso", window.progress_label.text())
            self.assertIn("nmap: 192.0.2.10", window.progress_log.toPlainText())
            self.assertTrue(window.cancel_button.isEnabled())
            self.assertEqual(window.report_table.rowCount(), 1)
            self.assertEqual(window.report_table.item(0, 3).text(), "2")
            window.report_table.cellWidget(0, 4).setCurrentText("comprobado")
            window.report_table.item(0, 5).setText("Revisado")
            self.assertEqual(window._store_reviews(), 1)
            self.assertEqual(project.saved_reviews()[0]["note"], "Revisado")
            with mock.patch("kaliscan_studio.app.QDesktopServices") as services:
                services.openUrl.return_value = True
                window.results_table.itemClicked.emit(window.results_table.item(0, 0))
                services.openUrl.assert_called_once()
                self.assertEqual(services.openUrl.call_args.args[0].toLocalFile(), str(result))

            worker.isRunning.return_value = False
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
