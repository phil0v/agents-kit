import json
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


HOOK = Path(__file__).resolve().parents[1] / "scripts/edit-checks.py"
GATE = Path(__file__).resolve().parents[1] / "scripts/style-gate.py"
EXTENSIONS = (".go", ".php", ".js", ".jsx", ".ts", ".tsx", ".vue", ".py", ".sh")
TOOLS = ("gofmt", "goimports", "golangci-lint", "git", "php-cs-fixer",
         "prettier", "eslint", "ruff", "shfmt", "shellcheck")


def load_hook(path, name="edit_checks"):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class EditCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        self.environment = dict(os.environ, PATH=str(self.bin), CHECK_LOG=str(self.log),
                                AGENTS_HOME=str(self.root / ".metrics-home"),
                                AGENTS_METRICS_MODULE="")
        self.environment.pop("AGENTS_METRICS", None)

    def load_hook(self):
        return load_hook(HOOK)

    def hook(self, patch_text, event="PreToolUse"):
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(self.root),
                              "hook_event_name": event, "tool_input": {"command": patch_text}}),
                              env=self.environment, text=True, capture_output=True, timeout=10)

    def claude_hook(self, tool_input, event="PreToolUse"):
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(self.root),
                              "hook_event_name": event, "tool_name": "Edit", "tool_input": tool_input}),
                              env=self.environment, text=True, capture_output=True, timeout=10)

    def antigravity_hook(self, tool_name, args, event="PreToolUse", flags=("--antigravity",)):
        payload = {"cwd": str(self.root), "hookEventName": event,
                   "toolCall": {"id": "toolu_01", "name": tool_name, "args": args}}
        return subprocess.run([sys.executable, str(HOOK), *flags], input=json.dumps(payload),
                              env=self.environment, text=True, capture_output=True, timeout=10)

    def stub(self, name, status=0, cwd=None):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\nimport json,os,sys\n"
                        + (f"assert os.getcwd() == {str(cwd)!r}\n" if cwd is not None else "")
                        + "with open(os.environ['CHECK_LOG'], 'a') as log:\n"
                        " log.write(json.dumps(sys.argv) + '\\n')\n"
                        f"print('diagnostic from {name}')\nsys.exit({status})\n")
        path.chmod(0o755)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_invalid_json_addition_is_denied_before_write(self):
        result = self.hook('*** Begin Patch\n*** Add File: hooks.json\n+{"hooks":\n*** End Patch')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertFalse((self.root / "hooks.json").exists())

    def test_toml_update_is_checked_against_current_content(self):
        (self.root / "config.toml").write_text('model = "old"\n')
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n-model = "old"\n+model = [\n*** End Patch')
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual((self.root / "config.toml").read_text(), 'model = "old"\n')

    def test_bare_blank_context_is_accepted(self):
        (self.root / "config.toml").write_text('enabled = true\n\nmodel = "old"\n')
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n enabled = true\n\n-model = "old"\n+model = "new"\n*** End Patch')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_bare_trailing_newline_context_is_accepted(self):
        (self.root / "config.toml").write_text('model = "old"\n')
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n-model = "old"\n+model = "new"\n\n*** End of File\n*** End Patch')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_prefixed_trailing_newline_context_is_accepted(self):
        (self.root / "config.toml").write_text('model = "old"\n')
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n-model = "old"\n+model = "new"\n \n*** End Patch')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_multiple_files_and_spaces_are_validated(self):
        (self.root / "old name.json").write_text('{}\n')
        result = self.hook('*** Begin Patch\n*** Update File: old name.json\n*** Move to: settings new.json\n@@\n-{}\n+{"ok": true}\n*** End of File\n*** Add File: config.toml\n+model = "new"\n*** Delete File: missing.json\n*** End Patch')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_invalid_moved_config_is_denied(self):
        (self.root / "old.json").write_text('{}\n')
        result = self.hook('*** Begin Patch\n*** Update File: old.json\n*** Move to: hooks.json\n@@\n-{}\n+{bad}\n*** End Patch')
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_exact_context_is_preferred_to_earlier_whitespace_match(self):
        (self.root / "config.toml").write_text('description = """\n  key = "one"\n"""\nkey = "one"\n')
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n-key = "one"\n+description = "two"\n*** End Patch')
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_end_of_file_context_matches_tail(self):
        (self.root / "config.toml").write_text('description = """\nkey = "one"\n"""\nkey = "one"\n')
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n-key = "one"\n+description = "two"\n*** End of File\n*** End Patch')
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_missing_file_is_explicitly_denied(self):
        result = self.hook('*** Begin Patch\n*** Update File: config.toml\n@@\n-missing = 1\n+missing = 2\n*** End Patch')
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_nonstandard_json_constant_is_denied(self):
        result = self.hook('*** Begin Patch\n*** Add File: hooks.json\n+{"bad": NaN}\n*** End Patch')
        self.assertIn("not valid JSON", json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_deleted_file_is_not_linted(self):
        result = self.hook('*** Begin Patch\n*** Delete File: deleted.py\n*** End Patch', "PostToolUse")
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_multiple_python_files_use_ruff_with_exact_paths(self):
        self.stub("ruff")
        (self.root / "one.py").write_text("x = 1\n")
        (self.root / "two words.py").write_text("x = 2\n")
        result = self.hook('*** Begin Patch\n*** Update File: one.py\n@@\n-x=1\n+x = 1\n*** Add File: two words.py\n+x = 2\n*** End Patch', "PostToolUse")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [[str(self.bin / "ruff"), "check", "--output-format", "concise", str(self.root / "one.py")],
                                       [str(self.bin / "ruff"), "check", "--output-format", "concise", str(self.root / "two words.py")]])

    def test_linter_failure_is_reported(self):
        self.stub("shellcheck", 1)
        (self.root / "script.sh").write_text("echo hello\n")
        result = self.hook('*** Begin Patch\n*** Add File: script.sh\n+echo hello\n*** End Patch', "PostToolUse")
        self.assertEqual(result.returncode, 2)
        self.assertIn("shellcheck exited 1", result.stderr)
        self.assertIn("diagnostic from shellcheck", result.stderr)

    def test_absent_optional_tools_do_not_fail(self):
        (self.root / "script.py").write_text("x = 1\n")
        result = self.hook('*** Begin Patch\n*** Add File: script.py\n+x = 1\n*** End Patch', "PostToolUse")
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_unreadable_pyproject_does_not_fail_the_hook(self):
        if os.getuid() == 0:
            self.skipTest("chmod blocking does not apply when running as root")
        self.stub("ruff")
        pyproject = self.root / "pyproject.toml"
        pyproject.write_text("[tool.ruff]\n")
        pyproject.chmod(0o000)
        self.addCleanup(os.chmod, pyproject, 0o644)
        (self.root / "script.py").write_text("x = 1\n")
        result = self.hook('*** Begin Patch\n*** Add File: script.py\n+x = 1\n*** End Patch', "PostToolUse")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.calls(), [[str(self.bin / "ruff"), "check", "--output-format", "concise", str(self.root / "script.py")]])

    def test_timeout_is_reported_through_hook_entrypoint(self):
        module = self.load_hook()
        self.stub("shellcheck")
        (self.root / "script.sh").write_text("echo hello\n")
        payload = json.dumps({"cwd": str(self.root), "hook_event_name": "PostToolUse",
                              "tool_input": {"command": '*** Begin Patch\n*** Add File: script.sh\n+echo hello\n*** End Patch'}})
        errors = io.StringIO()
        with patch.dict(os.environ, self.environment, clear=True), patch.object(sys, "stdin", io.StringIO(payload)), patch.object(sys, "stderr", errors), patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("shellcheck", 30)) as run:
            with self.assertRaises(SystemExit) as stopped:
                module.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("timed out", errors.getvalue())
        self.assertLessEqual(run.call_args.kwargs["timeout"], 30)

    def test_go_formatter_and_js_tools_receive_file_paths(self):
        self.stub("gofmt")
        self.stub("prettier")
        self.stub("eslint")
        (self.root / "main.go").write_text("package main\n")
        (self.root / "main.ts").write_text("const x = 1;\n")
        result = self.hook('*** Begin Patch\n*** Add File: main.go\n+package main\n*** Add File: main.ts\n+const x = 1;\n*** End Patch', "PostToolUse")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [[str(self.bin / "gofmt"), "-w", str(self.root / "main.go")],
                                       [str(self.bin / "prettier"), "--write", str(self.root / "main.ts")],
                                       [str(self.bin / "eslint"), str(self.root / "main.ts")]])

    def test_go_linter_checks_containing_package_from_its_directory(self):
        (self.root / ".git").mkdir()
        (self.root / ".golangci.yaml").write_text("version: 2\n")
        package = self.root / "nested"
        package.mkdir()
        (package / "main.go").write_text("package nested\n")
        (package / "sibling.go").write_text("package nested\n")
        self.stub("gofmt", cwd=self.root)
        self.stub("git", cwd=self.root)
        self.stub("golangci-lint", cwd=package)
        result = self.hook('*** Begin Patch\n*** Update File: nested/main.go\n@@\n-package nested\n+package nested\n*** End Patch', "PostToolUse")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.calls(), [[str(self.bin / "gofmt"), "-w", str(package / "main.go")],
                                       [str(self.bin / "git"), "rev-parse", "--verify", "-q", "HEAD"],
                                       [str(self.bin / "golangci-lint"), "run", "--new-from-rev", "HEAD", "./."]])

    def test_go_linter_skips_repository_without_head(self):
        (self.root / ".git").mkdir()
        (self.root / ".golangci.yml").write_text("version: 2\n")
        (self.root / "main.go").write_text("package main\n")
        self.stub("gofmt")
        self.stub("git", 1)
        self.stub("golangci-lint")
        result = self.hook('*** Begin Patch\n*** Add File: main.go\n+package main\n*** End Patch', "PostToolUse")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.calls(), [[str(self.bin / "gofmt"), "-w", str(self.root / "main.go")],
                                       [str(self.bin / "git"), "rev-parse", "--verify", "-q", "HEAD"]])

    def test_go_linter_requires_a_config_file(self):
        (self.root / ".golangci.yml").mkdir()
        (self.root / "main.go").write_text("package main\n")
        self.stub("gofmt")
        self.stub("git")
        self.stub("golangci-lint")
        result = self.hook('*** Begin Patch\n*** Add File: main.go\n+package main\n*** End Patch', "PostToolUse")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.calls(), [[str(self.bin / "gofmt"), "-w", str(self.root / "main.go")]])

    def test_total_budget_stops_later_commands(self):
        module = self.load_hook()
        self.stub("prettier")
        self.stub("eslint")
        (self.root / "main.ts").write_text("const x = 1;\n")
        payload = json.dumps({"cwd": str(self.root), "hook_event_name": "PostToolUse",
                              "tool_input": {"command": '*** Begin Patch\n*** Add File: main.ts\n+const x = 1;\n*** End Patch'}})
        errors = io.StringIO()
        success = subprocess.CompletedProcess([], 0, "", "")
        with patch.dict(os.environ, self.environment, clear=True), patch.object(sys, "stdin", io.StringIO(payload)), patch.object(sys, "stderr", errors), patch.object(module.time, "monotonic", side_effect=[0, 0, 91]), patch.object(module.subprocess, "run", return_value=success) as run:
            with self.assertRaises(SystemExit) as stopped:
                module.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("exceeded 90 seconds", errors.getvalue())
        self.assertEqual(run.call_count, 1)

    def test_claude_edit_denied_when_result_is_invalid_json(self):
        path = self.root / "settings.json"
        path.write_text('{"a": 1}\n')
        result = self.claude_hook({"file_path": str(path), "old_string": '"a": 1', "new_string": '"a": ['})
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual((result.returncode, decision["permissionDecision"]), (0, "deny"))
        self.assertEqual(path.read_text(), '{"a": 1}\n')

    def test_claude_write_denied_on_invalid_json_content(self):
        result = self.claude_hook({"file_path": str(self.root / "hooks.json"), "content": '{"bad": NaN}'})
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual((result.returncode, decision["permissionDecision"]), (0, "deny"))
        self.assertIn("not valid JSON", decision["permissionDecisionReason"])

    def test_claude_edit_with_absent_anchor_is_accepted(self):
        path = self.root / "settings.json"
        path.write_text('{"a": 1}\n')
        result = self.claude_hook({"file_path": str(path), "old_string": '"b": 2', "new_string": '"b": ['})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_anchored_insertion_is_placed_after_anchor(self):
        module = self.load_hook()
        current = "line 1\nline 2\nline 3\n"
        block = ["@@ line 2", "+inserted", "@@"]
        result = module.apply_hunks(current, block)
        self.assertEqual(result, "line 1\nline 2\ninserted\nline 3\n")

    def test_claude_post_edit_lints_the_written_file(self):
        self.stub("ruff")
        path = self.root / "one.py"
        path.write_text("x = 1\n")
        result = self.claude_hook({"file_path": str(path), "old_string": "x=1", "new_string": "x = 1"},
                                  "PostToolUse")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [[str(self.bin / "ruff"), "check", "--output-format", "concise", str(path)]])

    def test_claude_post_linter_failure_is_reported(self):
        self.stub("shellcheck", 1)
        path = self.root / "script.sh"
        path.write_text("echo hello\n")
        result = self.claude_hook({"file_path": str(path), "old_string": "echo", "new_string": "echo hello"},
                                  "PostToolUse")
        self.assertEqual(result.returncode, 2)
        self.assertIn("shellcheck exited 1", result.stderr)
        self.assertIn("diagnostic from shellcheck", result.stderr)

    def test_antigravity_write_file_validates_json_and_toml(self):
        hooks_path = self.root / "hooks.json"
        denied = self.antigravity_hook("write_file", {"Path": str(hooks_path), "Content": '{"bad": NaN}'})
        self.assertEqual((denied.returncode, denied.stderr), (0, ""))
        self.assertEqual(json.loads(denied.stdout)["decision"], "deny")
        self.assertIn("not valid JSON", json.loads(denied.stdout)["reason"])
        self.assertFalse(hooks_path.exists())

        allowed = self.antigravity_hook("write_file", {"Path": str(hooks_path), "Content": '{"ok": true}'})
        self.assertEqual((allowed.returncode, allowed.stderr), (0, ""))
        self.assertEqual(json.loads(allowed.stdout), {"decision": "allow"})

        toml_denied = self.antigravity_hook("write_file", {"Path": str(self.root / "config.toml"), "Content": "model = [\n"},
                                            flags=())
        self.assertEqual(toml_denied.returncode, 0)
        self.assertEqual(json.loads(toml_denied.stdout)["decision"], "deny")

    def test_antigravity_write_file_append_previews_existing_regular_file(self):
        settings = self.root / "settings.json"
        settings.write_text('{"a": 1}\n')
        denied = self.antigravity_hook("write_file", {"Path": str(settings), "Content": ', "b": 2}', "Append": True})
        self.assertEqual(json.loads(denied.stdout)["decision"], "deny")

        settings.write_text('{"a": 1')
        allowed = self.antigravity_hook("write_file", {"Path": str(settings), "Content": '}\n', "Append": True})
        self.assertEqual(json.loads(allowed.stdout), {"decision": "allow"})

        replaced = self.antigravity_hook("write_file", {"Path": str(settings), "Content": '{"a": 2}\n', "Append": False})
        self.assertEqual(json.loads(replaced.stdout), {"decision": "allow"})

        missing = self.root / "hooks.json"
        missing_denied = self.antigravity_hook("write_file", {"Path": str(missing), "Content": "{bad}", "Append": True})
        self.assertEqual(json.loads(missing_denied.stdout)["decision"], "deny")
        missing_allowed = self.antigravity_hook("write_file", {"Path": str(missing), "Content": "{}", "Append": True})
        self.assertEqual(json.loads(missing_allowed.stdout), {"decision": "allow"})

    def test_antigravity_edit_file_replaces_every_occurrence_and_skips_missing(self):
        config = self.root / "config.toml"
        config.write_text("a = 1\nb = 1\n")
        denied = self.antigravity_hook("edit_file", {"Path": str(config), "Target": "= 1", "Replacement": "= ["})
        self.assertEqual(json.loads(denied.stdout)["decision"], "deny")
        self.assertEqual(config.read_text(), "a = 1\nb = 1\n")

        allowed = self.antigravity_hook("edit_file", {"Path": str(config), "Target": "= 1", "Replacement": "= 2"})
        self.assertEqual(json.loads(allowed.stdout), {"decision": "allow"})

        absent = self.antigravity_hook("edit_file", {"Path": str(config), "Target": "missing", "Replacement": "= ["})
        self.assertEqual(json.loads(absent.stdout), {"decision": "allow"})

        missing = self.antigravity_hook("edit_file", {"Path": str(self.root / "hooks.json"),
                                                      "Target": "x", "Replacement": "["})
        self.assertEqual(json.loads(missing.stdout), {"decision": "allow"})

    def test_antigravity_malformed_payload_emits_antigravity_deny(self):
        result = subprocess.run([sys.executable, str(HOOK), "--antigravity"], input="{bad",
                                env=self.environment, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["decision"], "deny")

    def test_antigravity_non_object_payload_emits_antigravity_deny(self):
        for raw_payload in ("null", "[]", "123"):
            with self.subTest(payload=raw_payload):
                result = subprocess.run([sys.executable, str(HOOK), "--antigravity"], input=raw_payload,
                                        env=self.environment, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0)
                decision = json.loads(result.stdout)
                self.assertEqual(decision["decision"], "deny")
                self.assertIn("hook payload must be an object", decision["reason"])

    def test_antigravity_relative_path_resolves_against_payload_cwd(self):
        settings = self.root / "settings.json"
        settings.write_text('{"a": 1')
        edit_denied = self.antigravity_hook("edit_file",
                                            {"Path": "settings.json", "Target": '"a": 1', "Replacement": '"a": ['})
        self.assertEqual(edit_denied.returncode, 0)
        self.assertEqual(json.loads(edit_denied.stdout)["decision"], "deny")

        append_allowed = self.antigravity_hook("write_file",
                                               {"Path": "settings.json", "Content": '}\n', "Append": True})
        self.assertEqual(append_allowed.returncode, 0)
        self.assertEqual(json.loads(append_allowed.stdout), {"decision": "allow"})

        write_denied = self.antigravity_hook("write_file", {"Path": "hooks.json", "Content": "{bad}"})
        self.assertEqual(write_denied.returncode, 0)
        self.assertEqual(json.loads(write_denied.stdout)["decision"], "deny")

    def test_antigravity_invalid_tool_call_and_args_emit_deny(self):
        cases = (
            ("write_file", {"Path": str(self.root / "hooks.json"), "Content": "{}", "Append": "true"}),
            ("write_file", {"Path": str(self.root / "hooks.json")}),
            ("write_file", {"Content": "{}"}),
            ("edit_file", {"Path": str(self.root / "hooks.json"), "Target": "a"}),
            ("write_file", ["not", "a", "dict"]),
        )
        for tool_name, args in cases:
            with self.subTest(tool_name=tool_name, args=args):
                result = self.antigravity_hook(tool_name, args)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["decision"], "deny")

        non_dict_tool_call = subprocess.run(
            [sys.executable, str(HOOK), "--antigravity"],
            input=json.dumps({"cwd": str(self.root), "hookEventName": "PreToolUse", "toolCall": "bad"}),
            env=self.environment, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(non_dict_tool_call.returncode, 0)
        self.assertEqual(json.loads(non_dict_tool_call.stdout)["decision"], "deny")

    def test_antigravity_post_tool_use_emits_empty_object_and_exits_zero(self):
        self.stub("ruff")
        py_path = self.root / "one.py"
        py_path.write_text("x = 1\n")
        ok = self.antigravity_hook("edit_file", {"Path": str(py_path), "Target": "x=1", "Replacement": "x = 1"},
                                   "PostToolUse")
        self.assertEqual((ok.returncode, ok.stderr), (0, ""))
        self.assertEqual(json.loads(ok.stdout), {})
        self.assertEqual(self.calls(), [[str(self.bin / "ruff"), "check", "--output-format", "concise", str(py_path)]])

        self.stub("shellcheck", 1)
        sh_path = self.root / "script.sh"
        sh_path.write_text("echo hello\n")
        diag = self.antigravity_hook("write_file", {"Path": str(sh_path), "Content": "echo hello\n"}, "PostToolUse")
        self.assertEqual(diag.returncode, 0)
        self.assertEqual(json.loads(diag.stdout), {})
        self.assertIn("shellcheck exited 1", diag.stderr)
        self.assertIn("diagnostic from shellcheck", diag.stderr)

        long_tool = self.bin / "shellcheck"
        long_tool.write_text(f"#!{sys.executable}\nimport sys\nprint('x' * 15000)\nsys.exit(1)\n")
        long_tool.chmod(0o755)
        truncated = self.antigravity_hook("write_file", {"Path": str(sh_path), "Content": "echo hello\n"}, "PostToolUse")
        self.assertEqual(truncated.returncode, 0)
        self.assertEqual(json.loads(truncated.stdout), {})
        self.assertEqual(len(truncated.stderr.rstrip("\n")), 12000)

        bad_post = self.antigravity_hook("unknown_tool", {"Path": str(sh_path)}, "PostToolUse")
        self.assertEqual(bad_post.returncode, 0)
        self.assertEqual(json.loads(bad_post.stdout), {})
        self.assertIn("Edit checks:", bad_post.stderr)


class SharedTableTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".git").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in TOOLS:
            tool = self.bin / name
            tool.write_text("#!/bin/sh\nexit 0\n")
            tool.chmod(0o755)

    def resolve(self, commands):
        resolved = {}
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            for extension in EXTENSIONS:
                path = self.root / f"file{extension}"
                path.write_text("x\n")
                resolved[extension] = list(commands(path, self.root))
        return resolved

    def isolated_hook(self):
        layout = self.root / "standalone" / "scripts"
        layout.mkdir(parents=True)
        copy = layout / "edit-checks.py"
        copy.write_text(HOOK.read_text())
        return load_hook(copy, "edit_checks_standalone")

    def test_shared_table_is_used_when_the_sibling_gate_is_resolvable(self):
        module = load_hook(HOOK)
        self.assertIsNot(module.commands, module._embedded_commands)
        self.assertEqual(module.commands.__module__, "code_flow_style_gate")

    def test_fallback_table_is_used_without_the_sibling_gate(self):
        module = self.isolated_hook()
        self.assertIs(module.commands, module._embedded_commands)

    def test_loadable_gate_without_commands_falls_back(self):
        layout = self.root / "skewed" / "scripts"
        layout.mkdir(parents=True)
        (layout / "style-gate.py").write_text("TABLE = {}\n")
        hook = layout / "edit-checks.py"
        hook.write_text(HOOK.read_text())
        module = load_hook(hook, "edit_checks_skewed")
        self.assertIs(module.commands, module._embedded_commands)

    def test_shared_and_fallback_resolve_identical_commands(self):
        (self.root / ".editorconfig").write_text("[*]\nindent_style = space\n")
        (self.root / ".golangci.yml").write_text("version: 2\n")
        (self.root / "pyproject.toml").write_text("[tool.ruff]\n")
        shared = load_hook(HOOK)
        fallback = self.isolated_hook()
        expected = self.resolve(shared.commands)
        self.assertEqual(expected, self.resolve(fallback.commands))
        self.assertTrue(all(expected.values()))

    def test_repo_conditional_branches_resolve_identically(self):
        (self.bin / "goimports").unlink()
        (self.root / "pyproject.toml").write_text("[tool.black]\n")
        shared = load_hook(HOOK)
        fallback = self.isolated_hook()
        expected = self.resolve(shared.commands)
        self.assertEqual(expected, self.resolve(fallback.commands))
        self.assertEqual(len(expected[".go"]), 1)
        self.assertEqual(len(expected[".py"]), 1)
        self.assertEqual(len(expected[".sh"]), 1)
        self.assertEqual(expected[".go"][0][0], str(self.bin / "gofmt"))

    def test_shared_commands_match_the_gate_directly(self):
        gate = load_hook(GATE, "code_flow_style_gate_direct")
        shared = load_hook(HOOK)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            for extension in EXTENSIONS:
                path = self.root / f"file{extension}"
                path.write_text("x\n")
                self.assertEqual(list(shared.commands(path, self.root)),
                                 list(gate.commands(path, self.root)))

