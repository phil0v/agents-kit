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
        shutil.copytree(ROOT / "scripts", self.plugin / "scripts")
        self.work = self.root / "work"
        self.work.mkdir()
        self.home = self.root / "agents-home"
        self.home.mkdir()
        self.module = self.root / "module"
        self.module.mkdir()
        self.environment = dict(os.environ, AGENTS_HOME=str(self.home), AGENTS_METRICS_MODULE="",
                                GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
                                GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com",
                                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.environment.pop("AGENTS_METRICS", None)

    def run_shell_hook(self, script, command, scripts=None, **overrides):
        return subprocess.run(["bash", str((scripts or self.plugin / "scripts") / script)],
                              input=json.dumps({"cwd": str(self.work), "hook_event_name": "PreToolUse",
                                                "tool_input": {"command": command}}),
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
                        "        log_file.write(json.dumps(['record', *arguments]) + '\\n')\n")
        return str(stub)

    def install_cache_layout(self):
        scripts = self.root / "cache" / "phil0v" / "local-guards" / "0.4.0" / "scripts"
        shutil.copytree(ROOT / "scripts", scripts)
        return scripts

    def install_cache_recorder(self, version, source=RECORDER):
        target = self.root / "cache" / "phil0v" / "usage-metrics" / version / "scripts" / "metrics.py"
        target.parent.mkdir(parents=True)
        shutil.copy(source, target)
        return target

    def install_clone_layout(self):
        clone = self.root / "clone"
        scripts = clone / "plugins" / "local-guards" / "scripts"
        shutil.copytree(ROOT / "scripts", scripts)
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

    def init_repository(self):
        for arguments in (("init", "-q", "-b", "main"), ("add", "file"), ("commit", "-q", "-m", "Initial"),
                          ("branch", "feature")):
            if arguments[0] == "add":
                (self.work / "file").write_text("initial\n")
            subprocess.run(["git", "-C", str(self.work), *arguments], env=self.environment, check=True,
                           capture_output=True)

    def assert_cli_event(self, call, event, status):
        self.assertEqual(call[:7], ["record", "--plugin", "local-guards", "--event", event,
                                    "--status", status])
        self.assertEqual(len(call), 9)
        self.assertEqual(call[7], "--seconds")
        self.assertRegex(call[8], r"^\d+$")

    def run_cache_checks(self, scripts):
        denied = self.run_shell_hook("deny-git-push.sh", "git push origin main", scripts=scripts)
        self.assertEqual((denied.returncode, denied.stderr), (0, ""))
        self.assertEqual(self.deny_decision(denied)["permissionDecision"], "deny")

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_cache_layout_resolves_versioned_sibling_recorder(self):
        self.install_cache_recorder("0.2.0")
        self.run_cache_checks(self.install_cache_layout())
        events = self.events()
        self.assertEqual([event["event"] for event in events], ["deny-git-push"])
        for event in events:
            self.assert_jsonl_event(event, event["event"], "ok")
        self.assertNotIn(str(self.root), (self.home / "metrics" / "usage.jsonl").read_text())

    def test_cache_layout_prefers_the_highest_numeric_version(self):
        versions = ("0.2.0", "0.10.0", "0.9.0", "preview")
        for version in versions:
            self.install_cache_recorder(version, self.stub_recorder(version))
        self.run_cache_checks(self.install_cache_layout())
        self.assertEqual([version for version in versions if self.stub_calls(version)], ["0.10.0"])
        calls = self.stub_calls("0.10.0")
        self.assertEqual(len(calls), 1)
        self.assert_cli_event(calls[0], "deny-git-push", "ok")
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
        self.run_cache_checks(clone / "plugins" / "local-guards" / "scripts")
        events = self.events()
        self.assertEqual([event["event"] for event in events], ["deny-git-push"])
        for event in events:
            self.assert_jsonl_event(event, event["event"], "ok")
        self.assertEqual(self.stub_calls("cached"), [])

    def assert_jsonl_event(self, event, name, status):
        self.assertEqual(set(event), EVENT_FIELDS)
        self.assertEqual((event["plugin"], event["event"], event["status"]),
                         ("local-guards", name, status))
        self.assertRegex(event["ts"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(event["run"], r"^[0-9a-f]{32}$")
        self.assertIsInstance(event["seconds"], float)
        self.assertGreaterEqual(event["seconds"], 0.0)

    def assert_no_recording_side_effects(self):
        self.assertEqual(self.events(), [])
        self.assertFalse((self.home / "metrics").exists())
        self.assertEqual(self.stub_calls("stub"), [])

    def test_deny_git_push_deny_outcome_records_one_ok_cli_event(self):
        result = self.run_shell_hook("deny-git-push.sh", "git push origin main",
                                     AGENTS_METRICS_MODULE=self.stub_recorder("stub"))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        calls = self.stub_calls("stub")
        self.assertEqual(len(calls), 1)
        self.assert_cli_event(calls[0], "deny-git-push", "ok")

    def test_backup_ref_deny_outcome_records_one_ok_cli_event(self):
        result = self.run_shell_hook("backup-ref.sh", "git -C /missing/hook-metrics-repository merge feature",
                                     AGENTS_METRICS_MODULE=self.stub_recorder("stub"))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        calls = self.stub_calls("stub")
        self.assertEqual(len(calls), 1)
        self.assert_cli_event(calls[0], "backup-ref", "ok")

    def test_backup_ref_completed_backup_records_one_ok_cli_event(self):
        self.init_repository()
        result = self.run_shell_hook("backup-ref.sh", "git merge feature",
                                     AGENTS_METRICS_MODULE=self.stub_recorder("stub"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        calls = self.stub_calls("stub")
        self.assertEqual(len(calls), 1)
        self.assert_cli_event(calls[0], "backup-ref", "ok")

    def test_non_matching_command_records_one_ok_cli_event(self):
        result = self.run_shell_hook("deny-git-push.sh", "git status",
                                     AGENTS_METRICS_MODULE=self.stub_recorder("stub"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        calls = self.stub_calls("stub")
        self.assertEqual(len(calls), 1)
        self.assert_cli_event(calls[0], "deny-git-push", "ok")

    def test_unsourceable_guard_records_fail_cli_event(self):
        (self.plugin / "scripts" / "git-guard.sh").unlink()
        for script, name, reason in (("backup-ref.sh", "backup-ref", "backup-ref: git-guard.sh could not be sourced"),
                                     ("deny-git-push.sh", "deny-git-push", "git push is blocked by local-guards deny-git-push.sh")):
            with self.subTest(script=script):
                result = self.run_shell_hook(script, "git push origin main",
                                             AGENTS_METRICS_MODULE=self.stub_recorder(name))
                self.assertEqual(result.returncode, 0)
                self.assertIn("No such file or directory", result.stderr)
                decision = self.deny_decision(result)
                self.assertEqual((decision["permissionDecision"], decision["permissionDecisionReason"]),
                                 ("deny", reason))
                calls = self.stub_calls(name)
                self.assertEqual(len(calls), 1)
                self.assert_cli_event(calls[0], name, "fail")

    def test_missing_jq_records_fail_cli_event(self):
        tools = self.root / "tools"
        tools.mkdir()
        for tool in ("bash", "cat", "date"):
            (tools / tool).symlink_to(shutil.which(tool))
        (tools / "python3").symlink_to(sys.executable)
        result = self.run_shell_hook("deny-git-push.sh", "git push origin main", PATH=str(tools),
                                     AGENTS_METRICS_MODULE=self.stub_recorder("stub"))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecisionReason"],
                         "git push is blocked by local-guards deny-git-push.sh")
        calls = self.stub_calls("stub")
        self.assertEqual(len(calls), 1)
        self.assert_cli_event(calls[0], "deny-git-push", "fail")

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_adjacent_sibling_recorder_is_resolved(self):
        sibling_home = self.root / "sibling-home"
        sibling_home.mkdir()
        result = subprocess.run(["bash", str(ROOT / "scripts" / "deny-git-push.sh")],
                                input=json.dumps({"cwd": str(self.work), "hook_event_name": "PreToolUse",
                                                  "tool_input": {"command": "git push origin main"}}),
                                env=dict(self.environment, AGENTS_HOME=str(sibling_home),
                                         AGENTS_METRICS_MODULE=""),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        log = (sibling_home / "metrics" / "usage.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in log]
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "deny-git-push", "ok")
        self.assertNotIn(str(self.root), log[0])

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_shell_hook_events_append_to_usage_log(self):
        self.install_home_recorder()
        result = self.run_shell_hook("backup-ref.sh", "git -C /missing/hook-metrics-repository merge feature")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.deny_decision(result)["permissionDecision"], "deny")
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assert_jsonl_event(events[0], "backup-ref", "ok")
        line = (self.home / "metrics" / "usage.jsonl").read_text()
        self.assertNotIn(str(self.root), line)
        self.assertNotIn("/missing", line)

    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    @unittest.skipUnless(RECORDER.is_file(), "usage-metrics package is not installed alongside")
    def test_metrics_off_skips_recording_entirely(self):
        self.install_home_recorder()
        stub = self.stub_recorder("stub")
        denied = self.run_shell_hook("deny-git-push.sh", "git push origin main",
                                     AGENTS_METRICS="off", AGENTS_METRICS_MODULE=stub)
        self.assertEqual((denied.returncode, denied.stderr), (0, ""))
        self.assertEqual(self.deny_decision(denied)["permissionDecision"], "deny")
        self.assert_no_recording_side_effects()

    def test_unresolved_recorder_leaves_shell_hooks_byte_identical(self):
        for module in ("/nonexistent/hook-metrics/metrics.py", ""):
            for script, command in (("deny-git-push.sh", "git push origin main"),
                                    ("backup-ref.sh", "git -C /missing/hook-metrics-repository merge feature")):
                with self.subTest(module=module, script=script):
                    result = self.run_shell_hook(script, command, AGENTS_METRICS_MODULE=module)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    decision = self.deny_decision(result)
                    self.assertEqual(decision["permissionDecision"], "deny")
                    self.assertEqual(result.stdout.count("\n"), 1)
        result = self.run_shell_hook("deny-git-push.sh", "git status", AGENTS_METRICS_MODULE="")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assert_no_recording_side_effects()

