from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from PySide6.QtWidgets import QApplication
    from kaliscan_studio.app import MainWindow
except ImportError:
    QApplication = None
    MainWindow = None


@unittest.skipUnless(QApplication is not None, "PySide6 no está instalado en este equipo")
class UISmokeTests(unittest.TestCase):
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
