import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECORDER = Path(__file__).resolve().parents[2] / "usage-metrics" / "scripts" / "metrics.py"
EVENT_FIELDS = {"ts", "run", "plugin", "event", "status", "seconds"}


class HookMetricsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.plugin = self.root / "plugin"
        shutil.copytree(ROOT / "scripts", self.plugin / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        self.work = self.root / "work"
        self.work.mkdir()
        self.home = self.root / "agents-home"
        self.home.mkdir()
        self.module = self.root / "module"
        self.module.mkdir()
        self.environment = dict(os.environ, AGENTS_HOME=str(self.home), AGENTS_METRICS_MODULE="")
        self.environment.pop("AGENTS_METRICS", None)

    def run_edit_checks(self, tool_input, event="PreToolUse", scripts=None, **overrides):
        return subprocess.run([sys.executable, str((scripts or self.plugin / "scripts") / "edit-checks.py")],
                              input=json.dumps({"cwd": str(self.work), "hook_event_name": event,
                                                "tool_name": "Edit", "tool_input": tool_input}),
                              env=dict(self.environment, **overrides), text=True, capture_output=True,
                              timeout=10)

    def stub_recorder(self, name):
        log = self.module / f"{name}.log"
        stub = self.module / f"{name}.py"
        stub.write_text(f"#!{sys.executable}\n"
                        "import json, sys\n"
                        "if __name__ == '__main__':\n"
                        f"    with open({str(log)!r}, 'a') as log_file:\n"
                        "        log_file.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                        "def record(*arguments, **options):\n"
                        f"    with open({str(log)!r}, 'a') as log_file:\n"
                        "        log_file.write(json.dumps(['record', *arguments]) + '\\n')")
        return str(stub)

    def install_cache_layout(self):
        scripts = self.root / "cache" / "phil0v" / "code-flow" / "0.8.0" / "scripts"
        shutil.copytree(ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
        return scripts

    def install_cache_recorder(self, version, source=RECORDER):
        target = self.root / "cache" / "phil0v" / "usage-metrics" / version / "scripts" / "metrics.py"
        target.parent.mkdir(parents=True)
        shutil.copy(source, target)
        return target

    def install_clone_layout(self):
        clone = self.root / "clone"
        scripts = clone / "plugins" / "code-flow" / "scripts"
        shutil.copytree(ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
        return scripts

    def install_home_recorder(self):
        target = self.home / "plugins" / "usage-metrics" / "scripts" / "metrics.py"
        target.parent.mkdir(parents=True)
        shutil.copy(RECORDER, target)

    def stub_calls(self, name):
        log = self.module / f"{name}.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def events(self):
        log = self.home / "metrics" / "usage.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def deny_decision(self, result):
        return json.loads(result.stdout)["hookSpecificOutput"]

    def assert_function_event(self, call, event, status):
        self.assertEqual(call[:4], ["record", "code-flow", event, status])
        self.assertEqual(len(call), 5)
        self.assertIsInstance(call[4], float)

    def invalid_json_patch(self):
        return {"command": "*** Begin Patch\n*** Add File: hooks.json\n+{\"hooks\":\n*** End Patch"}

    def run_cache_checks(self, scripts):
        checked = self.run_edit_checks(self.invalid_json_patch(), scripts=scripts)
        self.assertEqual((checked.returncode, checked.stderr), (0, ""))
        self.assertEqual(self.deny_decision(checked)["permissionDecision"], "deny")

    def assert_jsonl_event(self, event, name, status):
        self.assertEqual(set(event), EVENT_FIELDS)
        self.assertEqual((event["plugin"], event["event"], event["status"]),
                         ("code-flow", name, status))
        self.assertRegex(event["ts"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(event["run"], r"^[0-9a-f]{32}$")
        self.assertIsInstance(event["seconds"], float)
        self.assertGreaterEqual(event["seconds"], 0.0)

    def assert_no_recording_side_effects(self):
        self.assertEqual(self.events(), [])
        self.assertFalse((self.home / "metrics").exists())
        self.assertEqual(self.stub_calls("stub"), [])

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_cache_layout_resolves_versioned_sibling_recorder(self):
        self.install_cache_recorder("0.2.0")
        self.run_cache_checks(self.install_cache_layout())
        events = self.events()
        self.assertEqual([event["event"] for event in events], ["edit-checks"])
        for event in events:
            self.assert_jsonl_event(event, "edit-checks", "ok")
        self.assertNotIn(str(self.root), (self.home / "metrics" / "usage.jsonl").read_text())

    def test_cache_layout_prefers_the_highest_numeric_version(self):
        versions = ("0.2.0", "0.10.0", "0.9.0", "preview")
        for version in versions:
            self.install_cache_recorder(version, self.stub_recorder(version))
        self.run_cache_checks(self.install_cache_layout())
        self.assertEqual([version for version in versions if self.stub_calls(version)], ["0.10.0"])
        calls = self.stub_calls("0.10.0")
        self.assertEqual(len(calls), 1)
        self.assert_function_event(calls[0], "edit-checks", "ok")
        self.assertEqual(self.events(), [])

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_clone_layout_prefers_the_unversioned_sibling(self):
        clone = self.root / "clone"
        self.install_clone_layout()
        sibling = clone / "plugins" / "usage-metrics" / "scripts" / "metrics.py"
        sibling.parent.mkdir(parents=True)
        shutil.copy(RECORDER, sibling)
        cached = clone / "usage-metrics" / "9.9.9" / "scripts" / "metrics.py"
        cached.parent.mkdir(parents=True)
        shutil.copy(self.stub_recorder("cached"), cached)
        self.run_cache_checks(clone / "plugins" / "code-flow" / "scripts")
        events = self.events()
        self.assertEqual([event["event"] for event in events], ["edit-checks"])
        for event in events:
            self.assert_jsonl_event(event, "edit-checks", "ok")
        self.assertEqual(self.stub_calls("cached"), [])

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_adjacent_sibling_recorder_is_resolved(self):
        sibling_home = self.root / "sibling-home"
        sibling_home.mkdir()
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit-checks.py")],
                                input=json.dumps({"cwd": str(self.work), "hook_event_name": "PreToolUse",
                                                  "tool_name": "Edit", "tool_input": self.invalid_json_patch()}),
                                env=dict(self.environment, AGENTS_HOME=str(sibling_home), AGENTS_METRICS_MODULE=""),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        log = (sibling_home / "metrics" / "usage.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in log]
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "edit-checks", "ok")
        self.assertNotIn(str(self.root), log[0])

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_edit_checks_deny_records_ok_event_without_paths(self):
        self.install_home_recorder()
        result = self.run_edit_checks(self.invalid_json_patch())
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "edit-checks", "ok")
        line = (self.home / "metrics" / "usage.jsonl").read_text()
        self.assertNotIn(str(self.root), line)
        self.assertNotIn("hooks.json", line)

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_edit_checks_diagnostics_records_ok_event(self):
        self.install_home_recorder()
        (self.work / "config.toml").write_text("model = [\n")
        result = self.run_edit_checks(
            {"command": "*** Begin Patch\n*** Add File: config.toml\n+model = [\n*** End Patch"}, "PostToolUse")
        self.assertEqual(result.returncode, 2)
        self.assertIn("config.toml", result.stderr)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "edit-checks", "ok")
        self.assertNotIn(str(self.root), (self.home / "metrics" / "usage.jsonl").read_text())

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_edit_checks_error_reason_records_ok_event(self):
        self.install_home_recorder()
        result = self.run_edit_checks({}, "PostToolUse")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Edit checks:", result.stderr)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "edit-checks", "ok")
        self.assertNotIn(str(self.root), (self.home / "metrics" / "usage.jsonl").read_text())

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_edit_checks_crash_records_fail_event(self):
        self.install_home_recorder()
        result = self.run_edit_checks({"command": 0}, "PostToolUse")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Traceback", result.stderr)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "edit-checks", "fail")
        self.assertNotIn(str(self.root), (self.home / "metrics" / "usage.jsonl").read_text())

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_metrics_off_skips_recording_entirely(self):
        self.install_home_recorder()
        stub = self.stub_recorder("stub")
        result = self.run_edit_checks(self.invalid_json_patch(), AGENTS_METRICS="off", AGENTS_METRICS_MODULE=stub)
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        self.assert_no_recording_side_effects()

    def test_unresolved_recorder_leaves_edit_checks_byte_identical(self):
        for module in ("/nonexistent/hook-metrics/metrics.py", ""):
            with self.subTest(module=module):
                result = self.run_edit_checks(self.invalid_json_patch(), AGENTS_METRICS_MODULE=module)
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
                self.assertEqual(result.stdout.count("\n"), 1)
        self.assert_no_recording_side_effects()


if __name__ == "__main__":
    unittest.main()
