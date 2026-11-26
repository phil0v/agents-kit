import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PluginHookTests(unittest.TestCase):
    def test_manifests_declare_one_hook_file_per_harness(self):
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(claude["name"], "code-flow")
        self.assertEqual({claude["version"], codex["version"]}, {"0.1.1"})
        self.assertNotIn("$schema", codex)
        self.assertEqual((claude["hooks"], codex["hooks"]),
                         ("./hooks/claude.json", "./hooks/codex.json"))
        for name in (claude["hooks"], codex["hooks"]):
            self.assertTrue((ROOT / name).is_file())
        self.assertEqual(list(json.loads((ROOT / "hooks.json").read_text())), ["code-flow"])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="automation plugin ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.plugin = self.root / "installed plugin"
        shutil.copytree(ROOT / "hooks", self.plugin / "hooks")
        shutil.copytree(ROOT / "scripts", self.plugin / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "hooks.json", self.plugin / "hooks.json")
        self.work = self.root / "work tree"
        self.work.mkdir()
        self.environment = dict(os.environ, PLUGIN_ROOT=str(self.plugin), CLAUDE_PLUGIN_ROOT=str(self.plugin),
                                AGENTS_HOME=str(self.root / "metrics home"), AGENTS_METRICS_MODULE="")
        self.environment.pop("AGENTS_METRICS", None)
        self.environment["PATH"] = os.pathsep.join([str(self.bin), self.environment["PATH"]])
        self.claude_hooks = json.loads((self.plugin / "hooks/claude.json").read_text())["hooks"]
        self.codex_hooks = json.loads((self.plugin / "hooks/codex.json").read_text())["hooks"]
        self.antigravity_hooks = json.loads((self.plugin / "hooks.json").read_text())["code-flow"]

    def run_codex_hook(self, event, patch_text):
        return self.run_configured(self.codex_hooks, event, self.work,
                                   {"cwd": str(self.work), "hook_event_name": event,
                                    "tool_input": {"command": patch_text}}, "^apply_patch$")

    def run_claude_hook(self, event, tool_input, tool_name="Edit"):
        return self.run_configured(self.claude_hooks, event, self.work,
                                   {"cwd": str(self.work), "hook_event_name": event,
                                    "tool_name": tool_name, "tool_input": tool_input}, "Edit|Write")

    def run_antigravity_hook(self, event, tool_name, args):
        return self.run_configured(self.antigravity_hooks, event, self.plugin,
                                   {"cwd": str(self.work), "hookEventName": event,
                                    "toolCall": {"id": "toolu_01", "name": tool_name, "args": args}},
                                   "write_file|edit_file")

    def run_configured(self, hooks, event, cwd, payload, matcher):
        self.assertEqual([group["matcher"] for group in hooks[event]], [matcher])
        self.assertEqual(len(hooks[event]), 1)
        configured = hooks[event][0]["hooks"]
        self.assertEqual(len(configured), 1)
        self.assertEqual(configured[0]["timeout"], {"PreToolUse": 30, "PostToolUse": 120}[event])
        return subprocess.run(["bash", "-c", configured[0]["command"]], cwd=cwd,
                              env=self.environment, input=json.dumps(payload),
                              text=True, capture_output=True, timeout=10)

    def test_claude_edit_checks_deny_invalid_json_edit(self):
        path = self.work / "settings.json"
        path.write_text('{"a": 1}\n')
        result = self.run_claude_hook("PreToolUse",
                                      {"file_path": str(path), "old_string": '"a": 1', "new_string": '"a": ['})
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual((result.returncode, decision["permissionDecision"]), (0, "deny"))
        self.assertEqual(path.read_text(), '{"a": 1}\n')

    def test_claude_post_edit_reports_invalid_config(self):
        (self.work / "config.toml").write_text("model = [\n")
        result = self.run_claude_hook("PostToolUse",
                                      {"file_path": str(self.work / "config.toml"), "content": "model = [\n"}, "Write")
        self.assertEqual(result.returncode, 2)
        self.assertIn("config.toml", result.stderr)

    def test_codex_edit_checks_deny_invalid_json_patch(self):
        result = self.run_codex_hook("PreToolUse", '*** Begin Patch\n*** Add File: hooks.json\n+{bad}\n*** End Patch')
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_codex_post_edit_reports_invalid_config(self):
        (self.work / "config.toml").write_text("model = [\n")
        result = self.run_codex_hook("PostToolUse", '*** Begin Patch\n*** Add File: config.toml\n+model = [\n*** End Patch')
        self.assertEqual(result.returncode, 2)
        self.assertIn("config.toml", result.stderr)

    def test_antigravity_edit_checks_hooks_validate_and_post_emit_empty_object(self):
        settings = self.work / "settings.json"
        settings.write_text('{"a": 1}\n')
        denied = self.run_antigravity_hook("PreToolUse", "write_file", {"Path": str(settings), "Content": "{bad}"})
        self.assertEqual((denied.returncode, denied.stderr), (0, ""))
        self.assertEqual(json.loads(denied.stdout)["decision"], "deny")
        allowed = self.run_antigravity_hook("PreToolUse", "edit_file",
                                            {"Path": str(settings), "Target": '"a": 1', "Replacement": '"a": 2'})
        self.assertEqual((allowed.returncode, allowed.stderr), (0, ""))
        self.assertEqual(json.loads(allowed.stdout), {"decision": "allow"})
        config = self.work / "config.toml"
        config.write_text("model = [\n")
        post = self.run_antigravity_hook("PostToolUse", "write_file", {"Path": str(config), "Content": "model = [\n"})
        self.assertEqual(post.returncode, 0)
        self.assertEqual(json.loads(post.stdout), {})
        self.assertIn("config.toml", post.stderr)


if __name__ == "__main__":
    unittest.main()
