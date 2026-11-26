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
    def test_compatibility_manifest_exposes_hooks_without_portable_shadow(self):
        self.assertFalse((ROOT / "plugin.json").exists())
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(manifest["name"], "local-guards")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertNotIn("$schema", manifest)
        paths = [
            manifest["skills"],
            manifest["hooks"],
            manifest["extensions"]["com.openai"]["onboardingSkill"],
        ]
        self.assertEqual(
            paths, ["./skills/", "./hooks/codex.json", "./skills/setup/SKILL.md"]
        )
        for name in paths:
            with self.subTest(path=name):
                resolved = (ROOT / name).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT.resolve()))
                self.assertTrue(resolved.exists())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="automation plugin ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.plugin = self.root / "installed plugin"
        shutil.copytree(ROOT / "hooks", self.plugin / "hooks")
        shutil.copytree(ROOT / "scripts", self.plugin / "scripts")
        shutil.copytree(ROOT / "skills", self.plugin / "skills")
        shutil.copy2(ROOT / "hooks.json", self.plugin / "hooks.json")
        self.work = self.root / "work tree"
        self.work.mkdir()
        self.codex = self.root / "codex home"
        self.codex.mkdir()
        self.environment = dict(
            os.environ,
            PLUGIN_ROOT=str(self.plugin),
            CLAUDE_PLUGIN_ROOT=str(self.plugin),
            CODEX_HOME=str(self.codex),
            AGENTS_HOME=str(self.root / "metrics home"),
            AGENTS_METRICS_MODULE="",
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.com",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
        )
        self.environment.pop("AGENTS_METRICS", None)
        self.environment["PATH"] = os.pathsep.join(
            [str(self.bin), self.environment["PATH"]]
        )
        self.hooks = json.loads((self.plugin / "hooks/codex.json").read_text())["hooks"]
        self.claude_hooks = json.loads((self.plugin / "hooks/claude.json").read_text())[
            "hooks"
        ]
        self.antigravity_hooks = json.loads((self.plugin / "hooks.json").read_text())[
            "local-guards"
        ]

    def run_hook(self, event, group, handler, command):
        configured = self.hooks[event][group]["hooks"][handler]
        return subprocess.run(
            ["bash", "-c", configured["command"]],
            cwd=self.work,
            env=self.environment,
            input=json.dumps(
                {
                    "cwd": str(self.work),
                    "hook_event_name": event,
                    "tool_input": {"command": command},
                }
            ),
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_claude_hook(self, event, group, handler, tool_input, tool_name="Edit"):
        configured = self.claude_hooks[event][group]["hooks"][handler]
        return subprocess.run(
            ["bash", "-c", configured["command"]],
            cwd=self.work,
            env=self.environment,
            input=json.dumps(
                {
                    "cwd": str(self.work),
                    "hook_event_name": event,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                }
            ),
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_antigravity_hook(self, event, group, handler, tool_name, args, env=None):
        configured = self.antigravity_hooks[event][group]["hooks"][handler]
        payload = {
            "cwd": str(self.work),
            "hookEventName": event,
            "toolCall": {"id": "toolu_01", "name": tool_name, "args": args},
        }
        return subprocess.run(
            ["bash", "-c", configured["command"]],
            cwd=self.plugin,
            env=self.environment if env is None else env,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
        )

    def setup_check(self):
        return subprocess.run(
            ["python3", str(self.plugin / "skills/setup/scripts/check.py")],
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_packaged_push_command_denies_push_with_space_in_plugin_path(self):
        result = self.run_hook("PreToolUse", 0, 0, "git push origin main")
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(
            (result.returncode, decision["permissionDecision"]), (0, "deny")
        )
        self.assertIn("local-guards", decision["permissionDecisionReason"])

    def test_packaged_push_guard_denies_evaluated_push(self):
        result = self.run_hook("PreToolUse", 0, 0, 'bash -c "git push origin main"')
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_packaged_guards_accept_quoted_python_text_edits(self):
        code = (
            "from pathlib import Path\n"
            "path = Path('notes.md')\n"
            "old = '`git grep -nF`'\n"
            "path.write_text(path.read_text().replace(old, 'search'))\n"
        )
        heredoc = "python3 - <<'PY'\n" + code + "PY"
        suffixes = (
            "",
            "\ngit diff --check",
            "\ndiff -u .agents/skills/apply-facts/SKILL.md /tmp/result/SKILL.md",
        )
        for suffix in suffixes:
            for hook in (0, 1):
                with self.subTest(suffix=suffix, hook=hook):
                    result = self.run_hook("PreToolUse", 0, hook, heredoc + suffix)
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr), (0, "", "")
                    )

    def test_packaged_push_guard_denies_push_after_quoted_python_text(self):
        command = "python3 - <<'PY'\nprint('data')\nPY\ngit push origin main"
        result = self.run_hook("PreToolUse", 0, 0, command)
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_packaged_push_guard_denies_executable_heredoc(self):
        commands = (
            (
                "python3 - <<'PY'\nimport subprocess\n"
                "subprocess.run(['git', 'push', 'origin', 'main'])\nPY"
            ),
            "python3 - <<PY\nprint('$(git push origin main)')\nPY",
            "python3 - <<'PY' | bash\nprint('git push origin main')\nPY",
            "python3 - <<'PY' < script.py\nprint('git push')\nPY",
            (
                "python3 - <<'PY'\nprint = __builtins__.__dict__['exec']\n"
                "print(opaque_payload)\nPY\ngit diff --check"
            ),
            (
                "python3 - <<'PY'\nprint = exec\n"
                "print(opaque_payload)\nPY\ngit diff --check"
            ),
        )
        for command in commands:
            with self.subTest(command=command):
                result = self.run_hook("PreToolUse", 0, 0, command)
                self.assertEqual(
                    json.loads(result.stdout)["hookSpecificOutput"][
                        "permissionDecision"
                    ],
                    "deny",
                )

    def test_packaged_guards_deny_python_with_local_import_shadowing(self):
        (self.work / "pathlib.py").touch()
        command = (
            "python3 - <<'PY'\nfrom pathlib import Path\n"
            "print(Path('notes.md'))\nPY\ngit diff --check"
        )
        for hook in (0, 1):
            with self.subTest(hook=hook):
                result = self.run_hook("PreToolUse", 0, hook, command)
                self.assertEqual(
                    json.loads(result.stdout)["hookSpecificOutput"][
                        "permissionDecision"
                    ],
                    "deny",
                )

    def test_guard_bootstrap_failure_emits_deny_reason_after_return(self):
        script = self.plugin / "scripts" / "guard-bootstrap-regression.sh"
        script.write_text(
            '#!/usr/bin/env bash\n'
            'set -uo pipefail\n'
            'source "$(dirname "$0")/git-guard.sh" || exit 2\n'
            'guard_bootstrap "push" "no-jq-reason" "expected-reason" || exit 0\n'
            'exit 1\n'
        )
        result = subprocess.run(
            ["bash", str(script)],
            cwd=self.work,
            env=self.environment,
            input=json.dumps(
                {
                    "cwd": str(self.work),
                    "tool_input": {"command": "git push origin main"},
                }
            ),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertEqual(decision["permissionDecisionReason"], "expected-reason")
        self.assertNotIn("unbound variable", result.stderr)

    def test_may_trigger_ignores_git_metadata_paths_without_jq(self):
        script = self.plugin / "scripts" / "may-trigger-regression.sh"
        script.write_text(
            '#!/usr/bin/env bash\n'
            'source "$(dirname "$0")/git-guard.sh" || exit 2\n'
            'payload="{\\"git_dir\\":\\"/tmp/repo/.git\\",\\"branch\\":\\"merge\\"}"\n'
            'may_trigger "merge" "$(unescape_json "$payload")" && exit 1\n'
            'may_trigger "merge" "git merge feature" || exit 1\n'
            'exit 0\n'
        )
        result = subprocess.run(
            ["bash", str(script)],
            cwd=self.work,
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_packaged_push_guard_allows_read(self):
        result = self.run_hook("PreToolUse", 0, 0, "git status")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_packaged_guards_allow_non_evaluator_mentions_and_script_calls(self):
        commands = (
            "echo bash git",
            "echo bash git && git branch --list",
            "echo python3 && git diff",
            "echo python3 && git merge --abort",
            "cat <<'EOF'\ngit push origin main\nEOF",
            "cat <<'EOF'\nbash -c 'git push origin main'\nEOF",
            "python3 script.py",
            "python3 script.py && git branch --list",
            "bash script.sh",
            "bash script.sh && git branch --list",
            "python3 -m unittest discover -s tests && git branch --list",
            "python3 script.py <<'DATA'\ngit push origin main\nDATA",
            "python3 script.py <<'DATA' && git diff\ngit push origin main\nDATA",
            "bash script.sh <<'DATA'\ngit push origin main\nDATA",
            "echo data | python3 script.py && git branch --list",
            "python3 script.py | grep ok && git branch --list",
            "command -v bash && git branch --list",
            "bash -eu script.sh && git branch --list",
            "bash -eo errexit script.sh && git branch --list",
            "bash +eO extglob script.sh && git branch --list",
            "python3 -W ignore script.py && git branch --list",
            "python3 -um unittest discover -s tests && git branch --list",
            "perl -Ilib/core script.pl && git branch --list",
            "node --no-warnings script.js && git branch --list",
        )
        for command in commands:
            for handler in (0, 1):
                with self.subTest(command=command, handler=handler):
                    result = self.run_hook("PreToolUse", 0, handler, command)
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr), (0, "", "")
                    )
                    antigravity = self.run_antigravity_hook(
                        "PreToolUse",
                        0,
                        handler,
                        "run_command",
                        {"CommandLine": command},
                    )
                    self.assertEqual(
                        (antigravity.returncode, antigravity.stderr), (0, "")
                    )
                    self.assertEqual(
                        json.loads(antigravity.stdout), {"decision": "allow"}
                    )

    def test_packaged_guards_deny_inline_evaluators_and_guarded_compound_segments(self):
        commands = (
            "source helper.sh && git branch --list",
            ". ./helper.sh && git branch --list",
            "alias gp='git branch --list' && gp",
            "eval 'git branch --list'",
            "env -S 'git branch --list'",
            "ssh host git branch --list",
            "su -c 'git branch --list'",
            "watch git branch --list",
            "osascript -e 'do shell script \"git branch\"'",
            "bash -c 'git branch --list'",
            "bash -lc 'git branch --list'",
            "sh -xc 'git branch --list'",
            "bash -o errexit -c 'git branch --list'",
            "bash +x -c 'git branch --list'",
            "bash -O extglob -c 'git branch --list'",
            "bash +O extglob -c 'git branch --list'",
            "bash -eO extglob -c 'git branch --list'",
            "bash +eO extglob -c 'git branch --list'",
            "bash -eo errexit -c 'git branch --list'",
            "sh -eo errexit -c 'git branch --list'",
            "fish --command 'git branch --list'",
            "fish --command='git branch --list'",
            "fish --command='echo ok' arg1 && git branch --list",
            "fish -NC 'git branch --list'",
            "python3 -W ignore -c 'print(1)' && git branch --list",
            "python3 -uW ignore -c 'print(1)' && git branch --list",
            "python3 -uX dev -c 'print(1)' && git branch --list",
            "python3 -ucm 'print(1)' && git branch --list",
            "python3 -c 'print(1)' && git branch --list",
            "perl -e 'system(\"git branch\")'",
            "perl -E 'system(\"git branch\")'",
            "perl -ne 'print' && git branch --list",
            "perl -wI /tmp -e 'system(\"git branch\")'",
            "ruby -r open3 script.rb && git branch --list",
            "ruby -wr open3 -e 'system(\"git branch\")'",
            "ruby -wI /tmp -e 'system(\"git branch\")'",
            "node -p '1+1' && git branch --list",
            "node --eval '1+1' && git branch --list",
            "node --print '1+1' && git branch --list",
            "node --input-type module -e 'console.log(1)' && git branch --list",
            "node --diagnostic-dir /tmp -e 'console.log(1)' && git branch --list",
            "node --allow-fs-read /tmp -e 'console.log(1)' && git branch --list",
            "node --report-dir /tmp -e 'console.log(1)' && git branch --list",
            "node --no-warnings -e 'console.log(1)' && git branch --list",
            "node --watch -e 'console.log(1)' && git branch --list",
            "node --import 'data:text/javascript,execSync(\"git branch --list\")' script.js",
            "node --import 'data:text/javascript,console.log(1)' script.js && git branch --list",
            "node --import=data:text/javascript,console.log(1) script.js && git branch --list",
            "node --loader data:text/javascript,1 script.js && git branch --list",
            "node --loader=data:text/javascript,1 script.js && git branch --list",
            "node --experimental-loader data:text/javascript,1 script.js && git branch --list",
            "node --experimental-loader=data:text/javascript,1 script.js && git branch --list",
            "bash -s <<'SH'\ngit push origin main\nSH",
            "python3 - <<'PY'\nimport subprocess\nPY\ngit diff",
            "python3 <<'PY'\nprint('git push')\nPY",
            "bash <<'SH'\ngit push origin main\nSH",
            "python3 \"$SCRIPT\" && git branch --list",
            "python3 \"$OPT\" script.py && git branch --list",
            "python3 -m \"$MOD\" && git branch --list",
            "python3 \"$SCRIPT\" <<'DATA'\ngit push origin main\nDATA",
            "echo 'git push origin main' | python3",
            "echo 'git push origin main' | bash -s",
            "echo 'git push origin main' | python3 -",
        )
        for command in commands:
            self.assert_guards_deny(command)
        for command in (
            "python3 script.py && git push origin main",
            "echo bash git && git push origin main",
        ):
            with self.subTest(command=command):
                push_guard = self.run_hook("PreToolUse", 0, 0, command)
                self.assertEqual((push_guard.returncode, push_guard.stderr), (0, ""))
                self.assertEqual(
                    json.loads(push_guard.stdout)["hookSpecificOutput"][
                        "permissionDecision"
                    ],
                    "deny",
                )
                backup_guard = self.run_hook("PreToolUse", 0, 1, command)
                self.assertEqual(
                    (backup_guard.returncode, backup_guard.stdout, backup_guard.stderr),
                    (0, "", ""),
                )

    def git(self, *arguments, cwd=None):
        return subprocess.check_output(
            ["git", *arguments], cwd=cwd or self.work, env=self.environment, text=True
        ).strip()

    def assert_guards_deny(self, command):
        for handler in (0, 1):
            with self.subTest(command=command, handler=handler):
                result = self.run_hook("PreToolUse", 0, handler, command)
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(
                    json.loads(result.stdout)["hookSpecificOutput"][
                        "permissionDecision"
                    ],
                    "deny",
                )

    def test_packaged_guards_distinguish_evaluator_deny_reason_from_unparsed_git(self):
        evaluator_commands = (
            "python3 - <<'PY'\nimport subprocess\nPY\ngit diff",
            "python3 -c 'print(1)' && git status",
        )
        for handler in (0, 1):
            baseline = self.run_hook("PreToolUse", 0, handler, "git -c x=y status")
            self.assertEqual((baseline.returncode, baseline.stderr), (0, ""))
            baseline_decision = json.loads(baseline.stdout)["hookSpecificOutput"]
            self.assertEqual(baseline_decision["permissionDecision"], "deny")
            for command in evaluator_commands:
                with self.subTest(command=command, handler=handler):
                    result = self.run_hook("PreToolUse", 0, handler, command)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    decision = json.loads(result.stdout)["hookSpecificOutput"]
                    self.assertEqual(decision["permissionDecision"], "deny")
                    self.assertNotEqual(
                        decision["permissionDecisionReason"],
                        baseline_decision["permissionDecisionReason"],
                    )

    def test_packaged_guards_resolve_single_literal_command_variable(self):
        for command in (
            "PY=/usr/bin/python3; $PY script.py; git status",
            'PY=/usr/bin/python3; "$PY" script.py; git status',
            "PY=/usr/bin/python3; ${PY} script.py; git status",
        ):
            for handler in (0, 1):
                with self.subTest(command=command, handler=handler):
                    result = self.run_hook("PreToolUse", 0, handler, command)
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr), (0, "", "")
                    )

        push_reason = json.loads(
            self.run_hook("PreToolUse", 0, 0, "git push origin main").stdout
        )["hookSpecificOutput"]["permissionDecisionReason"]
        unparsed_push_reason = json.loads(
            self.run_hook("PreToolUse", 0, 0, "git -c x=y status").stdout
        )["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertNotEqual(push_reason, unparsed_push_reason)

        for command in (
            "G=git; $G push origin main",
            "G=git; ${G} push origin main",
            'G=git; "$G" push origin main',
        ):
            with self.subTest(command=command):
                result = self.run_hook("PreToolUse", 0, 0, command)
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                decision = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "deny")
                self.assertEqual(decision["permissionDecisionReason"], push_reason)

        for handler in (0, 1):
            evaluator_reason = json.loads(
                self.run_hook(
                    "PreToolUse", 0, handler, "python3 -c 'print(1)' && git status"
                ).stdout
            )["hookSpecificOutput"]["permissionDecisionReason"]
            unparsed_reason = json.loads(
                self.run_hook("PreToolUse", 0, handler, "git -c x=y status").stdout
            )["hookSpecificOutput"]["permissionDecisionReason"]
            result = self.run_hook(
                "PreToolUse",
                0,
                handler,
                "PY=/usr/bin/python3; $PY -c 'print(1)'; git status",
            )
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            decision = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertEqual(decision["permissionDecisionReason"], evaluator_reason)
            self.assertNotEqual(decision["permissionDecisionReason"], unparsed_reason)

    def test_packaged_guards_keep_unparsed_denial_when_variable_conditions_fail(self):
        commands = (
            "[ -n x ] && G=git; $G branch --list",
            "G=git; G=echo; $G branch --list",
            "G=git; read G; $G branch --list",
            "G=$X; $G branch --list; git status",
            "G='git push'; $G origin main",
            "IFS=x; G=gitxpush; $G origin; git status",
            "G=echo; eval G=git; $G push origin",
            "G=git; export G; $G branch --list",
            "if true; then G=echo; fi; $G branch --list; git status",
            "(G=git); $G branch --list",
            "f() { :; }; G=git; $G branch --list",
            "$G branch --list; G=git; git status",
            "C=cat\n$C <<'EOF'\nG=echo\nEOF\n$G branch --list; git status",
            "C=cat\n$C <<'EOF'\nG=echo\nEOF\n$G push origin main; git status",
        )
        for handler in (0, 1):
            unparsed_reason = json.loads(
                self.run_hook("PreToolUse", 0, handler, "git -c x=y status").stdout
            )["hookSpecificOutput"]["permissionDecisionReason"]
            for command in commands:
                with self.subTest(command=command, handler=handler):
                    result = self.run_hook("PreToolUse", 0, handler, command)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    decision = json.loads(result.stdout)["hookSpecificOutput"]
                    self.assertEqual(decision["permissionDecision"], "deny")
                    self.assertEqual(
                        decision["permissionDecisionReason"], unparsed_reason
                    )

    def test_packaged_guards_reject_configured_aliases(self):
        self.git("init", "-q", "-b", "main")
        for name, expansion in (
            ("publish", "push"),
            ("rewrite", "rebase main"),
            ("integrate", "merge feature"),
            ("remove", "branch -D feature"),
            ("shell", "!git push origin main"),
            ("chain", "publish"),
        ):
            self.git("config", f"alias.{name}", expansion)
            self.assert_guards_deny(f"git {name}")
        self.git("config", "alias.status", "!git push origin main")
        for handler in (0, 1):
            result = self.run_hook(
                "PreToolUse", 0, handler, "git status --short; git log -1"
            )
            self.assertEqual(
                (result.returncode, result.stdout, result.stderr), (0, "", "")
            )

    def test_packaged_guards_reject_configuration_option_forms(self):
        for command in (
            "git -calias.publish=push publish origin main",
            "git -c alias.publish=push publish origin main",
            "git --config-env=alias.publish=PUBLISH publish origin main",
            "git --config-env alias.publish=PUBLISH publish origin main",
            "git -calias.rewrite=rebase rewrite main",
            "git -c core.pager=cat status",
        ):
            self.assert_guards_deny(command)

    def test_packaged_guards_inspect_alias_in_effective_repository(self):
        self.git("init", "-q", "-b", "main")
        other = self.work / "other"
        other.mkdir()
        self.git("init", "-q", "-b", "main", cwd=other)
        self.git("config", "alias.publish", "push", cwd=other)
        for command in (
            "git -C other publish origin main",
            "git -Cother publish origin main",
            "git -C other -C .. -C other publish origin main",
            "cd other && git publish origin main",
            "git --git-dir=other/.git publish origin main",
            "git --git-dir other/.git publish origin main",
        ):
            self.assert_guards_deny(command)
        for handler in (0, 1):
            result = self.run_hook(
                "PreToolUse", 0, handler, "git -C other -C .. publish"
            )
            self.assertEqual(
                (result.returncode, result.stdout, result.stderr), (0, "", "")
            )

    def test_packaged_guards_reject_global_shell_alias(self):
        configuration = self.root / "global.gitconfig"
        configuration.write_text("[alias]\n\tpublish = !git push origin main\n")
        self.environment["GIT_CONFIG_GLOBAL"] = str(configuration)
        self.assert_guards_deny("git publish")

    def test_packaged_backup_uses_attached_directory_in_target_repository(self):
        self.git("init", "-q", "-b", "main")
        other = self.work / "other"
        other.mkdir()
        self.git("init", "-q", "-b", "main", cwd=other)
        for repository in (self.work, other):
            (repository / "file").write_text(f"{repository.name}\n")
            self.git("add", "file", cwd=repository)
            self.git("commit", "-q", "-m", "Initial", cwd=repository)
            self.git("branch", "feature", cwd=repository)
            self.git(
                "update-ref", "refs/remotes/origin/feature", "HEAD", cwd=repository
            )
        target_sha = self.git("rev-parse", "HEAD", cwd=other)
        for options in ("-Cother", "-C other -C.. -Cother", "-Cother -C .. -Cother"):
            for operation in (
                "merge feature",
                "branch -D feature",
                "branch -rD origin/feature",
            ):
                with self.subTest(options=options, operation=operation):
                    before = self.git(
                        "for-each-ref", "--format=%(refname)", "refs/backup", cwd=other
                    ).splitlines()
                    result = self.run_hook(
                        "PreToolUse", 0, 1, f"git {options} {operation}"
                    )
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr), (0, "", "")
                    )
                    refs = self.git(
                        "for-each-ref", "--format=%(refname)", "refs/backup", cwd=other
                    ).splitlines()
                    self.assertEqual(len(refs), len(before) + 1)
                    created = set(refs) - set(before)
                    self.assertEqual(len(created), 1)
                    self.assertEqual(
                        self.git("rev-parse", created.pop(), cwd=other), target_sha
                    )
                    self.assertEqual(
                        self.git("for-each-ref", "--format=%(refname)", "refs/backup"),
                        "",
                    )

    def test_packaged_guards_preserve_read_only_config_commands(self):
        for command in (
            "git config user.name",
            "git config --global user.name",
            "git config --get user.name",
            "git config --show-origin user.name",
            "git config get user.name",
            "git config list",
        ):
            for handler in (0, 1):
                with self.subTest(command=command, handler=handler):
                    result = self.run_hook("PreToolUse", 0, handler, command)
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr), (0, "", "")
                    )
        for command in (
            "git config alias.publish push; git publish",
            "git config --show-origin alias.publish push; git publish",
            "git config set alias.publish push; git publish",
        ):
            self.assert_guards_deny(command)

    def test_packaged_guards_fail_closed_on_alias_lookup_error(self):
        self.assert_guards_deny("git -C /missing/local-guards-alias-repository publish")
        self.git("init", "-q", "-b", "main")
        (self.work / ".git/config").write_text("[broken\n")
        self.assert_guards_deny("git publish")

    def test_packaged_guards_fail_closed_without_jq_for_alias_invocation(self):
        tools = self.root / "alias-tools"
        tools.mkdir()
        for name in ("bash", "cat"):
            (tools / name).symlink_to(shutil.which(name))
        self.environment["PATH"] = str(tools)
        self.assert_guards_deny("git publish")

    def test_packaged_backup_command_records_reflog(self):
        subprocess.run(
            ["git", "init", "-q", "-b", "main"],
            cwd=self.work,
            env=self.environment,
            check=True,
        )
        (self.work / "file").write_text("initial\n")
        subprocess.run(
            ["git", "add", "file"], cwd=self.work, env=self.environment, check=True
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "Initial"],
            cwd=self.work,
            env=self.environment,
            check=True,
        )
        result = self.run_hook("PreToolUse", 0, 1, "git merge feature")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        refs = subprocess.check_output(
            ["git", "for-each-ref", "--format=%(refname)", "refs/backup"],
            cwd=self.work,
            env=self.environment,
            text=True,
        ).splitlines()
        self.assertEqual(len(refs), 1)
        self.assertRegex(refs[0], r"^refs/backup/main-\d{8}-\d{6}$")
        entry = subprocess.check_output(
            ["git", "reflog", "show", "-1", "--format=%gs", "HEAD"],
            cwd=self.work,
            env=self.environment,
            text=True,
        ).strip()
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.work, env=self.environment, text=True
        ).strip()
        self.assertEqual(entry, f"local-guards: backup main {sha}")
        self.assertEqual(self.git("rev-parse", refs[0]), sha)

    def test_claude_bash_guard_denies_push(self):
        result = self.run_claude_hook(
            "PreToolUse", 0, 0, {"command": "git push origin main"}, "Bash"
        )
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(
            (result.returncode, decision["permissionDecision"]), (0, "deny")
        )
        self.assertIn("local-guards", decision["permissionDecisionReason"])

    def test_setup_check_reports_no_duplicates_for_empty_home(self):
        result = self.setup_check()
        self.assertEqual(result.returncode, 0)
        self.assertIn("no matching local hooks", result.stdout)

    def test_setup_check_reports_json_duplicates_without_modifying_them(self):
        content = json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {"command": 'bash "$HOME/.codex/hooks/backup-ref.sh"'}
                            ]
                        }
                    ]
                }
            }
        )
        path = self.codex / "hooks.json"
        path.write_text(content)
        result = self.setup_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Disable the matching local hook entries", result.stdout)
        self.assertEqual(path.read_text(), content)

    def test_setup_check_reports_inline_toml_duplicates(self):
        path = self.codex / "config.toml"
        path.write_text(
            '[[hooks.PostToolUse]]\n[[hooks.PostToolUse.hooks]]\ncommand = \'python3 "${CODEX_HOME}/hooks/edit-checks.py"\'\n'
        )
        result = self.setup_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PostToolUse", result.stdout)

    def test_setup_check_accepts_explicitly_disabled_json_hook(self):
        path = self.codex / "hooks.json"
        path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {"command": "bash ~/.codex/hooks/backup-ref.sh"}
                                ]
                            }
                        ]
                    }
                }
            )
        )
        state = f"{path}:pre_tool_use:0:0"
        (self.codex / "config.toml").write_text(
            f'[hooks.state.{json.dumps(state)}]\nenabled = false\n'
        )
        self.assertEqual(self.setup_check().returncode, 0)

    def test_setup_check_accepts_explicitly_disabled_inline_hook(self):
        path = self.codex / "config.toml"
        state = f"{path}:post_tool_use:0:0"
        path.write_text(
            '[[hooks.PostToolUse]]\n[[hooks.PostToolUse.hooks]]\ncommand = \'python3 "${CODEX_HOME}/scripts/edit-checks.py"\'\n'
            f'[hooks.state.{json.dumps(state)}]\nenabled = false\n'
        )
        self.assertEqual(self.setup_check().returncode, 0)

    def test_setup_check_still_blocks_explicitly_enabled_hook(self):
        path = self.codex / "hooks.json"
        path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {"command": "bash ~/.codex/hooks/backup-ref.sh"}
                                ]
                            }
                        ]
                    }
                }
            )
        )
        state = f"{path}:pre_tool_use:0:0"
        (self.codex / "config.toml").write_text(
            f'[hooks.state.{json.dumps(state)}]\nenabled = true\n'
        )
        self.assertEqual(self.setup_check().returncode, 2)

    def test_setup_check_reports_only_enabled_handlers_in_mixed_group(self):
        path = self.codex / "hooks.json"
        path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {"command": "bash ~/.codex/hooks/deny-git-push.sh"},
                                    {"command": "bash ~/.codex/hooks/backup-ref.sh"},
                                ]
                            }
                        ]
                    }
                }
            )
        )
        state = f"{path}:pre_tool_use:0:0"
        (self.codex / "config.toml").write_text(
            f'[hooks.state.{json.dumps(state)}]\nenabled = false\n'
        )
        result = self.setup_check()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("deny-git-push.sh", result.stdout)
        self.assertIn("backup-ref.sh", result.stdout)

    def test_setup_check_ignores_unrelated_status_hook(self):
        (self.codex / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"hooks": [{"command": "agterm-agent-status completed"}]}
                        ]
                    }
                }
            )
        )
        self.assertEqual(self.setup_check().returncode, 0)

    def test_setup_check_uses_default_home_when_codex_home_is_unset(self):
        self.environment.pop("CODEX_HOME")
        self.environment["HOME"] = str(self.root)
        directory = self.root / ".codex"
        directory.mkdir()
        (directory / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {
                                        "command": "bash ~/.codex/scripts/deny-git-push.sh"
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
        )
        self.assertEqual(self.setup_check().returncode, 2)

    def test_setup_check_reports_unreadable_config(self):
        (self.codex / "hooks.json").write_text("{bad}")
        result = self.setup_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cannot inspect", result.stdout)

    def test_setup_check_reports_missing_required_dependency(self):
        tools = self.root / "tools"
        tools.mkdir()
        (tools / "bash").symlink_to(shutil.which("bash"))
        (tools / "git").symlink_to(shutil.which("git"))
        (tools / "python3").symlink_to(sys.executable)
        self.environment["PATH"] = str(tools)
        result = self.setup_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "Missing required commands: jq")

    def test_antigravity_manifest_and_hooks_match_package_contract(self):
        self.assertFalse((ROOT / "plugin.json").exists())
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        antigravity = json.loads((ROOT / ".antigravity-plugin/plugin.json").read_text())
        for key in ("name", "version", "description"):
            self.assertEqual(antigravity[key], claude[key])
        hooks = json.loads((ROOT / "hooks.json").read_text())
        self.assertIn("local-guards", hooks)
        spec = hooks["local-guards"]
        self.assertEqual(spec["PreToolUse"][0]["matcher"], "run_command")
        self.assertEqual(
            [item["command"] for item in spec["PreToolUse"][0]["hooks"]],
            [
                "bash ./scripts/deny-git-push.sh --antigravity",
                "bash ./scripts/backup-ref.sh --antigravity",
            ],
        )
        self.assertEqual(len(spec["PreToolUse"]), 1)
        self.assertEqual(spec["PreToolUse"][0]["matcher"], "run_command")
        self.assertNotIn("PostToolUse", spec)
        for hooks, matcher in ((self.claude_hooks, "Bash"), (self.hooks, "Bash")):
            self.assertEqual(
                [group["matcher"] for group in hooks["PreToolUse"]], [matcher]
            )
            self.assertNotIn("PostToolUse", hooks)
        self.assertFalse((ROOT / "scripts/edit-checks.py").exists())

    def test_antigravity_run_command_denies_push_and_allows_read(self):
        denied = self.run_antigravity_hook(
            "PreToolUse", 0, 0, "run_command", {"CommandLine": "git push origin main"}
        )
        self.assertEqual((denied.returncode, denied.stderr), (0, ""))
        decision = json.loads(denied.stdout)
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("local-guards", decision["reason"])
        allowed = self.run_antigravity_hook(
            "PreToolUse", 0, 0, "run_command", {"CommandLine": "git status"}
        )
        self.assertEqual((allowed.returncode, allowed.stderr), (0, ""))
        self.assertEqual(json.loads(allowed.stdout), {"decision": "allow"})

    def test_antigravity_backup_ref_creates_backup_and_emits_allow(self):
        self.git("init", "-q", "-b", "main")
        (self.work / "file").write_text("initial\n")
        self.git("add", "file")
        self.git("commit", "-q", "-m", "Initial")
        result = self.run_antigravity_hook(
            "PreToolUse", 0, 1, "run_command", {"CommandLine": "git merge feature"}
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout), {"decision": "allow"})
        refs = self.git(
            "for-each-ref", "--format=%(refname)", "refs/backup"
        ).splitlines()
        self.assertEqual(len(refs), 1)
        self.assertRegex(refs[0], r"^refs/backup/main-\d{8}-\d{6}$")
        sha = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-parse", refs[0]), sha)
        self.assertEqual(
            self.git("reflog", "show", "-1", "--format=%gs", "HEAD"),
            f"local-guards: backup main {sha}",
        )

    def test_antigravity_fallback_paths_emit_valid_decision_json(self):
        tools = self.root / "no-jq-tools"
        tools.mkdir()
        for name in ("bash", "cat"):
            (tools / name).symlink_to(shutil.which(name))
        no_jq_env = dict(self.environment, PATH=str(tools))
        for handler, command in ((0, "git push origin main"), (1, "git merge feature")):
            denied = self.run_antigravity_hook(
                "PreToolUse",
                0,
                handler,
                "run_command",
                {"CommandLine": command},
                env=no_jq_env,
            )
            self.assertEqual(denied.returncode, 0)
            self.assertEqual(json.loads(denied.stdout)["decision"], "deny")
            allowed = self.run_antigravity_hook(
                "PreToolUse",
                0,
                handler,
                "run_command",
                {"CommandLine": "ls -la"},
                env=no_jq_env,
            )
            self.assertEqual(allowed.returncode, 0)
            self.assertEqual(json.loads(allowed.stdout), {"decision": "allow"})

        broken = self.root / "broken-plugin"
        shutil.copytree(self.plugin / "scripts", broken / "scripts")
        (broken / "scripts/git-guard.sh").unlink()
        for script_name in ("deny-git-push.sh", "backup-ref.sh"):
            result = subprocess.run(
                ["bash", f"./scripts/{script_name}", "--antigravity"],
                cwd=broken,
                env=self.environment,
                input=json.dumps({"cwd": str(self.work)}),
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["decision"], "deny")

        probe = self.plugin / "scripts/guard-bootstrap-antigravity.sh"
        probe.write_text(
            '#!/usr/bin/env bash\n'
            'set -uo pipefail\n'
            'GUARD_HOOK_FORMAT=antigravity\n'
            'source "$(dirname "$0")/git-guard.sh" || exit 2\n'
            'guard_bootstrap "push" "no-jq-reason" "expected-reason" || exit 0\n'
            'exit 1\n'
        )
        result = subprocess.run(
            ["bash", str(probe)],
            cwd=self.work,
            env=self.environment,
            input=json.dumps(
                {
                    "cwd": str(self.work),
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "git push origin main"},
                    },
                }
            ),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout), {"decision": "deny", "reason": "expected-reason"}
        )
        self.assertNotIn("unbound variable", result.stderr)
