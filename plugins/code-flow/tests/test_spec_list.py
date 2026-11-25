import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "spec-list" / "scripts" / "spec-list.py"


class SpecListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.base_env = os.environ.copy()

    def run_script(self, cwd=None, env=None, args=()):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)] + list(args),
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
            env=env or self.base_env,
        )
        return result

    def write_change(self, name, openspec_yaml=None, tasks=None, proposal=None):
        change_dir = self.repo / "openspec" / "changes" / name
        change_dir.mkdir(parents=True, exist_ok=True)
        if openspec_yaml is not None:
            (change_dir / ".openspec.yaml").write_text(openspec_yaml, encoding="utf-8")
        if tasks is not None:
            (change_dir / "tasks.md").write_text(tasks, encoding="utf-8")
        if proposal is not None:
            (change_dir / "proposal.md").write_text(proposal, encoding="utf-8")

    def parse_output(self, stdout):
        groups = {}
        errors = []
        current = None
        for line in stdout.splitlines()[1:]:
            if not line:
                continue
            if line in ("In progress", "Not started", "Ready to archive", "Draft"):
                current = line
                groups[current] = []
            elif line == "Errors":
                current = "Errors"
            elif current == "Errors":
                errors.append(line)
            elif current is not None:
                groups[current].append(line)
        return groups, errors

    def header_lines(self, group_rows):
        return [line for line in group_rows if not line.startswith(" ")]

    def change_name(self, line):
        parts = line.split()
        return parts[1] if parts[0] == "▸" else parts[0]

    def change_names(self, group_rows):
        return [self.change_name(line) for line in self.header_lines(group_rows)]

    def change_row(self, group_rows, name):
        return [
            line for line in self.header_lines(group_rows)
            if self.change_name(line) == name
        ][0]

    def description_rows(self, group_rows, name):
        collecting = False
        rows = []
        for line in group_rows:
            if line.startswith(" "):
                if collecting:
                    rows.append(line)
            else:
                collecting = self.change_name(line) == name
        return rows

    def row_summary(self, line):
        parts = re.split(r"  +", line.strip(), maxsplit=3)
        return parts[3] if len(parts) > 3 else ""

    def description_text(self, group_rows, name):
        return " ".join(
            line.strip() for line in self.description_rows(group_rows, name)
        )

    def test_no_openspec_directory(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "no openspec/ directory in this repository")
        self.assertEqual(result.stderr, "")

    def test_no_active_changes(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "no active changes under openspec/changes/")
        self.assertEqual(result.stderr, "")

    def _build_group_fixture(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()

        long_summary = "a" * 150
        self.write_change(
            "alice",
            openspec_yaml="created: 2024-01-01\nx-priority: P0\n",
            tasks="- [x] done\n- [ ] todo\n- [ ] todo\n",
            proposal=(
                "## Why\n\n"
                "Alice works on a description long enough to wrap onto several "
                "indented lines without losing any character of the paragraph.\n"
            ),
        )
        self.write_change(
            "charlie",
            openspec_yaml="created: 2024-01-01\nx-priority: P0\n",
            tasks="- [x] done\n- [ ] todo\n",
            proposal="## Why\n\nCharlie summary.\n",
        )
        self.write_change(
            "bob",
            openspec_yaml="created: 2024-01-01\nx-priority: P1\n",
            tasks="- [x] done\n- [ ] todo\n",
            proposal="## Why\n\nBob summary.\n",
        )
        self.write_change(
            "dave",
            openspec_yaml="created: 2024-01-01\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal=(
                "## Why\n\n"
                "Dave keeps a very long justification in the proposal so the inventory "
                "must shorten it at a word boundary, mark the digest with an ellipsis, "
                "and still keep the proposal file as the complete source of every "
                "remaining detail.\n"
            ),
        )
        self.write_change(
            "no_tasks",
            openspec_yaml="created: 2024-01-02\nx-priority: P0\n",
            tasks="",
            proposal="## Why\n\nNo tasks here.\n",
        )
        self.write_change(
            "frank",
            openspec_yaml="created: 2023-12-01\nx-priority: P2\n",
            tasks="- [ ] one\n- [ ] two\n",
            proposal="## Why\n\nFrank summary.\n",
        )
        self.write_change(
            "grace",
            openspec_yaml="created: not-a-date\nx-priority: P2\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nGrace summary.\n",
        )
        self.write_change(
            "eve",
            openspec_yaml="created: 2024-01-01\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nEve summary.\n",
        )
        self.write_change(
            "heidi",
            openspec_yaml="created: 2024-01-01\nx-priority: P1\n",
            tasks="- [x] one\n- [x] two\n",
            proposal="## Why\n\nHeidi summary.\n",
        )
        self.write_change(
            "ivan",
            openspec_yaml="created: 2024-01-01\nx-priority: P3\nx-draft: true\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nIvan summary.\n",
        )
        self.write_change(
            "longsum",
            openspec_yaml="created: 2024-01-03\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal=f"## Why\n\n{long_summary}\n",
        )
        self.write_change(
            "no_why",
            openspec_yaml="created: 2024-01-04\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="No Why section here.\n",
        )

    def test_grouped_inventory_reports_sorting_progress_and_compact_summaries(self):
        self._build_group_fixture()
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("12 active changes\n\n"))

        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(errors, ["grace: .openspec.yaml has invalid created date"])
        self.assertEqual(list(groups.keys()), ["In progress", "Not started", "Ready to archive", "Draft"])

        self.assertEqual(
            self.change_names(groups["In progress"]),
            ["alice", "charlie", "bob"],
        )
        self.assertEqual(
            self.change_names(groups["Not started"]),
            ["dave", "no_tasks", "longsum", "no_why", "frank", "grace", "eve"],
        )
        self.assertEqual(self.change_names(groups["Ready to archive"]), ["heidi"])
        self.assertEqual(self.change_names(groups["Draft"]), ["ivan"])

        self.assertIn("2/2", self.change_row(groups["Ready to archive"], "heidi"))

        no_tasks_line = self.change_row(groups["Not started"], "no_tasks")
        self.assertIn("-", no_tasks_line)
        self.assertNotIn("0/0", no_tasks_line)

        longsum_summary = self.row_summary(self.change_row(groups["Not started"], "longsum"))
        self.assertEqual(len(longsum_summary), 80)
        self.assertTrue(longsum_summary.endswith("…"))

        self.assertEqual(
            self.row_summary(self.change_row(groups["Not started"], "no_why")),
            "",
        )

    def test_full_listing_reports_indented_digest_blocks(self):
        self._build_group_fixture()
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env, args=["--full"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(errors, ["grace: .openspec.yaml has invalid created date"])
        self.assertEqual(list(groups.keys()), ["In progress", "Not started", "Ready to archive", "Draft"])

        self.assertEqual(
            self.change_names(groups["Not started"]),
            ["dave", "no_tasks", "longsum", "no_why", "frank", "grace", "eve"],
        )

        self.assertEqual(
            self.description_text(
                groups["In progress"],
                "alice",
            ),
            "Alice works on a description long enough to wrap onto several "
            "indented lines without losing any character of the paragraph.",
        )
        alice_rows = self.description_rows(groups["In progress"], "alice")
        self.assertGreater(len(alice_rows), 1)
        self.assertTrue(all(line.startswith("    ") for line in alice_rows))

        dave_full = (
            "Dave keeps a very long justification in the proposal so the inventory "
            "must shorten it at a word boundary, mark the digest with an ellipsis, "
            "and still keep the proposal file as the complete source of every "
            "remaining detail."
        )
        self.assertEqual(
            self.description_text(groups["Not started"], "dave"),
            dave_full[: dave_full.rfind(" ", 0, 180)] + "…",
        )
        self.assertLessEqual(len(self.description_rows(groups["Not started"], "dave")), 2)

        self.assertEqual(
            self.description_rows(groups["Not started"], "longsum"),
            ["    " + "a" * 150],
        )

        self.assertEqual(self.description_text(groups["Not started"], "no_why"), "")

        self.assertEqual(self.header_lines(groups["Draft"]), ["▸ ivan  P3  0/1"])

    def test_short_listing_prints_names_only(self):
        self._build_group_fixture()
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env, args=["--short"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("12 active changes\n\n"))

        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(errors, ["grace: .openspec.yaml has invalid created date"])
        self.assertEqual(list(groups.keys()), ["In progress", "Not started", "Ready to archive", "Draft"])

        self.assertEqual(groups["In progress"], ["alice", "charlie", "bob"])
        self.assertEqual(
            groups["Not started"],
            ["dave", "no_tasks", "longsum", "no_why", "frank", "grace", "eve"],
        )
        self.assertEqual(groups["Ready to archive"], ["heidi"])
        self.assertEqual(groups["Draft"], ["ivan"])

    def test_batch_without_unfinished_changes_reports_nothing_runnable(self):
        self._build_group_fixture()
        for name in ("alice", "charlie", "bob", "dave", "no_tasks", "longsum",
                     "no_why", "frank", "grace", "eve"):
            shutil.rmtree(self.repo / "openspec" / "changes" / name)
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env, args=["--batch"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "no unfinished changes to batch\n")

    def test_batch_prints_single_line_without_capability_conflicts(self):
        self._build_group_fixture()
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        for name, cap in (("alice", "caps-one"), ("dave", "caps-two"), ("heidi", "caps-three")):
            specs = self.repo / "openspec" / "changes" / name / "specs" / cap / "inner"
            specs.mkdir(parents=True)
            (specs / "spec.md").write_text("", encoding="utf-8")
        result = self.run_script(env=env, args=["--batch"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            "alice charlie bob dave no_tasks longsum no_why frank grace eve\n",
        )

    def test_batch_splits_into_valid_lines_on_capability_conflicts(self):
        self._build_group_fixture()
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        for name, spec_rel in (
            ("alice", "auth"),
            ("charlie", "auth"),
            ("dave", "auth/session"),
        ):
            specs = self.repo / "openspec" / "changes" / name / "specs" / spec_rel
            specs.mkdir(parents=True)
            (specs / "spec.md").write_text("", encoding="utf-8")
        result = self.run_script(env=env, args=["--batch"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "alice\ncharlie bob dave no_tasks longsum no_why frank grace eve\n",
        )

    def test_listing_mode_flags_are_mutually_exclusive(self):
        for first, second in (
            ("--full", "--short"),
            ("--full", "--batch"),
            ("--short", "--batch"),
        ):
            with self.subTest(first=first, second=second):
                result = self.run_script(args=[first, second])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.startswith("spec-list:"))

    def test_failing_openspec_cli_is_reported(self):
        self._build_group_fixture()
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        groups, errors = self.parse_output(result.stdout)
        self.assertIn("In progress", groups)
        self.assertEqual(errors, ["openspec list --json failed with exit code 1", "grace: .openspec.yaml has invalid created date"])

    def test_failing_openspec_cli_with_empty_changes_dir_reports_error(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("spec-list: openspec list --json failed with exit code 1", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("no active changes", result.stdout)

    def test_invalid_json_openspec_cli_with_empty_changes_dir_reports_error(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            "print('not json')\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("spec-list: openspec list --json output is not valid JSON", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("no active changes", result.stdout)

    def test_successful_openspec_cli_overrides_file_progress(self):
        self._build_group_fixture()
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            'print(json.dumps({"changes": [\n'
            '  {"name": "alice", "completedTasks": 0, "totalTasks": 3},\n'
            '  {"name": "heidi", "completedTasks": 2, "totalTasks": 2}\n'
            ']}))\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(errors, ["grace: .openspec.yaml has invalid created date"])
        self.assertIn("alice", self.change_names(groups["Not started"]))
        self.assertIn("heidi", self.change_names(groups["Ready to archive"]))

    def test_malformed_metadata_is_reported(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        self.write_change(
            "bad_meta",
            openspec_yaml="x-priority: P5\ncreated: not-a-date\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nSummary.\n",
        )
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        groups, errors = self.parse_output(result.stdout)
        self.assertIn("Not started", groups)
        self.assertEqual(
            errors,
            [
                "bad_meta: .openspec.yaml has invalid x-priority",
                "bad_meta: .openspec.yaml has invalid created date",
            ],
        )

    def test_unreadable_file_is_reported(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        self.write_change(
            "unreadable",
            openspec_yaml="x-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nSummary.\n",
        )
        change_dir = self.repo / "openspec" / "changes" / "unreadable"
        (change_dir / ".openspec.yaml").write_bytes(b"\xff\xfe")
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        groups, errors = self.parse_output(result.stdout)
        self.assertIn("Not started", groups)
        self.assertEqual(errors, ["unreadable: .openspec.yaml is unreadable"])

    def test_cli_only_change_is_listed_when_changes_dir_is_empty(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            'print(json.dumps({"changes": [\n'
            '  {"name": "cli-only", "completedTasks": 1, "totalTasks": 2}\n'
            ']}))\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("1 active change\n\n"))
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(self.change_names(groups["In progress"]), ["cli-only"])
        self.assertEqual(errors, [])

    def test_symlinked_change_dir_outside_changes_dir_is_excluded(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / ".openspec.yaml").write_text("created: 2024-01-01\n", encoding="utf-8")
        (outside / "proposal.md").write_text("## Why\n\nOutside.\n", encoding="utf-8")
        (outside / "tasks.md").write_text("- [ ] todo\n", encoding="utf-8")
        (self.repo / "openspec" / "changes" / "outside-link").symlink_to(outside)

        inside = self.repo / "openspec" / "changes" / "real"
        inside.mkdir()
        (inside / ".openspec.yaml").write_text("created: 2024-01-01\n", encoding="utf-8")
        (inside / "proposal.md").write_text("## Why\n\nInside.\n", encoding="utf-8")
        (inside / "tasks.md").write_text("- [ ] todo\n", encoding="utf-8")
        (self.repo / "openspec" / "changes" / "inside-link").symlink_to(inside)

        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("2 active changes\n\n"))
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(self.change_names(groups["Not started"]), ["inside-link", "real"])
        self.assertEqual(errors, [])

    def test_cli_reported_outside_symlink_is_excluded(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / ".openspec.yaml").write_text("created: 2024-01-01\n", encoding="utf-8")
        (outside / "proposal.md").write_text("## Why\n\nOutside.\n", encoding="utf-8")
        (outside / "tasks.md").write_text("- [ ] todo\n", encoding="utf-8")
        (self.repo / "openspec" / "changes" / "outside-link").symlink_to(outside)

        inside = self.repo / "openspec" / "changes" / "real"
        inside.mkdir()
        (inside / ".openspec.yaml").write_text("created: 2024-01-01\n", encoding="utf-8")
        (inside / "proposal.md").write_text("## Why\n\nInside.\n", encoding="utf-8")
        (inside / "tasks.md").write_text("- [ ] todo\n", encoding="utf-8")
        (self.repo / "openspec" / "changes" / "inside-link").symlink_to(inside)

        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            'print(json.dumps({"changes": [\n'
            '  {"name": "outside-link", "completedTasks": 0, "totalTasks": 1},\n'
            '  {"name": "inside-link", "completedTasks": 0, "totalTasks": 1},\n'
            '  {"name": "real", "completedTasks": 0, "totalTasks": 1}\n'
            ']}))\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("2 active changes\n\n"))
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(self.change_names(groups["Not started"]), ["inside-link", "real"])
        self.assertEqual(errors, [])


    def test_cli_reported_archive_hidden_and_escape_names_are_excluded(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        real = self.repo / "openspec" / "changes" / "real"
        real.mkdir()
        (real / ".openspec.yaml").write_text("created: 2024-01-01\n", encoding="utf-8")
        (real / "proposal.md").write_text("## Why\n\nReal.\n", encoding="utf-8")
        (real / "tasks.md").write_text("- [ ] todo\n", encoding="utf-8")
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            'print(json.dumps({"changes": [\n'
            '  {"name": "archive", "completedTasks": 0, "totalTasks": 1},\n'
            '  {"name": ".hidden", "completedTasks": 0, "totalTasks": 1},\n'
            '  {"name": "../escape", "completedTasks": 0, "totalTasks": 1},\n'
            '  {"name": "real", "completedTasks": 0, "totalTasks": 1}\n'
            ']}))\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("1 active change\n\n"))
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(self.change_names(groups["Not started"]), ["real"])
        self.assertEqual(errors, [])

    def test_openspec_cli_receives_telemetry_disabled(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        self.write_change(
            "alpha",
            openspec_yaml="created: 2024-01-01\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nAlpha.\n",
        )
        stub = Path(self.tmp.name) / "openspec"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "assert sys.argv[1:] == ['list', '--json'], sys.argv\n"
            "assert os.environ.get('OPENSPEC_TELEMETRY') == '0', os.environ.get('OPENSPEC_TELEMETRY')\n"
            'print(json.dumps({"changes": [{"name": "alpha", "completedTasks": 1, "totalTasks": 1}]}))\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(stub.parent) + os.pathsep + env["PATH"]
        env["OPENSPEC_TELEMETRY"] = "1"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(errors, [])
        self.assertEqual(self.change_names(groups["Ready to archive"]), ["alpha"])

    def test_error_ordering_and_cli_progress_override_with_disk_errors(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        self.write_change(
            "alpha",
            openspec_yaml="created: 2024-01-01\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nAlpha.\n",
        )
        (self.repo / "openspec" / "changes" / "alpha" / "proposal.md").write_bytes(b"\xff\xfe")
        self.write_change(
            "beta",
            openspec_yaml="x-priority: P9\ncreated: bad\n",
            tasks="- [x] done\n- [ ] todo\n",
            proposal="## Why\n\nBeta.\n",
        )
        self.write_change(
            "gamma",
            openspec_yaml="created: 2024-01-02\nx-priority: P1\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nGamma.\n",
        )
        (self.repo / "openspec" / "changes" / "gamma" / "tasks.md").write_bytes(b"\xff\xfe")

        failing_stub = Path(self.tmp.name) / "openspec"
        failing_stub.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        failing_stub.chmod(0o755)
        env = self.base_env.copy()
        env["PATH"] = str(failing_stub.parent) + os.pathsep + env["PATH"]
        fail_result = self.run_script(env=env)
        self.assertEqual(fail_result.returncode, 0, fail_result.stderr)
        _, fail_errors = self.parse_output(fail_result.stdout)
        self.assertEqual(
            fail_errors,
            [
                "openspec list --json failed with exit code 2",
                "alpha: proposal.md is unreadable",
                "beta: .openspec.yaml has invalid x-priority",
                "beta: .openspec.yaml has invalid created date",
                "gamma: tasks.md is unreadable",
            ],
        )

        ok_stub = Path(self.tmp.name) / "openspec"
        ok_stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "assert os.environ.get('OPENSPEC_TELEMETRY') == '0'\n"
            'print(json.dumps({"changes": [\n'
            '  {"name": "alpha", "completedTasks": 1, "totalTasks": 1},\n'
            '  {"name": "gamma", "completedTasks": 0, "totalTasks": 2},\n'
            '  {"name": "delta", "completedTasks": 1, "totalTasks": 2}\n'
            ']}))\n',
            encoding="utf-8",
        )
        ok_stub.chmod(0o755)
        ok_result = self.run_script(env=env)
        self.assertEqual(ok_result.returncode, 0, ok_result.stderr)
        groups, ok_errors = self.parse_output(ok_result.stdout)
        self.assertEqual(
            ok_errors,
            [
                "alpha: proposal.md is unreadable",
                "beta: .openspec.yaml has invalid x-priority",
                "beta: .openspec.yaml has invalid created date",
                "gamma: tasks.md is unreadable",
            ],
        )
        self.assertEqual(self.change_names(groups["Ready to archive"]), ["alpha"])
        self.assertEqual(self.change_names(groups["In progress"]), ["beta", "delta"])
        self.assertEqual(self.change_names(groups["Not started"]), ["gamma"])
        self.assertIn("0/2", self.change_row(groups["Not started"], "gamma"))

    def test_collect_changes_overlaps_openspec_list_with_disk_reads(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        self.write_change(
            "alpha",
            openspec_yaml="created: 2024-01-01\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nAlpha.\n",
        )
        spec = importlib.util.spec_from_file_location("spec_list_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        disk_read_started = threading.Event()
        original_try_read = module.try_read

        def tracking_try_read(path, errors, name, filename):
            disk_read_started.set()
            return original_try_read(path, errors, name, filename)

        def synchronized_openspec_list(root):
            overlapped = disk_read_started.wait(timeout=0.5)
            self.assertTrue(overlapped, "disk reads did not overlap with run_openspec_list")
            return {"alpha": (1, 1)}, None

        with (
            mock.patch.object(module, "try_read", side_effect=tracking_try_read),
            mock.patch.object(module, "run_openspec_list", side_effect=synchronized_openspec_list),
        ):
            rows, errors = module.collect_changes(str(self.repo))

        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], module.READY_TO_ARCHIVE)
        self.assertEqual(rows[0][3], "alpha")
        self.assertEqual(rows[0][4], "1/1")

    def test_draft_variants_and_non_iso_calendar_created_dates(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec" / "changes").mkdir()
        self.write_change(
            "title_draft",
            openspec_yaml="created: 2025-01-01\nx-priority: P0\nx-draft: True\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nTitle-cased draft.\n",
        )
        self.write_change(
            "yes_draft",
            openspec_yaml="created: 2025-01-01\nx-priority: P0\nx-draft: YES\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nYes draft.\n",
        )
        self.write_change(
            "compact_date",
            openspec_yaml="created: 20250102\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nCompact date.\n",
        )
        self.write_change(
            "week_date",
            openspec_yaml="created: 2025-W01-1\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nISO week date.\n",
        )
        self.write_change(
            "valid_later",
            openspec_yaml="created: 2025-01-03\nx-priority: P0\n",
            tasks="- [ ] todo\n",
            proposal="## Why\n\nValid date.\n",
        )
        env = self.base_env.copy()
        env["PATH"] = "/usr/bin" + os.pathsep + "/bin"
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        groups, errors = self.parse_output(result.stdout)
        self.assertEqual(
            self.change_names(groups["Draft"]),
            ["title_draft", "yes_draft"],
        )
        self.assertEqual(
            self.change_names(groups["Not started"]),
            ["valid_later", "compact_date", "week_date"],
        )
        self.assertEqual(
            errors,
            [
                "compact_date: .openspec.yaml has invalid created date",
                "week_date: .openspec.yaml has invalid created date",
            ],
        )


class SetMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def run_set_metadata(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "set-metadata", *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
        )

    def make_change(self, name="alpha", metadata="created: 2024-01-01\nx-priority: P1\n"):
        change_dir = self.repo / "openspec" / "changes" / name
        if change_dir.exists():
            shutil.rmtree(change_dir)
        change_dir.mkdir(parents=True)
        if metadata is not None:
            (change_dir / ".openspec.yaml").write_text(metadata, encoding="utf-8")
        return change_dir

    def metadata_text(self, name="alpha"):
        return (self.repo / "openspec" / "changes" / name / ".openspec.yaml").read_text(
            encoding="utf-8"
        )

    def assert_rejected_unchanged(self, result, change_dir, original):
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.startswith("spec-list: "), result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            (change_dir / ".openspec.yaml").read_text(encoding="utf-8"), original
        )

    def test_priority_update_rewrites_value_and_reports_success(self):
        change_dir = self.make_change()
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "Updated metadata for alpha.\n")
        self.assertEqual(
            self.metadata_text(), "created: 2024-01-01\nx-priority: P2\n"
        )
        self.assertEqual(sorted(os.listdir(change_dir)), [".openspec.yaml"])

    def test_created_update_rewrites_value(self):
        self.make_change()
        result = self.run_set_metadata("alpha", "--created", "2024-06-30")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.metadata_text(), "created: 2024-06-30\nx-priority: P1\n")

    def test_draft_true_appends_and_draft_false_rewrites_itself(self):
        self.make_change()
        result = self.run_set_metadata("alpha", "--draft", "true")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self.metadata_text(), "created: 2024-01-01\nx-priority: P1\nx-draft: true\n"
        )
        result = self.run_set_metadata("alpha", "--draft", "false")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self.metadata_text(), "created: 2024-01-01\nx-priority: P1\nx-draft: false\n"
        )

    def test_combined_update_rewrites_each_requested_key(self):
        self.make_change(metadata="summary: keep\nx-draft: true #review\n")
        result = self.run_set_metadata(
            "alpha", "--priority", "P3", "--created", "2024-02-29", "--draft", "false"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self.metadata_text(),
            "summary: keep\nx-draft: false #review\ncreated: 2024-02-29\nx-priority: P3\n",
        )

    def test_preserves_unrelated_lines_and_metadata_looking_block_scalars(self):
        original = (
            "notes: |\n"
            "  created: 2099-01-01\n"
            "  x-priority: P0\n"
            "deep:\n"
            "  nested:\n"
            "    x-draft: true\n"
            "plain: value #keep me\n"
        )
        change_dir = self.make_change(metadata=original)
        result = self.run_set_metadata("alpha", "--draft", "true")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.metadata_text(), original + "x-draft: true\n")
        self.assertEqual(
            (change_dir / ".openspec.yaml").read_bytes(),
            (original + "x-draft: true\n").encode("utf-8"),
        )

    def test_trailing_comment_preserved_with_single_space(self):
        self.make_change(metadata="x-priority: P1 #note\nx-draft: false\n")
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self.metadata_text(), "x-priority: P2 #note\nx-draft: false\n"
        )

    def test_attached_comment_treated_as_comment(self):
        self.make_change(metadata="x-priority: P1#note\n")
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.metadata_text(), "x-priority: P2 #note\n")

    def test_created_attached_comment_preserved(self):
        self.make_change(metadata="created: 2024-01-01#note\n")
        result = self.run_set_metadata("alpha", "--created", "2024-05-05")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.metadata_text(), "created: 2024-05-05 #note\n")

    def test_empty_value_with_only_comment_is_rejected(self):
        original = "created: #note\nx-priority: P1\n"
        change_dir = self.make_change(metadata=original)
        result = self.run_set_metadata("alpha", "--created", "2024-01-01")
        self.assert_rejected_unchanged(result, change_dir, original)

    def test_out_of_pattern_values_are_rewritable(self):
        cases = [
            ("x-draft: false\n", ["--draft", "true"], "x-draft: true\n"),
            ("x-priority: P5\n", ["--priority", "P2"], "x-priority: P2\n"),
            (
                "created: not-a-date\n",
                ["--created", "2024-01-01"],
                "created: 2024-01-01\n",
            ),
        ]
        for original, args, expected in cases:
            with self.subTest(original=original):
                self.make_change(metadata=original)
                result = self.run_set_metadata("alpha", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.metadata_text(), expected)

    def test_duplicate_top_level_lines_are_rejected(self):
        original = "created: 2024-01-01\ncreated: 2024-02-02\n"
        change_dir = self.make_change(metadata=original)
        result = self.run_set_metadata("alpha", "--created", "2024-03-03")
        self.assert_rejected_unchanged(result, change_dir, original)

    def test_non_scalar_representations_are_rejected(self):
        cases = [
            ("x-priority: 'P1'\n", ["--priority", "P2"]),
            ('x-priority: "P1"\n', ["--priority", "P2"]),
            ("x-priority:\n", ["--priority", "P2"]),
            ("x-priority: P1 extra\n", ["--priority", "P2"]),
            ("x-priority: |\n  P1\n", ["--priority", "P2"]),
            ("x-priority: |-\n  P1\n", ["--priority", "P2"]),
            ("x-draft: >2\n  true\n", ["--draft", "true"]),
        ]
        for original, args in cases:
            with self.subTest(original=original):
                change_dir = self.make_change(metadata=original)
                result = self.run_set_metadata("alpha", *args)
                self.assert_rejected_unchanged(result, change_dir, original)

    def test_unrequested_supported_keys_preserved_byte_for_byte(self):
        change_dir = self.make_change(metadata="x-priority: P1\ncreated: not-a-date\nx-draft: maybe\n")
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            (change_dir / ".openspec.yaml").read_bytes(),
            b"x-priority: P2\ncreated: not-a-date\nx-draft: maybe\n",
        )

    def test_strict_date_enforcement(self):
        rejected = ["20240101", "2024-W01-1", "2024-1-1", "2024-13-01", "2024-00-10", "2023-02-29"]
        for value in rejected:
            with self.subTest(value=value):
                change_dir = self.make_change(metadata="created: 2024-01-01\n")
                result = self.run_set_metadata("alpha", "--created", value)
                self.assert_rejected_unchanged(
                    result, change_dir, "created: 2024-01-01\n"
                )
        change_dir = self.make_change(metadata="created: 2024-01-01\n")
        result = self.run_set_metadata("alpha", "--created", "2024-02-29")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.metadata_text(), "created: 2024-02-29\n")

    def test_invalid_priority_rejected(self):
        for value in ["P4", "P10", "p1", "1", "high"]:
            with self.subTest(value=value):
                change_dir = self.make_change(metadata="x-priority: P1\n")
                result = self.run_set_metadata("alpha", "--priority", value)
                self.assert_rejected_unchanged(result, change_dir, "x-priority: P1\n")

    def test_invalid_draft_rejected(self):
        for value in ["yes", "True", "1", "no"]:
            with self.subTest(value=value):
                change_dir = self.make_change(metadata="x-draft: true\n")
                result = self.run_set_metadata("alpha", "--draft", value)
                self.assert_rejected_unchanged(result, change_dir, "x-draft: true\n")

    def test_empty_update_rejected(self):
        change_dir = self.make_change()
        result = self.run_set_metadata("alpha")
        self.assert_rejected_unchanged(
            result, change_dir, "created: 2024-01-01\nx-priority: P1\n"
        )

    def test_unknown_option_rejected(self):
        change_dir = self.make_change()
        result = self.run_set_metadata("alpha", "--priority", "P2", "--owner", "bob")
        self.assert_rejected_unchanged(
            result, change_dir, "created: 2024-01-01\nx-priority: P1\n"
        )

    def test_missing_metadata_file_fails_without_creating_it(self):
        change_dir = self.make_change(metadata=None)
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spec-list:", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((change_dir / ".openspec.yaml").exists())

    def test_non_utf8_metadata_file_rejected_unchanged(self):
        change_dir = self.make_change()
        original = b"\xff\xfe"
        (change_dir / ".openspec.yaml").write_bytes(original)
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.startswith("spec-list: "), result.stderr)
        self.assertIn("unreadable .openspec.yaml", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual((change_dir / ".openspec.yaml").read_bytes(), original)

    def test_missing_change_rejected(self):
        (self.repo / "openspec" / "changes").mkdir(parents=True)
        result = self.run_set_metadata("ghost", "--priority", "P2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spec-list:", result.stderr)
        self.assertIn("not an active change", result.stderr)

    def test_archived_and_hidden_targets_rejected(self):
        for name in ["archive", ".hidden"]:
            with self.subTest(name=name):
                change_dir = self.make_change(name=name)
                result = self.run_set_metadata(name, "--priority", "P2")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("spec-list:", result.stderr)
                self.assertEqual(
                    (change_dir / ".openspec.yaml").read_text(encoding="utf-8"),
                    "created: 2024-01-01\nx-priority: P1\n",
                )

    def test_names_with_path_separators_rejected_as_unsafe(self):
        self.make_change(name="sub")
        for name in ["a/b", "../escape", "a\\b"]:
            with self.subTest(name=name):
                result = self.run_set_metadata(name, "--priority", "P2")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("spec-list: unsafe change name", result.stderr)

    def test_parent_directory_name_rejected(self):
        result = self.run_set_metadata("..", "--priority", "P2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spec-list: change '..' resolves outside openspec/changes", result.stderr)

    def test_symlink_escaping_changes_dir_rejected_as_unsafe(self):
        self.make_change()
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / ".openspec.yaml").write_text("x-priority: P1\n", encoding="utf-8")
        (self.repo / "openspec" / "changes" / "escape-link").symlink_to(outside)
        result = self.run_set_metadata("escape-link", "--priority", "P2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spec-list:", result.stderr)
        self.assertIn("outside", result.stderr)
        self.assertEqual(
            (outside / ".openspec.yaml").read_text(encoding="utf-8"), "x-priority: P1\n"
        )

    def test_symlink_resolving_inside_changes_dir_accepted(self):
        real = self.make_change(name="real")
        (self.repo / "openspec" / "changes" / "real-link").symlink_to(real)
        result = self.run_set_metadata("real-link", "--priority", "P3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (real / ".openspec.yaml").read_text(encoding="utf-8"),
            "created: 2024-01-01\nx-priority: P3\n",
        )

    @unittest.skipIf(os.geteuid() == 0, "chmod 0o555 cannot fail as root")
    def test_failed_write_leaves_no_temporary_file(self):
        change_dir = self.make_change()
        original = "created: 2024-01-01\nx-priority: P1\n"
        change_dir.chmod(0o555)
        self.addCleanup(change_dir.chmod, 0o755)
        result = self.run_set_metadata("alpha", "--priority", "P2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spec-list:", result.stderr)
        leftovers = [
            name for name in os.listdir(change_dir) if name != ".openspec.yaml"
        ]
        self.assertEqual(leftovers, [])
        self.assertEqual(
            (change_dir / ".openspec.yaml").read_text(encoding="utf-8"), original
        )

    def test_colon_adjacent_values_recognized_and_rewritten_canonically(self):
        self.make_change(metadata="x-priority:P1\ncreated:2024-01-01\n")
        result = self.run_set_metadata("alpha", "--priority", "P2", "--created", "2024-05-05")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.metadata_text(), "x-priority: P2\ncreated: 2024-05-05\n"
        )

    def test_unrequested_colon_adjacent_line_preserved_byte_for_byte(self):
        self.make_change(metadata="x-priority:P1\ncreated:2024-01-01\n")
        result = self.run_set_metadata("alpha", "--draft", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.metadata_text(), "x-priority:P1\ncreated:2024-01-01\nx-draft: true\n"
        )

    def test_absent_keys_appended_in_canonical_order(self):
        self.make_change(metadata="summary: keep me\n")
        result = self.run_set_metadata(
            "alpha", "--draft", "true", "--priority", "P0", "--created", "2024-01-01"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.metadata_text(),
            "summary: keep me\ncreated: 2024-01-01\nx-priority: P0\nx-draft: true\n",
        )

    def test_file_without_trailing_newline_still_newline_terminated(self):
        self.make_change(metadata="summary: keep me")
        result = self.run_set_metadata("alpha", "--priority", "P1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.metadata_text(), "summary: keep me\nx-priority: P1\n")

    def test_listing_interface_still_works_with_changes(self):
        self.make_change()
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("1 active change\n"))


if __name__ == "__main__":
    unittest.main()
