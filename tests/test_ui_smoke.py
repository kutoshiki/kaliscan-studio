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


if __name__ == "__main__":
    unittest.main()
