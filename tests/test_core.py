from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kaliscan_studio.commands import CommandSpec, build_command
from kaliscan_studio.models import ScanOptions
from kaliscan_studio.parsers import parse_nmap
from kaliscan_studio.planner import PlanError, make_plan, normalize_targets
from kaliscan_studio.projects import Project
from kaliscan_studio.runner import ScanEngine
from kaliscan_studio.suggestions import propose


NMAP_XML = """<?xml version="1.0"?>
<nmaprun><host><status state="up"/><address addr="192.0.2.10" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="80"><state state="open"/>
<service name="http" product="nginx" version="1.24"/></port>
<port protocol="tcp" portid="443"><state state="open"/>
<service name="https"/></port><port protocol="tcp" portid="445">
<state state="open"/><service name="microsoft-ds"/></port></ports></host></nmaprun>"""


class PlannerTests(unittest.TestCase):
    def test_multiple_targets_and_exclusive_port_modes(self):
        options = ScanOptions(target_mode="ip", targets=["192.0.2.10, 192.0.2.10\n192.0.2.11"],
                              port_mode="custom", custom_ports="22, 80-81", timing=3,
                              script_categories=["vuln"], formats=["json", "csv"])
        plan = make_plan(options)
        self.assertEqual(options.targets, ["192.0.2.10", "192.0.2.11"])
        self.assertEqual(plan.planned_files, 6)
        spec = build_command("nmap", "/usr/bin/nmap", "192.0.2.10", Path("/tmp/example"), "target", options)
        self.assertIn("--script", spec.argv)
        self.assertIn("vuln", spec.argv)
        self.assertIn("22,80-81", spec.argv)
        self.assertNotIn("-p-", spec.argv)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(PlanError):
            normalize_targets("192.0.2.1;not-an-ip", "ip")
        with self.assertRaises(PlanError):
            make_plan(ScanOptions(target_mode="ip", targets=["192.0.2.1"], timing=9))
        with self.assertRaises(PlanError):
            make_plan(ScanOptions(target_mode="ip", targets=["192.0.2.1"], port_mode="custom", custom_ports="0,70000"))

    def test_web_probe_is_visible_in_plan(self):
        plan = make_plan(ScanOptions(target_mode="web", targets=["example.org"], web_tools=["whatweb"]))
        self.assertEqual([task.tool for task in plan.tasks], ["httpx", "whatweb"])
        self.assertEqual(plan.planned_files, 3)

    def test_exclusions_remove_targets_before_planning(self):
        options = ScanOptions(target_mode="ip", targets=["192.0.2.10", "192.0.2.11"], exclusions=["192.0.2.11"])
        plan = make_plan(options)
        self.assertEqual([task.target for task in plan.tasks], ["192.0.2.10"])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get("KALISCAN_TEST_TMP", tempfile.gettempdir())).resolve()
        self.temp_root = base / ("kaliscan-test-" + uuid.uuid4().hex)
        self.temp_root.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.temp_root) if self.temp_root.is_relative_to(base) else None)
        self.environment = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.temp_root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.project = Project.create(self.temp_root, "Práctica de red")

    def test_parse_and_reopen_project(self):
        xml = self.temp_root / "scan.xml"
        xml.write_text(NMAP_XML, encoding="utf-8")
        observations = parse_nmap(xml, "192.0.2.10")
        self.assertEqual([value for kind, value, _ in observations if kind == "port"], ["80/tcp", "443/tcp", "445/tcp"])
        reopened = Project(self.project.root)
        self.assertEqual(reopened.meta["name"], "Práctica de red")

    def test_engine_persists_outputs_and_proposals(self):
        plan = make_plan(ScanOptions(target_mode="ip", targets=["192.0.2.10"], formats=["json", "csv"]))

        def fake_command(_tool, _binary, _target, folder, stem, _options, _ports=None):
            xml = folder / f"{stem}.xml"
            txt = folder / f"{stem}.txt"
            script = "from pathlib import Path; Path(%r).write_text(%r, encoding='utf-8')" % (str(xml), NMAP_XML)
            return CommandSpec((sys.executable, "-c", script), {"xml": xml, "txt": txt})

        with mock.patch("kaliscan_studio.runner.build_command", side_effect=fake_command):
            engine = ScanEngine(self.project, {"nmap": sys.executable})
            run_id = engine.run_plan(plan)

        self.assertEqual(self.project.runs()[0]["status"], "complete")
        self.assertTrue(any(row["kind"] == "version" for row in self.project.observations()))
        self.assertTrue(any(item.tool == "nmap_versions" for item in propose(self.project)))
        self.assertTrue(any(item.tool == "enum4linux" for item in propose(self.project)))
        self.assertTrue(any(row["format"] == "csv" for row in self.project.artifacts()))
        report = self.project.export_report()
        report_html = report.read_text(encoding="utf-8")
        self.assertIn("nginx 1.24", report_html)
        self.assertIn("Evidencia", report_html)
        self.assertIn("href=", report_html)

    def test_resume_skips_completed_targets(self):
        plan = make_plan(ScanOptions(target_mode="ip", targets=["192.0.2.10", "192.0.2.11"], formats=[]))

        def fake_command(_tool, _binary, _target, folder, stem, _options, _ports=None):
            xml = folder / f"{stem}.xml"
            txt = folder / f"{stem}.txt"
            script = "from pathlib import Path; Path(%r).write_text(%r, encoding='utf-8')" % (str(xml), NMAP_XML)
            return CommandSpec((sys.executable, "-c", script), {"xml": xml, "txt": txt})

        engine = ScanEngine(self.project, {"nmap": sys.executable})
        def cancel_after_first(kind, _message):
            if kind == "done":
                engine.cancel()
        engine.event = cancel_after_first
        with mock.patch("kaliscan_studio.runner.build_command", side_effect=fake_command):
            run_id = engine.run_plan(plan)
            self.assertEqual(self.project.runs()[0]["status"], "cancelled")
            resumed = ScanEngine(self.project, {"nmap": sys.executable})
            resumed.run_plan(plan, resume_run_id=run_id)
        completed = [job for job in self.project.jobs(run_id) if job["tool"] == "nmap" and job["status"] == "complete"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(self.project.runs()[0]["status"], "complete")

    def test_progress_events_survive_reopening_project(self):
        run_id = self.project.new_run(ScanOptions(target_mode="ip", targets=["192.0.2.10"]))
        self.project.add_run_event(run_id, "run", run_id)
        self.project.add_run_event(run_id, "start", "nmap: 192.0.2.10")
        reopened = Project(self.project.root)
        self.assertEqual(
            [(event["kind"], event["message"]) for event in reopened.run_events(run_id)],
            [("run", run_id), ("start", "nmap: 192.0.2.10")],
        )


if __name__ == "__main__":
    unittest.main()
