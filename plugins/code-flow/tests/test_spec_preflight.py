import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "spec-flow" / "scripts" / "spec-preflight.py"


class SpecPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.base_env = os.environ.copy()

    def run_script(self, args=(), cwd=None, env=None, input=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
            env=env or self.base_env,
            input=input,
        )

    def write_change(self, name, openspec_yaml=None, tasks=None, design=None):
        change_dir = self.repo / "openspec" / "changes" / name
        change_dir.mkdir(parents=True, exist_ok=True)
        if openspec_yaml is not None:
            (change_dir / ".openspec.yaml").write_text(openspec_yaml, encoding="utf-8")
        if tasks is not None:
            (change_dir / "tasks.md").write_text(tasks, encoding="utf-8")
        if design is not None:
            (change_dir / "design.md").write_text(design, encoding="utf-8")

    def install_cli(self, name, validate, instructions, validate_exit=0, instructions_exit=0):
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "if sys.argv[1] == 'validate':\n"
            f"    assert sys.argv[2:] == ['{name}', '--type', 'change', '--strict', '--no-interactive', '--json'], sys.argv\n"
            f"    out = {validate!r}\n"
            f"    code = {validate_exit}\n"
            "else:\n"
            f"    assert sys.argv[1:] == ['instructions', 'apply', '--change', '{name}', '--json'], sys.argv\n"
            f"    out = {instructions!r}\n"
            f"    code = {instructions_exit}\n"
            "if isinstance(out, str):\n"
            "    sys.stdout.write(out)\n"
            "else:\n"
            "    print(json.dumps(out))\n"
            "sys.exit(code)\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
        return env

    def ok_cli_env(self, name, total=2, complete=1, issues=None):
        validate = {
            "items": [
                {"id": name, "type": "change", "valid": True, "issues": issues or []}
            ]
        }
        instructions = {
            "changeName": name,
            "progress": {"total": total, "complete": complete, "remaining": total - complete},
        }
        return self.install_cli(name, validate, instructions)

    def install_concurrent_cli(
        self,
        name,
        validate,
        instructions,
        *,
        validate_exit=0,
        instructions_exit=0,
        timeout_seconds=2.0,
    ):
        sync_dir = Path(self.tmp.name) / "sync"
        sync_dir.mkdir(exist_ok=True)
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "import time\n"
            "from pathlib import Path\n"
            f"sync_dir = Path({str(sync_dir)!r})\n"
            f"deadline = time.monotonic() + {timeout_seconds!r}\n"
            "def wait_for(marker):\n"
            "    target = sync_dir / marker\n"
            "    while not target.exists():\n"
            "        if time.monotonic() >= deadline:\n"
            "            sys.stderr.write(f'timed out waiting for {marker}\\n')\n"
            "            sys.exit(98)\n"
            "        time.sleep(0.01)\n"
            "if sys.argv[1] == 'validate':\n"
            f"    assert sys.argv[2:] == ['{name}', '--type', 'change', '--strict', '--no-interactive', '--json'], sys.argv\n"
            "    (sync_dir / 'validate.started').touch()\n"
            "    wait_for('instructions.started')\n"
            "    wait_for('instructions.done')\n"
            f"    out = {validate!r}\n"
            f"    code = {validate_exit}\n"
            "else:\n"
            f"    assert sys.argv[1:] == ['instructions', 'apply', '--change', '{name}', '--json'], sys.argv\n"
            "    (sync_dir / 'instructions.started').touch()\n"
            "    wait_for('validate.started')\n"
            f"    out = {instructions!r}\n"
            f"    code = {instructions_exit}\n"
            "if isinstance(out, str):\n"
            "    sys.stdout.write(out)\n"
            "else:\n"
            "    print(json.dumps(out))\n"
            "if sys.argv[1] != 'validate':\n"
            "    sys.stdout.flush()\n"
            "    (sync_dir / 'instructions.done').touch()\n"
            "sys.exit(code)\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
        return env

    def stderr_ends_with(self, result, stop_line):
        self.assertTrue(result.stderr.rstrip().endswith(f"stop: {stop_line}"), result.stderr)

    def test_batch_flag_combinations(self):
        cases = [
            (["alpha"], False, False, False),
            (["alpha"], False, True, False),
            (["alpha"], True, False, False),
            (["alpha"], True, True, True),
            (["beta", "alpha"], False, False, False),
            (["beta", "alpha"], False, True, False),
        ]
        for changes, no_commit, archive, archive_skipped in cases:
            with self.subTest(changes=changes, no_commit=no_commit, archive=archive):
                request = {"changes": changes, "no_commit": no_commit, "archive": archive}
                result = self.run_script(
                    ("--batch-arguments",), cwd=self.tmp.name, input=json.dumps(request)
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), request | {"archive_skipped": archive_skipped})

    def test_batch_rejects_multiple_no_commit_changes(self):
        for archive in (False, True):
            with self.subTest(archive=archive):
                request = {"changes": ["beta", "alpha"], "no_commit": True, "archive": archive}
                result = self.run_script(("--batch-arguments",), input=json.dumps(request))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout)["error"], "unsupported_batch_no_commit")
                self.assertEqual(result.stdout.count("unsupported_batch_no_commit"), 1)

    def test_batch_rejects_malformed_arguments(self):
        cases = [
            "", "{", "null", "[]", "true", "1", '"alpha"',
            '{}',
            '{"no_commit":false,"archive":false}',
            '{"changes":[],"no_commit":false,"archive":false}',
            '{"changes":"alpha","no_commit":false,"archive":false}',
            '{"changes":[""],"no_commit":false,"archive":false}',
            '{"changes":["  "],"no_commit":false,"archive":false}',
            '{"changes":[1],"no_commit":false,"archive":false}',
            '{"changes":["alpha",null],"no_commit":false,"archive":false}',
            '{"changes":["alpha"],"archive":false}',
            '{"changes":["alpha"],"no_commit":false}',
            '{"changes":["alpha"],"no_commit":0,"archive":false}',
            '{"changes":["alpha"],"no_commit":1,"archive":false}',
            '{"changes":["alpha"],"no_commit":"false","archive":false}',
            '{"changes":["alpha"],"no_commit":null,"archive":false}',
            '{"changes":["alpha"],"no_commit":false,"archive":0}',
            '{"changes":["alpha"],"no_commit":false,"archive":1}',
            '{"changes":["alpha"],"no_commit":false,"archive":"true"}',
            '{"changes":["alpha"],"no_commit":false,"archive":null}',
            '{"changes":["alpha"],"no_commit":false,"archive":false,"other":true}',
        ]
        for request in cases:
            with self.subTest(request=request):
                result = self.run_script(("--batch-arguments",), input=request)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout)["error"], "invalid_batch_arguments")

    def test_batch_mode_rejects_other_cli_arguments(self):
        cases = [
            ("--batch-arguments", "alpha"),
            ("--batch-arguments", "--archive-verdict"),
            ("--batch-arguments", "--no-commit"),
            ("--batch-arguments", "--archive"),
            ("--batch-arguments", "--bogus"),
            ("--batch-arguments", "--batch-arguments"),
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_script(
                    args, input='{"changes":["alpha"],"no_commit":false,"archive":false}'
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["error"], "invalid_batch_arguments")

    def test_batch_validation_leaves_files_and_openspec_untouched(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        self.write_change("beta", tasks="- [ ] two\n")
        sentinel = Path(self.tmp.name) / "openspec-called"
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            f"#!{sys.executable}\nfrom pathlib import Path\n"
            f"Path({str(sentinel)!r}).touch()\nraise SystemExit(99)\n", encoding="utf-8"
        )
        stub.chmod(0o755)
        env = self.base_env | {"PATH": self.tmp.name + os.pathsep + "/usr/bin:/bin"}
        before = {str(path.relative_to(self.repo)): path.read_bytes()
                  for path in self.repo.rglob("*") if path.is_file()}
        cases = [
            ('{"changes":["alpha"],"no_commit":false,"archive":true}', 0),
            ('{"changes":["alpha"],"no_commit":true,"archive":true}', 0),
            ('{"changes":["alpha","beta"],"no_commit":false,"archive":true}', 0),
            ('{"changes":["alpha","beta"],"no_commit":true,"archive":true}', 1),
            ('{"changes":[],"no_commit":false,"archive":false}', 2),
        ]
        for request, exit_status in cases:
            with self.subTest(request=request):
                result = self.run_script(("--batch-arguments",), input=request, env=env)
                self.assertEqual(result.returncode, exit_status, result.stderr)
                self.assertFalse(sentinel.exists())
                self.assertEqual(
                    {str(path.relative_to(self.repo)): path.read_bytes()
                     for path in self.repo.rglob("*") if path.is_file()}, before
                )

    def test_batch_preserves_tasks_paths_with_spaces(self):
        request = {"changes": ["/project with spaces/openspec/changes/alpha/tasks.md", "beta"],
                   "no_commit": False, "archive": False}
        result = self.run_script(("--batch-arguments",), input=json.dumps(request))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), request | {"archive_skipped": False})

    def test_usage_error_on_two_arguments(self):
        result = self.run_script(("alpha", "beta"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_usage_error_on_unknown_flag(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        result = self.run_script(("--bogus", "alpha"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_no_open_change_exits_none(self):
        result = self.run_script(())
        self.assertEqual(result.returncode, 4)
        self.assertIn("no open change under openspec/changes/ — name one", result.stderr)

    def test_draft_only_discovery_exits_none(self):
        self.write_change("alpha", openspec_yaml="x-draft: true\n", tasks="- [ ] one\n")
        result = self.run_script(())
        self.assertEqual(result.returncode, 4)

    def test_several_open_changes_exit_several(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        self.write_change("beta", tasks="- [ ] one\n")
        result = self.run_script(())
        self.assertEqual(result.returncode, 3)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "alpha\topenspec/changes/alpha/tasks.md",
                "beta\topenspec/changes/beta/tasks.md",
            ],
        )

    def test_sole_change_runs_with_ok_cli(self):
        self.write_change("alpha", tasks="- [ ] one\n- [x] two\n")
        env = self.ok_cli_env("alpha")
        result = self.run_script((), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "change: alpha — openspec/changes/alpha/tasks.md",
                "validate: ok",
                "tasks: 1/2 done",
            ],
        )

    def test_change_resolved_by_name_and_by_tasks_path(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        env = self.ok_cli_env("alpha")
        by_name = self.run_script(("alpha",), env=env)
        by_path = self.run_script(("openspec/changes/alpha/tasks.md",), env=env)
        self.assertEqual(by_name.returncode, 0)
        self.assertEqual(by_path.returncode, 0)
        self.assertIn("change: alpha — openspec/changes/alpha/tasks.md", by_name.stdout)
        self.assertIn("change: alpha — openspec/changes/alpha/tasks.md", by_path.stdout)

    def test_change_resolved_from_nested_directory(self):
        nested = self.repo / "openspec" / "changes" / "alpha"
        self.write_change("alpha", tasks="- [ ] one\n")
        env = self.ok_cli_env("alpha")
        result = self.run_script(("alpha",), cwd=nested, env=env)
        self.assertEqual(result.returncode, 0)

    def test_named_draft_is_rejected(self):
        self.write_change("alpha", openspec_yaml="x-draft: true\n", tasks="- [ ] one\n")
        result = self.run_script(("alpha",))
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "change: alpha — draft, remove x-draft from openspec/changes/alpha/.openspec.yaml to apply it",
            result.stderr,
        )
        self.stderr_ends_with(
            result, "name an open change under openspec/changes/, or nothing to list them"
        )

    def test_draft_with_yes_value_is_rejected(self):
        self.write_change("alpha", openspec_yaml="x-draft: YES\n", tasks="- [ ] one\n")
        result = self.run_script(("alpha",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: alpha — draft", result.stderr)

    def test_draft_variants_are_rejected_and_skipped_in_discovery(self):
        for value in ("True", "YES"):
            with self.subTest(value=value):
                self.write_change("alpha", openspec_yaml=f"x-draft: {value}\n", tasks="- [ ] one\n")
                named = self.run_script(("alpha",))
                self.assertEqual(named.returncode, 1)
                self.assertIn("change: alpha — draft", named.stderr)
                discovered = self.run_script(())
                self.assertEqual(discovered.returncode, 4)

    def test_archived_change_is_rejected_by_name(self):
        archive = self.repo / "openspec" / "changes" / "archive" / "2024-01-01-alpha"
        archive.mkdir(parents=True)
        (archive / "tasks.md").write_text("- [ ] one\n", encoding="utf-8")
        result = self.run_script(("alpha",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: alpha — archived, under openspec/changes/archive/", result.stderr)

    def test_archived_change_is_rejected_by_path(self):
        archive = self.repo / "openspec" / "changes" / "archive" / "2024-01-01-alpha"
        archive.mkdir(parents=True)
        (archive / "tasks.md").write_text("- [ ] one\n", encoding="utf-8")
        result = self.run_script(("openspec/changes/archive/2024-01-01-alpha/tasks.md",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: alpha — archived, under openspec/changes/archive/", result.stderr)

    def test_unknown_change_is_rejected(self):
        result = self.run_script(("ghost",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: ghost — no directory under openspec/changes/", result.stderr)

    def test_change_directory_without_tasks_md_is_rejected(self):
        (self.repo / "openspec" / "changes" / "alpha").mkdir(parents=True)
        result = self.run_script(("alpha",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: alpha — no tasks.md under openspec/changes/alpha/", result.stderr)
        self.stderr_ends_with(
            result, "name an open change under openspec/changes/, or nothing to list them"
        )

    def test_non_tasks_path_is_rejected(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        result = self.run_script(("openspec/changes/alpha/proposal.md",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: openspec/changes/alpha/proposal.md — not a tasks.md under openspec/changes/", result.stderr)

    def test_symlinked_change_outside_is_rejected_by_name(self):
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / "tasks.md").write_text("- [ ] one\n", encoding="utf-8")
        (self.repo / "openspec" / "changes").mkdir(parents=True)
        (self.repo / "openspec" / "changes" / "alpha-link").symlink_to(outside)
        result = self.run_script(("alpha-link",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("change: alpha-link — symlinked outside openspec/changes/", result.stderr)

    def test_symlinked_change_outside_is_skipped_by_discovery(self):
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / "tasks.md").write_text("- [ ] one\n", encoding="utf-8")
        (self.repo / "openspec" / "changes").mkdir(parents=True)
        (self.repo / "openspec" / "changes" / "alpha-link").symlink_to(outside)
        result = self.run_script(())
        self.assertEqual(result.returncode, 4)

    def test_missing_openspec_cli_stops_with_broken_cli(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("validate: openspec not found on PATH", result.stderr)
        self.stderr_ends_with(
            result, "the openspec CLI is missing or broken — fix its install, not the change"
        )

    def test_validate_failure_stops_with_fix_artifacts(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        validate = {
            "items": [
                {
                    "id": "alpha",
                    "type": "change",
                    "valid": False,
                    "issues": [
                        {"level": "ERROR", "path": "specs/alpha/spec.md", "message": "no deltas"},
                        {"level": "WARN", "path": "proposal.md", "message": "short"},
                    ],
                }
            ]
        }
        env = self.install_cli("alpha", validate, {"progress": {"total": 2, "complete": 0}})
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("validate: [ERROR] specs/alpha/spec.md: no deltas", result.stderr)
        self.assertIn("validate: [WARN] proposal.md: short", result.stderr)
        self.stderr_ends_with(result, "fix the change's artifacts and rerun")

    def test_validate_warnings_are_kept_on_success(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        env = self.ok_cli_env(
            "alpha", issues=[{"level": "WARN", "path": "proposal.md", "message": "short"}]
        )
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("validate: [WARN] proposal.md: short", result.stdout)
        self.assertIn("validate: ok", result.stdout)

    def test_refused_delta_stops_with_base_specs_message(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        validate = {
            "items": [
                {
                    "id": "alpha",
                    "type": "change",
                    "valid": True,
                    "issues": [
                        {
                            "level": "ERROR",
                            "path": "specs/alpha/spec.md",
                            "message": "Archive would refuse this delta: requirement gone",
                        }
                    ],
                }
            ]
        }
        env = self.install_cli("alpha", validate, {"progress": {"total": 1, "complete": 0}})
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "validate: [ERROR] specs/alpha/spec.md: Archive would refuse this delta: requirement gone",
            result.stderr,
        )
        self.stderr_ends_with(
            result, "the base specs no longer take this delta — fix them and rerun"
        )

    def test_unrecognized_error_issue_stops_the_run(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        validate = {
            "items": [
                {
                    "id": "alpha",
                    "type": "change",
                    "valid": True,
                    "issues": [
                        {
                            "level": "ERROR",
                            "path": "specs/alpha/spec.md",
                            "message": "Archive rejected delta: requirement gone",
                        }
                    ],
                }
            ]
        }
        env = self.install_cli("alpha", validate, {"progress": {"total": 1, "complete": 0}})
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "validate: [ERROR] specs/alpha/spec.md: Archive rejected delta: requirement gone",
            result.stderr,
        )
        self.stderr_ends_with(result, "fix the change's artifacts and rerun")

    def test_validate_nonzero_exit_without_issues(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        validate = {"items": [{"id": "alpha", "type": "change", "valid": True, "issues": []}]}
        env = self.install_cli("alpha", validate, {"progress": {"total": 1, "complete": 0}}, validate_exit=1)
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("validate: exit 1", result.stderr)

    def test_cli_error_envelope_is_reported(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        envelope = {
            "status": [
                {"severity": "error", "code": "change_error", "message": "Change 'alpha' not found."}
            ]
        }
        env = self.install_cli("alpha", envelope, {"progress": {"total": 1, "complete": 0}})
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("validate: Change 'alpha' not found.", result.stderr)
        self.stderr_ends_with(result, "fix the change's artifacts and rerun")

    def test_unreadable_validate_output_stops_with_broken_cli(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        env = self.install_cli("alpha", "not json at all", {"progress": {"total": 1, "complete": 0}})
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("validate: unreadable output", result.stderr)
        self.assertIn("not json at all", result.stderr)
        self.stderr_ends_with(
            result, "the openspec CLI is missing or broken — fix its install, not the change"
        )

    def test_non_string_issue_messages_stop_with_broken_cli(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        for level in ("ERROR", "WARN"):
            for message in (None, 7, [], {}, True, 1.5):
                with self.subTest(level=level, message=message):
                    env = self.ok_cli_env(
                        "alpha",
                        issues=[{"level": level, "path": "specs/alpha/spec.md", "message": message}],
                    )
                    result = self.run_script(("--archive-verdict", "alpha"), env=env)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(
                        result.stdout.splitlines(),
                        ["change: alpha — openspec/changes/alpha/tasks.md", "archive: stop"],
                    )
                    self.assertTrue(
                        result.stderr.startswith("validate: unreadable output\n"), result.stderr
                    )
                    self.stderr_ends_with(
                        result, "the openspec CLI is missing or broken — fix its install, not the change"
                    )

    def test_zero_tasks_stops_with_fix_artifacts(self):
        self.write_change("alpha", tasks="no checkboxes here\n")
        env = self.ok_cli_env("alpha", total=0, complete=0)
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("tasks: alpha — no checkbox task in tasks.md", result.stderr)
        self.stderr_ends_with(result, "fix the change's artifacts and rerun")

    def test_instructions_failure_stops_with_broken_cli(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        validate = {"items": [{"id": "alpha", "type": "change", "valid": True, "issues": []}]}
        env = self.install_cli(
            "alpha", validate, {"progress": {"total": 1, "complete": 0}}, instructions_exit=1
        )
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("tasks: exit 1", result.stderr)

    def test_missing_referenced_paths_are_listed(self):
        self.write_change(
            "alpha",
            tasks="- [ ] update `src/lib/core.py`\n- [ ] remove the `old/thing.py`\n",
            design="## Deferred\n\n- edit `gone/module.py`\n",
        )
        env = self.ok_cli_env("alpha")
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("missing: src/lib/core.py", result.stdout)
        self.assertNotIn("old/thing.py", result.stdout)
        self.assertNotIn("gone/module.py", result.stdout)

    def test_existing_referenced_paths_are_not_listed(self):
        (self.repo / "src" / "lib").mkdir(parents=True)
        (self.repo / "src" / "lib" / "core.py").write_text("", encoding="utf-8")
        self.write_change("alpha", tasks="- [ ] update `src/lib/core.py`\n")
        env = self.ok_cli_env("alpha")
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("missing:", result.stdout)

    def test_skipped_references_are_not_listed_as_missing(self):
        cases = [
            ("rename source", "- [ ] rename `old/a.py` to `new/b.py`\n", ["old/a.py"]),
            ("import path", "- [ ] vendor `gopkg.in/yaml.v2`\n", ["gopkg.in/yaml.v2"]),
            ("joined removal", "- [ ] remove `old/c.py`, and `old/d.py`\n", ["old/c.py", "old/d.py"]),
        ]
        for label, tasks, skipped in cases:
            with self.subTest(label=label):
                self.write_change("alpha", tasks=tasks)
                env = self.ok_cli_env("alpha")
                result = self.run_script(("alpha",), env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                for path in skipped:
                    self.assertNotIn(f"missing: {path}", result.stdout)

    def test_joined_removal_still_reports_genuinely_missing_paths(self):
        self.write_change(
            "alpha",
            tasks="- [ ] remove `old/c.py`, and `old/d.py` then update `src/lib/core.py`\n",
        )
        env = self.ok_cli_env("alpha")
        result = self.run_script(("alpha",), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("missing: src/lib/core.py", result.stdout)
        self.assertNotIn("missing: old/c.py", result.stdout)
        self.assertNotIn("missing: old/d.py", result.stdout)

    def test_archive_verdict_clean(self):
        self.write_change("alpha", tasks="- [x] one\n- [x] two\n")
        env = self.ok_cli_env("alpha", total=2, complete=2)
        result = self.run_script(("--archive-verdict", "alpha"), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines()[-1], "archive: clean")

    def test_archive_verdict_ask_reasons(self):
        self.write_change("alpha", tasks="- [ ] one\n- [x] two\n")
        env = self.ok_cli_env(
            "alpha",
            total=2,
            complete=1,
            issues=[{"level": "WARN", "path": "proposal.md", "message": "short"}],
        )
        result = self.run_script(("--archive-verdict", "alpha"), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.splitlines()[-1],
            "archive: ask — 1/2 tasks done, validate warnings",
        )

    def test_archive_verdict_stop_on_failure(self):
        self.write_change("alpha", tasks="- [ ] one\n")
        validate = {
            "items": [
                {
                    "id": "alpha",
                    "type": "change",
                    "valid": False,
                    "issues": [{"level": "ERROR", "path": "file", "message": "broken"}],
                }
            ]
        }
        env = self.install_cli("alpha", validate, {"progress": {"total": 1, "complete": 0}})
        result = self.run_script(("--archive-verdict", "alpha"), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("archive: stop", result.stdout)
        self.assertIn("stop: fix the change's artifacts and rerun", result.stderr)

    def test_openspec_checks_run_concurrently_and_preserve_stdout_order(self):
        self.write_change("alpha", tasks="- [ ] one\n- [x] two\n")
        validate = {
            "items": [
                {
                    "id": "alpha",
                    "type": "change",
                    "valid": True,
                    "issues": [{"level": "WARN", "path": "proposal.md", "message": "short"}],
                }
            ]
        }
        instructions = {"changeName": "alpha", "progress": {"total": 2, "complete": 1}}
        env = self.install_concurrent_cli("alpha", validate, instructions)
        result = self.run_script(("--archive-verdict", "alpha"), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "change: alpha — openspec/changes/alpha/tasks.md",
                "validate: [WARN] proposal.md: short",
                "validate: ok",
                "tasks: 1/2 done",
                "archive: ask — 1/2 tasks done, validate warnings",
            ],
        )

    def test_validate_error_precedes_progress_failure_when_progress_finishes_first(self):
        self.write_change("alpha", tasks="no checkboxes here\n")
        validate = {
            "items": [
                {
                    "id": "alpha",
                    "type": "change",
                    "valid": False,
                    "issues": [{"level": "ERROR", "path": "specs/alpha/spec.md", "message": "no deltas"}],
                }
            ]
        }
        instructions = {"changeName": "alpha", "progress": {"total": 0, "complete": 0}}
        env = self.install_concurrent_cli(
            "alpha", validate, instructions, validate_exit=1, instructions_exit=1
        )
        result = self.run_script(("--archive-verdict", "alpha"), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout.splitlines(),
            ["change: alpha — openspec/changes/alpha/tasks.md", "archive: stop"],
        )
        self.assertTrue(
            result.stderr.startswith("validate: [ERROR] specs/alpha/spec.md: no deltas\n"),
            result.stderr,
        )
        self.assertNotIn("tasks:", result.stderr)
        self.stderr_ends_with(result, "fix the change's artifacts and rerun")

    def test_validate_warnings_print_before_progress_failure(self):
        self.write_change("alpha", tasks="no checkboxes here\n")
        env = self.ok_cli_env(
            "alpha",
            total=0,
            complete=0,
            issues=[{"level": "WARN", "path": "proposal.md", "message": "short"}],
        )
        result = self.run_script(("--archive-verdict", "alpha"), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "change: alpha — openspec/changes/alpha/tasks.md",
                "validate: [WARN] proposal.md: short",
                "validate: ok",
                "archive: stop",
            ],
        )
        self.assertIn("tasks: alpha — no checkbox task in tasks.md", result.stderr)
        self.stderr_ends_with(result, "fix the change's artifacts and rerun")

    def test_check_validate_and_check_progress_direct_signatures(self):
        spec = importlib.util.spec_from_file_location("spec_preflight_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.write_change("alpha", tasks="- [ ] one\n- [x] two\n")
        env = self.ok_cli_env(
            "alpha",
            total=2,
            complete=1,
            issues=[{"level": "WARN", "path": "proposal.md", "message": "short"}],
        )
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(stdout):
            validate_lines = module.check_validate(str(self.repo), "alpha")
            progress = module.check_progress(str(self.repo), "alpha")
        self.assertEqual(validate_lines, ["validate: [WARN] proposal.md: short"])
        self.assertEqual(progress, (1, 2))
        self.assertEqual(
            stdout.getvalue().splitlines(),
            [
                "validate: [WARN] proposal.md: short",
                "validate: ok",
                "tasks: 1/2 done",
            ],
        )


if __name__ == "__main__":
    unittest.main()
