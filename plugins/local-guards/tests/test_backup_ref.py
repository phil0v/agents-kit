import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


HOOK = Path(__file__).resolve().parents[1] / "scripts/backup-ref.sh"


class BackupRefTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = dict(os.environ, AGENTS_HOME=str(self.root / ".metrics-home"),
                                AGENTS_METRICS_MODULE="",
                                GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
                                GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com",
                                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.environment.pop("AGENTS_METRICS", None)
        self.git("init", "-q", "-b", "main")
        (self.root / "file").write_text("initial\n")
        self.git("add", "file")
        self.git("commit", "-q", "-m", "Initial")
        self.git("branch", "feature")

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.root), *arguments],
                                       env=self.environment, text=True).strip()

    def hook(self, command):
        return subprocess.run(["bash", str(HOOK)], input=json.dumps({"cwd": str(self.root),
                              "tool_input": {"command": command}}), env=self.environment,
                              text=True, capture_output=True, timeout=10)

    def backups(self):
        return self.git("reflog", "show", "--format=%gs",
                        "--grep-reflog=^local-guards: backup ", "HEAD").splitlines()

    def test_merge_backs_up_current_commit(self):
        result = self.hook("git merge --ff-only feature")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.backups(), [f"local-guards: backup main {self.git('rev-parse', 'main')}"])
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/backup").splitlines()
        self.assertEqual(len(refs), 1)
        self.assertRegex(refs[0], r"^refs/backup/main-\d{8}-\d{6}$")
        self.assertEqual(self.git("rev-parse", refs[0]), self.git("rev-parse", "main"))

    def test_rebase_backs_up_named_branch(self):
        result = self.hook("git rebase main feature")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.backups(), [f"local-guards: backup feature {self.git('rev-parse', 'feature')}"])

    def test_forced_branch_backs_up_target(self):
        result = self.hook("git branch -f feature main")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.backups(), [f"local-guards: backup feature {self.git('rev-parse', 'feature')}"])

    def test_new_forced_branch_has_no_previous_commit_to_backup(self):
        result = self.hook("git branch -f new main")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.backups(), [])

    def test_repeated_operations_preserve_existing_backups(self):
        self.hook("git merge feature")
        original = self.git("rev-parse", "main")
        (self.root / "file").write_text("changed\n")
        self.git("add", "file")
        self.git("commit", "-q", "-m", "Changed")
        self.hook("git merge feature")
        self.assertEqual(self.backups(), [f"local-guards: backup main {self.git('rev-parse', 'main')}",
                                         f"local-guards: backup main {original}"])

    def test_continuation_and_read_commands_create_no_backup(self):
        for command in ("git status", "git merge --continue", "git rebase --abort", "git branch --list"):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command).stdout, "")
        self.assertEqual(self.backups(), [])

    def test_repository_identity_data_piped_to_python_creates_no_backup(self):
        identity = json.dumps({"identity": {"worktree_root": str(self.root),
                                          "git_dir": str(self.root / ".git"),
                                          "branch": "main", "head": self.git("rev-parse", "HEAD"),
                                          "plan_realpath": str(self.root / "plans/change.md")}})
        command = (f"printf '%s' {shlex.quote(identity)} | python3.13 "
                   "plugins/code-flow/skills/plan-new/scripts/run_state.py "
                   "--state-root /tmp/planning-state show")
        result = self.hook(command)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.backups(), [])

    def test_repository_metadata_path_does_not_hide_a_later_merge(self):
        result = self.hook("printf '%s' '{\"git_dir\":\"/tmp/repo/.git\",\"branch\":\"main\"}'; "
                           "git merge feature")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.backups(), [f"local-guards: backup main {self.git('rev-parse', 'main')}"])

    def test_non_current_branch_backup_preserves_head_and_recovers_deleted_commit(self):
        self.git("checkout", "-q", "feature")
        (self.root / "file").write_text("feature\n")
        self.git("commit", "-qam", "Feature")
        feature = self.git("rev-parse", "feature")
        self.git("checkout", "-q", "main")
        head = self.git("rev-parse", "HEAD")
        result = self.hook("git branch -D feature")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.git("rev-parse", "HEAD@{0}"), head)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        entry = self.backups()[0]
        self.assertEqual(entry, f"local-guards: backup feature {feature}")
        old, new = (self.root / ".git/logs/HEAD").read_text().splitlines()[-1].split()[:2]
        self.assertEqual((old, new), (head, head))
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/backup").splitlines()
        self.assertEqual(len(refs), 1)
        self.git("branch", "-D", "feature")
        self.git("reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
        self.git("gc", "--prune=now")
        self.assertEqual(self.git("cat-file", "-t", feature), "commit")
        self.assertEqual(self.git("rev-parse", refs[0]), feature)
        self.git("branch", "recovered-feature", refs[0])
        self.assertEqual(self.git("rev-parse", "recovered-feature"), feature)

    def test_same_second_backups_preserve_every_saved_commit(self):
        tools = self.root / "tools"
        tools.mkdir()
        date = tools / "date"
        date.write_text("#!/bin/sh\nprintf '%s\\n' 20260101-120000\n")
        date.chmod(0o755)
        self.environment["PATH"] = os.pathsep.join([str(tools), self.environment["PATH"]])
        original = self.git("rev-parse", "main")
        first = self.hook("git merge feature")
        self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
        (self.root / "file").write_text("changed\n")
        self.git("commit", "-qam", "Changed")
        changed = self.git("rev-parse", "main")
        second = self.hook("git merge feature; git merge feature")
        self.assertEqual((second.returncode, second.stdout, second.stderr), (0, "", ""))
        refs = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/backup").splitlines()
        self.assertEqual(refs, [f"refs/backup/main-20260101-120000 {original}",
                                f"refs/backup/main-20260101-120000-1 {changed}",
                                f"refs/backup/main-20260101-120000-2 {changed}"])

    def test_backup_ref_write_failure_is_denied(self):
        (self.root / ".git/refs/backup").write_text("blocked\n")
        result = self.hook("git merge feature")
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("could not be written to refs/backup/", decision["permissionDecisionReason"])
        self.assertEqual(self.backups(), [])

    def test_forced_delete_backs_up_every_existing_branch(self):
        self.git("branch", "other")
        sha = self.git("rev-parse", "HEAD")
        for arguments in ("-D feature other", "--delete --force feature other",
                          "-df feature other", "--force --delete -- feature other",
                          "-D feature missing other"):
            with self.subTest(arguments=arguments):
                before = len(self.backups())
                result = self.hook(f"git branch {arguments}")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assertEqual(len(self.backups()), before + 2)
                self.assertEqual(self.backups()[:2], [f"local-guards: backup other {sha}",
                                                      f"local-guards: backup feature {sha}"])

    def test_non_forced_delete_does_not_create_backup(self):
        result = self.hook("git branch --delete feature")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.backups(), [])

    def test_forced_remote_delete_option_forms_back_up_every_target(self):
        head = self.git("rev-parse", "HEAD")
        tree = self.git("rev-parse", "HEAD^{tree}")
        remote = self.git("commit-tree", tree, "-p", head, "-m", "Remote only")
        other = self.git("commit-tree", tree, "-p", head, "-m", "Other remote")
        for arguments in ("-r -D", "--remotes -D", "-rD", "-Dr",
                          "--remotes --delete --force", "--force --delete --remotes",
                          "-rdf", "-dfr", "--rem --delete --force"):
            with self.subTest(arguments=arguments):
                self.git("update-ref", "refs/remotes/origin/topic", remote)
                self.git("update-ref", "refs/remotes/origin/other", other)
                before = set(self.git("for-each-ref", "--format=%(refname)", "refs/backup").splitlines())
                command = ["git", "branch", *shlex.split(arguments), "--", "origin/topic", "origin/other"]
                result = self.hook(shlex.join(command))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                refs = set(self.git("for-each-ref", "--format=%(refname)", "refs/backup").splitlines()) - before
                self.assertEqual(len(refs), 2)
                self.assertEqual({self.git("rev-parse", ref) for ref in refs}, {remote, other})
                subprocess.run(command, cwd=self.root, env=self.environment, check=True, capture_output=True)
                self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/remotes"), "")

    def test_remote_delete_distinguishes_local_branch_and_preserves_head(self):
        head = self.git("rev-parse", "HEAD")
        tree = self.git("rev-parse", "HEAD^{tree}")
        remote = self.git("commit-tree", tree, "-p", head, "-m", "Remote only")
        self.git("branch", "origin/topic", head)
        self.git("update-ref", "refs/remotes/origin/topic", remote)
        result = self.hook("git branch -r -D origin/topic")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/backup").splitlines()
        self.assertEqual(len(refs), 1)
        self.assertRegex(refs[0], r"^refs/backup/refs/remotes/origin/topic-\d{8}-\d{6}$")
        self.assertEqual(self.git("rev-parse", refs[0]), remote)
        self.assertEqual(self.backups(), [f"local-guards: backup refs/remotes/origin/topic {remote}"])
        entry = (self.root / ".git/logs/HEAD").read_text().splitlines()[-1]
        self.assertEqual(entry.split()[:2], [head, head])
        self.assertEqual(self.git("rev-parse", "HEAD@{0}"), head)
        self.git("branch", "-r", "-D", "origin/topic")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("rev-parse", "refs/heads/origin/topic"), head)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/remotes"), "")

    def test_remote_forced_move_and_copy_are_denied_without_local_backups(self):
        self.git("branch", "origin/topic")
        self.git("update-ref", "refs/remotes/origin/topic", "HEAD")
        for arguments in ("-rM", "-Mr", "--remotes --move --force",
                          "-rC", "-Cr", "--remotes --copy --force",
                          "-rD --no-delete -M", "-rD --no-delete -C",
                          "--remotes --force"):
            with self.subTest(arguments=arguments):
                result = self.hook(f"git branch {arguments} origin/topic origin/new")
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/backup"), "")

    def test_local_forced_move_and_copy_preserve_both_targets(self):
        head = self.git("rev-parse", "HEAD")
        tree = self.git("rev-parse", "HEAD^{tree}")
        source = self.git("commit-tree", tree, "-p", head, "-m", "Source")
        for arguments in ("-M", "--move --force", "-C", "--copy --force"):
            with self.subTest(arguments=arguments):
                self.git("update-ref", "refs/heads/source", source)
                self.git("update-ref", "refs/heads/target", head)
                before = len(self.backups())
                result = self.hook(f"git branch {arguments} source target")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assertEqual(len(self.backups()), before + 2)
                self.assertEqual(self.backups()[:2], [f"local-guards: backup target {head}",
                                                      f"local-guards: backup source {source}"])
                self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_evaluated_git_commands_are_still_denied(self):
        for command in ("bash -c 'git merge feature'",
                        "env bash -c '/usr/bin/git merge feature'",
                        "eval './git merge feature'",
                        "python3.13 -c 'repo.git.merge(\"feature\")'",
                        "printf '%s' 'git merge feature' | bash"):
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.backups(), [])

    def test_non_evaluator_mentions_and_script_calls_allow_and_guard_git_segments(self):
        for command in ("echo bash git && git branch --list",
                        "echo python3 && git merge --abort",
                        "cat <<'EOF'\ngit merge feature\nEOF",
                        "python3 script.py <<'DATA' && git diff\ngit merge feature\nDATA",
                        "bash script.sh <<'DATA'\ngit rebase main feature\nDATA",
                        "python3 -m unittest discover -s tests && git branch --list",
                        "echo data | python3 script.py && git branch --list",
                        "python3 script.py | grep ok && git branch --list"):
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.backups(), [])

        main_sha = self.git("rev-parse", "main")
        result = self.hook("python3 script.py && git merge feature")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.backups(), [f"local-guards: backup main {main_sha}"])

    def test_expanded_script_heredoc_and_piped_stdin_evaluators_are_denied(self):
        for command in ("python3 \"$SCRIPT\" <<'DATA'\ngit merge feature\nDATA",
                        "echo 'git merge feature' | bash -s",
                        "echo 'git merge feature' | python3 -",
                        "cat <<'EOF' | bash\ngit merge feature\nEOF"):
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.backups(), [])

    def test_quoted_python_heredoc_with_rewrite_suffix_is_denied(self):
        command = "python3 - <<'PY'\nprint('data')\nPY\ngit merge feature"
        result = self.hook(command)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.backups(), [])

    def test_quoted_python_heredoc_with_executable_rewrite_is_denied(self):
        commands = (
            ("python3 - <<'PY'\nimport subprocess\n"
             "subprocess.run(['git', 'merge', 'feature'])\nPY"),
            ("python3 - <<'PY'\nimport subprocess\n"
             "subprocess.run(['git', 'rebase', 'main', 'feature'])\nPY"),
            ("python3 - <<'PY'\nimport subprocess\n"
             "subprocess.run(['git', 'branch', '-f', 'feature', 'main'])\nPY"),
            ("python3 - <<'PY'\nimport subprocess\n"
             "subprocess.run(unknown_command)\nPY\ngit diff --check"),
        )
        for command in commands:
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.backups(), [])

    def test_unresolvable_repository_is_denied(self):
        result = self.hook("git -C /missing/parity-repository merge feature")
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("repository", decision["permissionDecisionReason"])

    def test_detached_head_is_denied(self):
        self.git("checkout", "-q", "--detach")
        result = self.hook("git merge feature")
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_write_failure_is_denied(self):
        head_log = self.root / ".git/logs/HEAD"
        head_log.unlink()
        head_log.mkdir()
        (head_log / "block").write_text("busy\n")
        result = self.hook("git merge feature")
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("could not be written", decision["permissionDecisionReason"])
