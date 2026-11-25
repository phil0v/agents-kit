import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import ROOT, RUN_STATE_SCRIPT as SCRIPT, load_plan_module, load_run_state_module


run_state = load_run_state_module()
plan = load_plan_module()

APP_PY = 'def greet():\n    return "hello"\n'


def passing_check(command="python3 -m unittest tests.test_app -v"):
    return {
        "command": command,
        "argv": command.split(),
        "cwd": "/repo",
        "exit_status": 0,
        "log_path": "/tmp/check-output.log",
        "input_hashes": {"src/app.py": "0" * 64},
        "kind": "auto",
    }


def incomplete_check(field):
    check = passing_check()
    del check[field]
    return check


CHECK_EVIDENCE_FIELDS = ("argv", "cwd", "log_path", "input_hashes")


def fixture_plan(step_ids, dependencies=None):
    dependencies = dependencies or {}
    steps = [
        f"{step_id}. [ ] Verify step {step_id}\n"
        f"   depends on: {', '.join(str(dep) for dep in dependencies.get(step_id, [])) or 'none'}\n"
        for step_id in step_ids
    ]
    return "# Greeting\n\n## Steps\n\n" + "\n".join(steps)


class RunStateTestBase(unittest.TestCase):
    DEFAULT_STEP_IDS = [1, 2]
    DEFAULT_STEP_DEPENDENCIES = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        git_root = patch.object(run_state.plan_module, "git_toplevel", side_effect=lambda directory: directory)
        git_root.start()
        self.addCleanup(git_root.stop)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "app.py").write_text(APP_PY, encoding="utf-8")
        (self.repo / "plans").mkdir()
        self.plan = self.repo / "plans" / "greeting.md"
        self.plan.write_text(fixture_plan(self.DEFAULT_STEP_IDS, self.DEFAULT_STEP_DEPENDENCIES), encoding="utf-8")
        self.state_root = Path(self.tmp.name) / "codex-home" / "planning-state"
        self.head = "1" * 40
        kwargs = {"step_ids": self.DEFAULT_STEP_IDS}
        if self.DEFAULT_STEP_DEPENDENCIES is not None:
            kwargs["step_dependencies"] = self.DEFAULT_STEP_DEPENDENCIES
        _, init_payload = self.call("init", self.identity_request(**kwargs))
        self.revision = init_payload["revision"]

    def identity_request(self, step_ids=None, step_dependencies=None, **overrides):
        request = {
            "identity": {
                "worktree_root": str(self.repo),
                "git_dir": str(self.repo / ".git"),
                "branch": "master",
                "head": self.head,
                "plan_realpath": str(self.plan.resolve()),
            }
        }
        if step_ids is not None:
            request["step_ids"] = step_ids
        if step_dependencies is not None:
            request["step_dependencies"] = step_dependencies
        request.update(overrides)
        return request

    def call(self, command, request=None, state_root=None):
        return run_state.run(command, request if request is not None else self.identity_request(), state_root or self.state_root)

    def mutate(self, command, **overrides):
        request = self.identity_request(expected_revision=self.revision, **overrides)
        code, payload = self.call(command, request)
        self.assertEqual(code, 0, payload)
        self.revision = payload["revision"]
        return payload

    def error_code(self, command, **overrides):
        if "expected_revision" not in overrides:
            overrides["expected_revision"] = self.revision
        request = self.identity_request(**overrides)
        code, payload = self.call(command, request)
        self.assertEqual(code, 1, payload)
        return payload["error"]["code"]

    def assert_close_needs_revalidation(self, owner):
        self.assertEqual(
            self.error_code("close", owner=owner, result="ready", checks=[passing_check()]),
            "needs_revalidation",
        )

    def claim_owner(self):
        return self.mutate("claim")["owner"]

    def accept_plan(self, digest="d" * 64):
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])

    def start_step(self, owner, step):
        self.mutate("start", owner=owner, step=step)

    def complete_pending_steps(self, owner):
        state = self.call("show")[1]
        for step_id, step in state["steps"].items():
            if step["status"] == "pending":
                self.start_step(owner, step_id)
                self.mutate(
                    "record", owner=owner, step=step_id, handback="verified remaining implementation",
                    checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
                )

    def app_hash(self):
        return hashlib.sha256((self.repo / "src" / "app.py").read_bytes()).hexdigest()


class RunStateTests(RunStateTestBase):
    def test_openspec_repo_refused_with_stable_code(self):
        (self.repo / "openspec").mkdir()
        for command, extra in (("show", {}), ("claim", {}), ("close", {"owner": "o"})):
            code, payload = self.call(command, self.identity_request(expected_revision=self.revision, **extra))
            self.assertEqual(code, 1, command)
            self.assertEqual(payload["error"]["code"], "openspec_project", command)
            self.assertIn(str(ROOT / "skills" / "spec-flow" / "SKILL.md"), payload["error"]["message"])
            self.assertIn("plugins/code-flow/skills/spec-flow/SKILL.md", payload["error"]["message"])
            self.assertIn(str((self.repo / "openspec").resolve()), payload["error"]["message"])

    def test_openspec_repo_refuses_init(self):
        (self.repo / "openspec").mkdir()
        other_plan = self.repo / "plans" / "other.md"
        other_plan.write_text("# Other\n", encoding="utf-8")
        request = self.identity_request(step_ids=[1])
        request["identity"]["plan_realpath"] = str(other_plan.resolve())
        code, payload = self.call("init", request)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "openspec_project")

    def test_non_openspec_repo_passes_openspec_gate(self):
        code, state = self.call("show")
        self.assertEqual(code, 0)
        self.assertEqual(state["revision"], self.revision)

    def test_init_and_show_roundtrip(self):
        code, state = self.call("show")
        self.assertEqual(code, 0)
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["revision"], 0)
        self.assertEqual(state["identity"]["worktree_root"], str(self.repo))
        self.assertEqual(state["identity"]["plan_realpath"], str(self.plan.resolve()))
        self.assertEqual(state["steps"]["1"]["status"], "pending")
        self.assertEqual(state["steps"]["2"]["status"], "pending")
        self.assertEqual(state["close_out"]["status"], "pending")
        self.assertEqual(state["ownership"]["state"], "released")
        self.assertIsNone(state["accepted_digest"])

    def test_init_twice_is_refused(self):
        code, payload = self.call("init", self.identity_request(step_ids=[1]))
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "state_exists")

    def test_identity_mismatch_from_moved_plan_or_other_worktree(self):
        other_plan = self.repo / "plans" / "other.md"
        other_plan.write_text("# Other\n", encoding="utf-8")
        moved = self.identity_request()
        moved["identity"]["plan_realpath"] = str(other_plan.resolve())
        code, payload = self.call("show", moved)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "state_missing")

        foreign_plan = Path(self.tmp.name) / "elsewhere" / "plans" / "greeting.md"
        foreign_plan.parent.mkdir(parents=True)
        foreign_plan.write_text("# Greeting\n", encoding="utf-8")
        foreign = self.identity_request()
        foreign["identity"]["worktree_root"] = str(Path(self.tmp.name) / "elsewhere")
        foreign["identity"]["plan_realpath"] = str(foreign_plan.resolve())
        code, payload = self.call("show", foreign)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "state_missing")

    def test_identity_mismatch_when_same_plan_has_different_worktree(self):
        foreign_plan = Path(self.tmp.name) / "elsewhere" / "plans" / "greeting.md"
        foreign_plan.parent.mkdir(parents=True)
        foreign_plan.write_text("# Greeting\n", encoding="utf-8")
        foreign = self.identity_request()
        foreign["identity"]["worktree_root"] = str(Path(self.tmp.name) / "elsewhere")
        foreign["identity"]["git_dir"] = str(Path(self.tmp.name) / "elsewhere" / ".git")
        foreign["identity"]["plan_realpath"] = str(self.plan.resolve())
        code, payload = self.call("show", foreign)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "identity_mismatch")

    def test_init_refuses_identity_mismatch_for_same_plan_in_different_worktree(self):
        foreign = self.identity_request()
        foreign["identity"]["worktree_root"] = str(Path(self.tmp.name) / "elsewhere")
        foreign["identity"]["git_dir"] = str(Path(self.tmp.name) / "elsewhere" / ".git")
        code, payload = self.call("init", foreign)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "identity_mismatch")

    def test_missing_state_is_reported(self):
        code, payload = self.call("show", state_root=Path(self.tmp.name) / "empty" / "planning-state")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "state_missing")

    def test_non_object_request_or_missing_identity_exits_two(self):
        code, payload = self.call("show", request=[])
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")

        code, payload = self.call("show", request={})
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")

        code, payload = self.call("show", request={"identity": {}})
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_corrupt_state_file_is_reported(self):
        key = run_state.state_key(self.identity_request()["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text("{ not json", encoding="utf-8")
        code, payload = self.call("show")
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "corrupt_state")

    def test_truncated_state_file_is_reported_as_corrupt(self):
        key = run_state.state_key(self.identity_request()["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        code, payload = self.call("show")
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "corrupt_state")

    def test_leftover_temporary_write_is_ignored(self):
        key = run_state.state_key(self.identity_request()["identity"])
        (self.state_root / f"{key}.json.tmp.123").write_text("garbage", encoding="utf-8")
        code, state = self.call("show")
        self.assertEqual(code, 0)
        self.assertEqual(state["revision"], 0)
        self.mutate("review", digest="d" * 64, result="clean")
        leftovers = [name for name in os.listdir(self.state_root) if ".tmp." in name]
        self.assertEqual(leftovers, [f"{key}.json.tmp.123"])
        state_path = self.state_root / f"{key}.json"
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["review"]["result"], "clean")

    def test_lock_file_survives_replacement(self):
        key = run_state.state_key(self.identity_request()["identity"])
        lock_path = self.state_root / f"{key}.lock"
        self.assertTrue(lock_path.exists())
        lock_inode = lock_path.stat().st_ino
        self.mutate("review", digest="d" * 64, result="clean")
        self.assertTrue(lock_path.exists())
        self.assertEqual(lock_path.stat().st_ino, lock_inode)

    def _assert_write_syncs_file_and_directory(self, state_root, key, action):
        events = []
        synced_fds = []
        real_fsync = os.fsync
        real_replace = os.replace

        def tracked_fsync(fd):
            st = os.fstat(fd)
            synced_fds.append(fd)
            kind = "fsync_dir" if stat.S_ISDIR(st.st_mode) else "fsync_file"
            events.append((kind, (st.st_dev, st.st_ino), st.st_size))
            return real_fsync(fd)

        def tracked_replace(src, dst):
            st = os.stat(src)
            events.append(("replace", (st.st_dev, st.st_ino), Path(dst)))
            return real_replace(src, dst)

        with patch.object(run_state.os, "fsync", side_effect=tracked_fsync), patch.object(
            run_state.os, "replace", side_effect=tracked_replace
        ):
            action()

        state_path = state_root / f"{key}.json"
        dir_stat = os.stat(state_root)
        self.assertEqual([kind for kind, _, _ in events], ["fsync_file", "replace", "fsync_dir"])
        self.assertEqual(events[0][1], events[1][1])
        self.assertGreater(events[0][2], 0)
        self.assertEqual(events[0][2], state_path.stat().st_size)
        self.assertEqual(events[1][2], state_path)
        self.assertEqual(events[2][1], (dir_stat.st_dev, dir_stat.st_ino))
        for fd in synced_fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_state_write_syncs_file_and_directory(self):
        key = run_state.state_key(self.identity_request()["identity"])
        fresh_root = Path(self.tmp.name) / "fresh-state"
        self._assert_write_syncs_file_and_directory(
            fresh_root,
            key,
            lambda: self.assertEqual(
                self.call("init", self.identity_request(step_ids=[1, 2]), state_root=fresh_root)[0],
                0,
            ),
        )
        self._assert_write_syncs_file_and_directory(
            self.state_root,
            key,
            lambda: self.mutate("review", digest="d" * 64, result="clean"),
        )

    def test_state_write_cleans_up_temp_file_on_fsync_failure(self):
        key = run_state.state_key(self.identity_request()["identity"])
        state_path = self.state_root / f"{key}.json"
        before_text = state_path.read_text(encoding="utf-8")
        failed_fds = []

        def failing_fsync(fd):
            failed_fds.append(fd)
            raise OSError("disk error")

        with patch.object(run_state.os, "fsync", side_effect=failing_fsync):
            with self.assertRaises(OSError):
                self.mutate("review", digest="d" * 64, result="clean")

        self.assertEqual(len(failed_fds), 1)
        with self.assertRaises(OSError):
            os.fstat(failed_fds[0])
        self.assertEqual([name for name in os.listdir(self.state_root) if ".tmp." in name], [])
        self.assertEqual(state_path.read_text(encoding="utf-8"), before_text)

    def test_state_key_separates_worktree_and_plan_unambiguously(self):
        left = run_state.state_key({"worktree_root": "/repo", "plan_realpath": "/repo/plans/a.md"})
        right = run_state.state_key({"worktree_root": "/repo/r", "plan_realpath": "/plans/a.md"})
        self.assertNotEqual(left, right)

    def test_review_and_accept_track_digests(self):
        self.mutate("review", digest="d" * 64, result="clean")
        self.mutate("accept", digest="d" * 64, criteria=["A1", "A2"])
        code, state = self.call("show")
        self.assertEqual(state["review"]["receipts"][-1]["result"], "clean")
        self.assertEqual(state["review"]["receipts"][-1]["digest"], "d" * 64)
        self.assertEqual(state["accepted_digest"], "d" * 64)
        self.assertEqual(state["accepted_criteria"], ["A1", "A2"])

    def test_progress_only_update_keeps_receipt_valid(self):
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.mutate("review", digest=digest, result="clean")
        code, state = self.call("show")
        self.assertEqual(state["review"]["digest"], digest)
        self.assertEqual(state["accepted_digest"], digest)
        self.assertEqual(len(state["review"]["receipts"]), 2)

    def test_stale_revision_is_rejected_after_lock(self):
        self.assertEqual(self.error_code("review", digest="d" * 64, result="clean", expected_revision=99), "stale_state")

    def _record_check_kind(self, owner, kind):
        self.start_step(owner, 1)
        self.mutate("recover", owner=owner, step=1, outcome="pending", evidence={"changed_files": [], "checks": []})
        self.mutate("start", owner=owner, step=1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[{"command": "pytest", "exit_status": None, "kind": kind}],
            hashes={"src/app.py": self.app_hash()},
        )

    def test_failed_auto_check_never_marks_done(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[{"command": "pytest", "exit_status": 1, "kind": "auto"}],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "blocked")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["checks"][0]["status"], "failed")

    def test_start_refused_for_non_pending_step(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[{"command": "pytest", "exit_status": 1, "kind": "auto"}],
            hashes={"src/app.py": self.app_hash()},
        )
        self.assertEqual(self.error_code("start", owner=owner, step=1), "step_not_pending")
        self.assertEqual(
            self.error_code(
                "record",
                owner=owner,
                step=1,
                handback="changed greet",
                changed_files=["src/app.py"],
                checks=[passing_check()],
                hashes={"src/app.py": self.app_hash()},
            ),
            "step_not_running",
        )
        code, payload = self.call(
            "start",
            self.identity_request(expected_revision=self.revision, owner=owner, step=1),
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "step_not_pending")

    def test_manual_check_never_marks_done(self):
        owner = self.claim_owner()
        self.accept_plan()
        self._record_check_kind(owner, "manual")
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "needs-manual-check")

    def test_planned_check_never_marks_done(self):
        owner = self.claim_owner()
        self.accept_plan()
        self._record_check_kind(owner, "planned")
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "needs-manual-check")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["checks"][0]["status"], "gap")

    def test_passing_checks_mark_done_and_checkpoint(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["checkpoints"][-1]["changed_files"], ["src/app.py"])
        self.assertEqual(state["checkpoints"][-1]["hashes"], {"src/app.py": self.app_hash()})

    def test_record_rejects_incomplete_check_evidence(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        for field in CHECK_EVIDENCE_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(
                    self.error_code(
                        "record",
                        owner=owner,
                        step=1,
                        handback="changed greet",
                        changed_files=["src/app.py"],
                        checks=[incomplete_check(field)],
                        hashes={"src/app.py": self.app_hash()},
                    ),
                    "incomplete_check_evidence",
                )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "running")
        self.assertEqual(state["steps"]["1"]["evidence"], [])
        self.assertEqual(state["checkpoints"], [])

    def test_close_rejects_incomplete_check_evidence(self):
        owner = self.claim_owner()
        self.accept_plan()
        for field in CHECK_EVIDENCE_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(
                    self.error_code(
                        "close",
                        owner=owner,
                        result="ready",
                        checks=[incomplete_check(field)],
                        hashes={"src/app.py": self.app_hash()},
                    ),
                    "incomplete_check_evidence",
                )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "pending")
        self.assertNotIn("checks", state["close_out"])

    def test_recover_done_rejects_incomplete_check_evidence(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        for field in CHECK_EVIDENCE_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(
                    self.error_code(
                        "recover",
                        owner=owner,
                        step=1,
                        outcome="done",
                        evidence={
                            "changed_files": ["src/app.py"],
                            "checks": [incomplete_check(field)],
                            "hashes": {"src/app.py": self.app_hash()},
                        },
                    ),
                    "incomplete_check_evidence",
                )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "running")
        self.assertEqual(state["steps"]["1"]["evidence"], [])
        self.assertEqual(state["checkpoints"], [])

    def test_record_requires_handback(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(
            self.error_code(
                "record",
                owner=owner,
                step=1,
                handback="",
                changed_files=["src/app.py"],
                checks=[passing_check()],
                hashes={"src/app.py": self.app_hash()},
            ),
            "missing_handback",
        )

    def test_record_accepts_handback_at_byte_bound(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="x" * 4096,
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")

    def test_record_rejects_handback_over_byte_bound(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(
            self.error_code(
                "record",
                owner=owner,
                step=1,
                handback="x" * 4097,
                changed_files=["src/app.py"],
                checks=[passing_check()],
                hashes={"src/app.py": self.app_hash()},
            ),
            "oversized_handback",
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "running")
        self.assertEqual(state["steps"]["1"]["evidence"], [])

    def test_second_running_step_is_refused(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(self.error_code("start", owner=owner, step=2), "step_already_running")

    def test_claim_refuses_while_owner_active(self):
        self.claim_owner()
        self.assertEqual(self.error_code("claim"), "owner_active")

    def test_same_owner_continues_without_reapproval(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.mutate("start", owner=owner, step=1)
        self.mutate("block", owner=owner, step=1, reason="waiting on user")
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "blocked")

    def test_wrong_owner_token_is_refused(self):
        owner = self.claim_owner()
        self.assertEqual(self.error_code("start", owner="not-" + owner, step=1), "owner_mismatch")

    def test_release_then_reclaim_by_new_session(self):
        owner = self.claim_owner()
        self.mutate("release", owner=owner, reason="stop")
        replacement = self.mutate("claim")["owner"]
        self.assertNotEqual(replacement, owner)
        code, state = self.call("show")
        self.assertEqual(state["ownership"]["state"], "active")

    def test_mutate_after_release_is_refused(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.mutate("release", owner=owner, reason="stop")
        self.assertEqual(self.error_code("start", owner=owner, step=1), "owner_mismatch")
        self.assertEqual(
            self.error_code("record", owner=owner, step=1, handback="x", changed_files=[], checks=[]),
            "owner_mismatch",
        )
        self.assertEqual(self.error_code("block", owner=owner, step=1), "owner_mismatch")
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=None,
                replacement_digest="e" * 64,
                affected_steps=[1],
            ),
            "owner_mismatch",
        )

    def test_takeover_without_ended_run_evidence_asks_and_leaves_state_unchanged(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(self.error_code("takeover", previous_owner=owner), "recovery_question_required")
        code, state = self.call("show")
        self.assertEqual(state["revision"], self.revision)
        self.assertEqual(state["ownership"]["owner"], owner)
        self.assertEqual(state["steps"]["1"]["status"], "running")

    def test_takeover_with_user_statement_replaces_owner_and_preserves_run(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.accept_plan(digest)
        self.start_step(owner, 1)
        replacement = self.mutate(
            "takeover",
            previous_owner=owner,
            evidence={"user_statement": "the old session is gone"},
        )["owner"]
        code, state = self.call("show")
        self.assertEqual(state["ownership"]["owner"], replacement)
        self.assertEqual(state["ownership"]["state"], "active")
        self.assertEqual(state["steps"]["1"]["status"], "running")
        self.assertEqual(state["accepted_digest"], digest)
        self.assertEqual(state["ownership"]["history"][-1]["kind"], "takeover")

    def test_takeover_with_ended_session_result(self):
        owner = self.claim_owner()
        replacement = self.mutate("takeover", previous_owner=owner, evidence={"ended_session": True})["owner"]
        self.assertNotEqual(replacement, owner)

    def test_takeover_with_unknown_previous_owner_is_refused(self):
        owner = self.claim_owner()
        self.assertEqual(self.error_code("takeover", previous_owner="not-" + owner, evidence={"ended_session": True}), "owner_mismatch")

    def test_recover_requires_running_step(self):
        owner = self.claim_owner()
        self.assertEqual(
            self.error_code("recover", owner=owner, step=1, outcome="pending", evidence={"changed_files": [], "checks": []}),
            "step_not_running",
        )

    def test_recover_rejects_unknown_outcome(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.start_step(owner, 1)
        code, payload = self.call(
            "recover",
            self.identity_request(
                expected_revision=self.revision,
                owner=owner,
                step=1,
                outcome="don",
                evidence={"changed_files": ["src/app.py"], "checks": []},
            ),
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_recover_done_requires_hashes(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(
            self.error_code(
                "recover",
                owner=owner,
                step=1,
                outcome="done",
                evidence={"changed_files": ["src/app.py"], "checks": [passing_check()]},
            ),
            "missing_hashes",
        )

    def test_recover_marks_done_pending_or_blocked(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])

        self.start_step(owner, 1)
        self.mutate(
            "recover",
            owner=owner,
            step=1,
            outcome="done",
            evidence={
                "handback": "partial edits completed the step",
                "changed_files": ["src/app.py"],
                "checks": [passing_check()],
                "hashes": {"src/app.py": self.app_hash()},
            },
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["checkpoints"][-1]["changed_files"], ["src/app.py"])

        self.start_step(owner, 2)
        self.mutate("recover", owner=owner, step=2, outcome="pending", evidence={"changed_files": ["src/app.py"], "checks": []})
        code, state = self.call("show")
        self.assertEqual(state["steps"]["2"]["status"], "pending")
        self.assertEqual(state["steps"]["2"]["evidence"][-1]["status"], "interrupted-partial")

        self.mutate("start", owner=owner, step=2)
        self.mutate(
            "recover",
            owner=owner,
            step=2,
            outcome="blocked",
            evidence={"changed_files": ["src/app.py"], "checks": [], "reason": "tooling unavailable"},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["2"]["status"], "blocked")

    def test_init_records_step_dependencies(self):
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["depends_on"], [])
        self.assertEqual(state["steps"]["2"]["depends_on"], [])

    def test_close_records_checks_and_blocked_reason(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.complete_pending_steps(owner)
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["checks"][0]["status"], "passed")
        self.assertNotIn("reason", state["close_out"])

        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[{"command": "pytest", "exit_status": 1, "kind": "auto"}],
            hashes={"src/app.py": self.app_hash()},
            reason="tidy found issues",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["checks"][0]["status"], "failed")
        self.assertEqual(state["close_out"]["reason"], "tidy found issues")

    def test_close_empty_checks_or_blocked_result_stays_blocked(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[],
            hashes={},
            reason="no checks supplied",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "blocked")
        self.assertEqual(state["close_out"]["reason"], "no checks supplied")
        self.assertEqual(state["close_out"]["checks"], [])

        self.mutate(
            "close",
            owner=owner,
            result="blocked",
            checks=[passing_check()],
            hashes={},
            reason="caller reported blocked",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "blocked")
        self.assertEqual(state["close_out"]["reason"], "caller reported blocked")

    def test_empty_checks_never_mark_done(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "blocked")
        self.assertEqual(state["checkpoints"], [])

    def test_exempt_check_counts_as_passed(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="verified by inspection",
            changed_files=["src/app.py"],
            checks=[{"command": "none", "kind": "exempt", "reason": "manual verification"}],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["checks"][0]["status"], "passed")

    def test_concurrent_init_only_one_wins(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        fresh_root = Path(self.tmp.name) / "codex-home" / "planning-state-concurrent"
        other_plan = self.repo / "plans" / "concurrent.md"
        other_plan.write_text(fixture_plan([1]), encoding="utf-8")
        request = self.identity_request(step_ids=[1])
        request["identity"]["plan_realpath"] = str(other_plan.resolve())
        processes = [
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "--state-root", str(fresh_root), "init"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [process.communicate(json.dumps(request)) for process in processes]
        exit_codes = [0 if "revision" in json.loads(stdout or stderr) else 1 for stdout, stderr in results]
        self.assertEqual(sorted(exit_codes), [0, 1])
        loser = next(json.loads(stdout or stderr) for stdout, stderr in results if "error" in json.loads(stdout or stderr))
        self.assertEqual(loser["error"]["code"], "state_exists")

    def test_concurrent_init_same_plan_two_worktrees_writes_one_state_file(self):
        fresh_root = Path(self.tmp.name) / "codex-home" / "planning-state-concurrent-worktrees"
        shared_plan = self.repo / "plans" / "concurrent-worktrees.md"
        shared_plan.write_text(fixture_plan([1]), encoding="utf-8")
        requests = []
        for name in ("wt-a", "wt-b"):
            worktree = Path(self.tmp.name) / name
            worktree.mkdir()
            req = self.identity_request(step_ids=[1])
            req["identity"]["worktree_root"] = str(worktree)
            req["identity"]["git_dir"] = str(worktree / ".git")
            req["identity"]["plan_realpath"] = str(shared_plan.resolve())
            requests.append(req)

        real_scan = run_state._find_conflicting_identity
        first_scanned = threading.Event()
        second_scanned = threading.Event()
        scan_lock = threading.Lock()
        call_count = 0

        def synchronized_scan(state_root, identity, current_key):
            nonlocal call_count
            result = real_scan(state_root, identity, current_key)
            with scan_lock:
                call_count += 1
                is_first = call_count == 1
            if is_first:
                first_scanned.set()
                second_scanned.wait(timeout=0.2)
            else:
                second_scanned.set()
            return result

        results = [None, None]
        with patch.object(run_state, "_find_conflicting_identity", side_effect=synchronized_scan):
            t1 = threading.Thread(target=lambda: results.__setitem__(0, run_state.run("init", requests[0], fresh_root)))
            t2 = threading.Thread(target=lambda: results.__setitem__(1, run_state.run("init", requests[1], fresh_root)))
            t1.start()
            self.assertTrue(first_scanned.wait(timeout=2.0))
            t2.start()
            t1.join(timeout=5.0)
            t2.join(timeout=5.0)
            self.assertFalse(t1.is_alive())
            self.assertFalse(t2.is_alive())

        self.assertEqual(len(list(fresh_root.glob("*.json"))), 1)
        self.assertEqual(sorted(code for code, _ in results), [0, 1])
        loser = next(payload for code, payload in results if code == 1)
        self.assertEqual(loser["error"]["code"], "identity_mismatch")

    def test_missing_step_and_malformed_request_return_stable_errors(self):
        owner = self.claim_owner()
        self.accept_plan()
        code, payload = self.call(
            "start",
            self.identity_request(expected_revision=self.revision, owner=owner, step=99),
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "unknown_step")

        code, payload = self.call(
            "record",
            self.identity_request(expected_revision=self.revision, owner=owner),
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")

        code, payload = self.call(
            "start",
            self.identity_request(expected_revision=self.revision, owner=owner, step=None),
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_start_after_accept_without_review_returns_review_missing(self):
        owner = self.claim_owner()
        self.mutate("accept", digest="d" * 64, criteria=["A1"])
        self.assertEqual(self.error_code("start", owner=owner, step=1), "review_missing")

    def test_documented_sequence_init_review_accept_claim_start_record(self):
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1", "A2"], criterion_text={"A1": "greets", "A2": "exits"})
        owner = self.claim_owner()
        self.mutate("start", owner=owner, step=1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="done",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(code, 0)
        self.assertEqual(state["accepted_digest"], digest)
        self.assertEqual(state["accepted_criteria"], ["A1", "A2"])
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["steps"]["2"]["status"], "pending")

    def test_start_requires_accepted_review_and_clean_verdict(self):
        owner = self.claim_owner()
        self.assertEqual(self.error_code("start", owner=owner, step=1), "not_authorized")

        self.mutate("review", digest="d" * 64, result="clean")
        self.assertEqual(self.error_code("start", owner=owner, step=1), "not_authorized")

        self.mutate("accept", digest="d" * 64, criteria=["A1"])
        self.start_step(owner, 1)
        self.mutate("block", owner=owner, step=1, reason="retry")

        self.mutate("review", digest="e" * 64, result="clean")
        self.assertEqual(self.error_code("start", owner=owner, step=1), "review_stale")

        self.mutate("review", digest="d" * 64, result="issues")
        self.assertEqual(self.error_code("start", owner=owner, step=1), "review_unclean")

    def test_done_requires_hashes(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(
            self.error_code(
                "record",
                owner=owner,
                step=1,
                handback="changed greet",
                changed_files=["src/app.py"],
                checks=[passing_check()],
            ),
            "missing_hashes",
        )

    def test_recover_processes_checks_and_refuses_done_with_failures(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.assertEqual(
            self.error_code(
                "recover",
                owner=owner,
                step=1,
                outcome="done",
                evidence={
                    "changed_files": ["src/app.py"],
                    "checks": [{"command": "pytest", "exit_status": 1, "kind": "auto"}],
                    "hashes": {"src/app.py": self.app_hash()},
                },
            ),
            "step_not_done",
        )
        self.mutate(
            "recover",
            owner=owner,
            step=1,
            outcome="done",
            evidence={
                "changed_files": ["src/app.py"],
                "checks": [passing_check()],
                "hashes": {"src/app.py": self.app_hash()},
            },
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["checks"][0]["status"], "passed")

    def test_plan_file_drift_uses_digest_not_bytes(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        parsed = plan.parse_plan(str(self.plan.resolve()))
        plan_digest = plan.plan_digest(parsed)
        before = self.plan.read_bytes()
        text = before.decode("utf-8")
        self.plan.write_text(text.replace("1. [ ] Verify step 1", "1. [x] Verify step 1"), encoding="utf-8")
        self.assertNotEqual(self.plan.read_bytes(), before)
        self.assertEqual(plan.plan_digest(plan.parse_plan(str(self.plan.resolve()))), plan_digest)
        self.mutate(
            "release",
            owner=owner,
            reason="stop",
            watched_files={"plans/greeting.md": plan_digest},
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "pending")
        text = self.plan.read_text(encoding="utf-8")
        self.plan.write_text(text.replace("# Greeting", "# Greeting changed"), encoding="utf-8")
        owner2 = self.mutate("claim")["owner"]
        self.assertEqual(
            self.error_code(
                "release",
                owner=owner2,
                reason="stop",
                watched_files={"plans/greeting.md": plan_digest},
            ),
            "unexplained_drift",
        )

    def test_close_ready_requires_passing_checks(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.complete_pending_steps(owner)
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[passing_check("python3 -m unittest discover -s tests -v")],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "ready")
        self.assertEqual(state["close_out"]["final_hashes"], {"src/app.py": self.app_hash()})

        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[{"command": "pytest", "exit_status": 1, "kind": "auto"}],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "blocked")

    def test_close_ready_rejects_incomplete_execution_without_writing(self):
        owner = self.claim_owner()
        request = {
            "owner": owner, "result": "ready", "checks": [passing_check()],
            "hashes": {"src/app.py": self.app_hash()},
        }

        def assert_rejected(error, **overrides):
            state_path = next(self.state_root.glob("*.json"))
            before = state_path.read_bytes()
            self.assertEqual(self.error_code("close", **(request | overrides)), error)
            self.assertEqual(state_path.read_bytes(), before)

        assert_rejected("not_authorized")
        self.mutate("accept", digest="d" * 64, criteria=["A1"])
        assert_rejected("review_missing")
        self.mutate("review", digest="e" * 64, result="clean")
        assert_rejected("review_stale")
        self.mutate("review", digest="d" * 64, result="needs revision")
        assert_rejected("review_unclean")
        self.mutate("review", digest="d" * 64, result="clean")
        assert_rejected("steps_incomplete")
        self.start_step(owner, 1)
        assert_rejected("steps_incomplete")
        self.mutate("record", owner=owner, step=1, handback="verified greeting",
                    checks=[passing_check()], hashes={"src/app.py": self.app_hash()})
        assert_rejected("steps_incomplete")
        self.complete_pending_steps(owner)
        assert_rejected("missing_hashes", hashes={})
        assert_rejected("missing_hashes", hashes={"src/app.py": ""})
        assert_rejected("missing_hashes", hashes={"": self.app_hash()})
        self.mutate("amend", owner=owner, prior_digest="d" * 64,
                    replacement_digest="f" * 64, affected_steps=[],
                    new_criteria=["A1", "A2"], decision="propose additional acceptance")
        assert_rejected("authorization_pending")
        self.mutate("close", owner=owner, result="blocked", reason="authorization required")
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "blocked")

    def test_close_ready_rejects_blocked_and_manual_steps(self):
        owner = self.claim_owner()
        self.accept_plan()
        for checks in (
            [{"kind": "auto", "exit_status": 1}],
            [{"kind": "manual"}],
            [{"kind": "planned"}],
        ):
            with self.subTest(checks=checks):
                self.start_step(owner, 1)
                self.mutate("record", owner=owner, step=1, handback="verification incomplete", checks=checks)
                before = self.call("show")[1]
                self.assertEqual(self.error_code(
                    "close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()},
                ), "steps_incomplete")
                self.assertEqual(self.call("show")[1], before)
                self.mutate("recover", owner=owner, step=1, outcome="pending")

    def test_timed_out_exit_zero_cannot_complete_step_or_close(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        check = passing_check() | {"timed_out": True}
        self.mutate("record", owner=owner, step=1, handback="verification timed out", checks=[check])
        state = self.call("show")[1]
        self.assertEqual(state["steps"]["1"]["status"], "blocked")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["checks"][0]["status"], "failed")
        self.mutate("recover", owner=owner, step=1, outcome="pending")
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[check],
                    hashes={"src/app.py": self.app_hash()})
        closed = self.call("show")[1]
        self.assertEqual(closed["close_out"]["status"], "blocked")
        self.assertEqual(closed["close_out"]["checks"][0]["status"], "failed")
        self.assertEqual(closed["close_out"]["final_hashes"], {})

    def test_archive_receipt_preserves_original_state_identity_and_history(self):
        owner = self.claim_owner()
        self.accept_plan()
        for step in (1, 2):
            self.start_step(owner, step)
            self.mutate(
                "record", owner=owner, step=step, handback="verified greeting",
                changed_files=["src/app.py"], checks=[passing_check()],
                hashes={"src/app.py": self.app_hash()},
            )
        _, before = self.call("show")
        receipt = {
            "original_path": "plans/greeting.md",
            "archive_path": "plans/archive/2026-11-06-greeting.md",
            "plan_digest": plan.plan_digest(plan.parse_plan(str(self.plan))),
            "sha256": hashlib.sha256(self.plan.read_bytes()).hexdigest(),
        }
        archive = self.repo / receipt["archive_path"]
        archive.parent.mkdir()
        self.plan.rename(archive)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), receipt["sha256"])
        self.assertEqual(plan.plan_digest(plan.parse_plan(str(archive))), receipt["plan_digest"])

        self.mutate(
            "close", owner=owner, result="ready",
            checks=[passing_check(), passing_check("verify archive") | {"archive": receipt}],
            hashes={receipt["archive_path"]: receipt["sha256"], "src/app.py": self.app_hash()},
        )

        code, state = self.call("show")
        self.assertEqual(code, 0)
        self.assertEqual(state["identity"], before["identity"])
        self.assertEqual(state["checkpoints"], before["checkpoints"])
        self.assertEqual(state["steps"], before["steps"])
        self.assertEqual(state["review"], before["review"])
        self.assertEqual(state["close_out"]["status"], "ready")
        self.assertEqual(state["close_out"]["checks"][-1]["archive"], receipt)
        self.assertEqual(state["close_out"]["final_hashes"][receipt["archive_path"]], receipt["sha256"])
        archived_identity = self.identity_request()
        archived_identity["identity"]["plan_realpath"] = str(archive)
        code, payload = self.call("show", archived_identity)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "state_missing")
        self.assertEqual(len(list(self.state_root.glob("*.json"))), 1)

    def test_drift_outside_verified_set_invalidates_evidence_and_readiness(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        self.complete_pending_steps(owner)
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[passing_check("python3 -m unittest discover -s tests -v")],
            hashes={"src/app.py": self.app_hash()},
        )

        verified_hash = self.app_hash()
        (self.repo / "src" / "app.py").write_text(APP_PY + "\n# sneaky\n", encoding="utf-8")
        self.assertEqual(
            self.error_code("release", owner=owner, reason="stop", watched_files={"src/app.py": verified_hash}),
            "unexplained_drift",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "blocked")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["flags"], ["needs_revalidation"])

    def test_authorized_edit_advances_checkpoint_without_drift(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        original_hash = self.app_hash()
        (self.repo / "src" / "app.py").write_text(APP_PY + "\n# authorized\n", encoding="utf-8")
        edited_hash = self.app_hash()
        self.assertNotEqual(edited_hash, original_hash)

        self.mutate(
            "start",
            owner=owner,
            step=2,
            watched_files={"src/app.py": edited_hash},
        )
        self.mutate(
            "record",
            owner=owner,
            step=2,
            handback="authorized tidy",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": edited_hash},
        )
        code, state = self.call("show")
        self.assertEqual(state["checkpoints"][-1]["hashes"]["src/app.py"], edited_hash)
        self.assertNotIn("needs_revalidation", state["steps"]["2"]["evidence"][-1].get("flags", []))

        self.mutate(
            "release",
            owner=owner,
            reason="stop",
            watched_files={"src/app.py": edited_hash},
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "pending")
        self.assertNotIn("needs_revalidation", state["steps"]["2"]["evidence"][-1].get("flags", []))

    def test_amend_replaces_accepted_digest_and_resets_closeout(self):
        owner = self.claim_owner()
        old_digest, new_digest = "d" * 64, "e" * 64
        self.mutate("review", digest=old_digest, result="clean")
        self.mutate("accept", digest=old_digest, criteria=["A1"])
        self.complete_pending_steps(owner)
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        self.mutate(
            "amend",
            owner=owner,
            prior_digest=old_digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            decision="user approved wider scope",
            invalidated=["close-out"],
        )
        code, state = self.call("show")
        self.assertEqual(state["accepted_digest"], new_digest)
        self.assertEqual(state["close_out"]["status"], "pending")
        self.assertEqual(state["amendments"][-1]["prior_digest"], old_digest)
        self.assertEqual(state["steps"]["2"]["status"], "pending")

    def test_amend_with_wrong_prior_digest_is_refused(self):
        owner = self.claim_owner()
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest="f" * 64,
                replacement_digest="e" * 64,
                affected_steps=[1],
                decision="nope",
            ),
            "prior_digest_mismatch",
        )

    def test_branch_change_stops_for_reconciliation(self):
        request = self.identity_request(expected_revision=self.revision)
        request["identity"]["branch"] = "feature"
        code, payload = self.call("review", {**request, "digest": "d" * 64, "result": "clean"})
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "branch_changed")

    def test_head_change_stops_for_reconciliation(self):
        request = self.identity_request(expected_revision=self.revision)
        request["identity"]["head"] = "0" * 40
        code, payload = self.call("review", {**request, "digest": "d" * 64, "result": "clean"})
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "head_changed")
        self.assertEqual(payload["error"]["prior_head"], self.head)

    def test_close_out_invalidated_by_drift_after_ready(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        self.complete_pending_steps(owner)
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[passing_check("python3 -m unittest discover -s tests -v")],
            hashes={"src/app.py": self.app_hash()},
        )
        verified_hash = self.app_hash()
        (self.repo / "src" / "app.py").write_text(APP_PY + "\n# drift\n", encoding="utf-8")
        self.assertEqual(
            self.error_code("release", owner=owner, reason="stop", watched_files={"src/app.py": verified_hash}),
            "unexplained_drift",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "blocked")
        self.assertEqual(state["close_out"]["final_hashes"], {})
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["flags"], ["needs_revalidation"])

    def test_close_out_invalidated_by_amendment_after_ready(self):
        owner = self.claim_owner()
        old_digest, new_digest = "d" * 64, "e" * 64
        self.mutate("review", digest=old_digest, result="clean")
        self.mutate("accept", digest=old_digest, criteria=["A1"])
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        self.complete_pending_steps(owner)
        self.mutate(
            "close",
            owner=owner,
            result="ready",
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        self.mutate(
            "amend",
            owner=owner,
            prior_digest=old_digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            decision="user approved wider scope",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "pending")
        self.assertEqual(state["close_out"]["final_hashes"], {})

    def test_later_step_editing_earlier_anchor_preserves_history(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        first_checkpoint = self.app_hash()
        (self.repo / "src" / "app.py").write_text(APP_PY.replace('def greet():', 'def hello():'), encoding="utf-8")
        self.start_step(owner, 2)
        self.mutate(
            "record",
            owner=owner,
            step=2,
            handback="removed greet anchor",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["status"], "done")
        self.assertEqual(state["checkpoints"][0]["hashes"]["src/app.py"], first_checkpoint)
        self.assertEqual(state["steps"]["2"]["status"], "done")

    def test_external_edit_after_checkpoint_requires_revalidation(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.start_step(owner, 1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        verified_hash = self.app_hash()
        (self.repo / "src" / "app.py").write_text(APP_PY + "\n# external\n", encoding="utf-8")
        self.assertEqual(
            self.error_code("start", owner=owner, step=2, watched_files={"src/app.py": verified_hash}),
            "unexplained_drift",
        )
        code, state = self.call("show")
        self.assertEqual(state["close_out"]["status"], "blocked")
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["flags"], ["needs_revalidation"])

    def test_watched_file_drift_can_be_revalidated_before_close(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
        )
        verified_hash = self.app_hash()
        (self.repo / "src" / "app.py").write_text(APP_PY + "\n# external\n", encoding="utf-8")
        self.assertEqual(
            self.error_code("start", owner=owner, step=2, watched_files={"src/app.py": verified_hash}),
            "unexplained_drift",
        )
        drifted = self.call("show")[1]
        self.revision = drifted["revision"]

        self.mutate(
            "recover", owner=owner, step=1, outcome="done",
            evidence={"checks": [passing_check()], "hashes": {"src/app.py": self.app_hash()}},
        )

        self.assertEqual(drifted["steps"]["1"]["status"], "needs-manual-check")
        self.assertEqual(drifted["pending_revalidation"], ["1"])
        revalidated = self.call("show")[1]
        self.assertEqual(revalidated["pending_revalidation"], [])
        self.assertEqual(revalidated["steps"]["1"]["evidence"][0]["flags"], ["needs_revalidation"])
        self.assertEqual(revalidated["checkpoints"][0]["hashes"]["src/app.py"], verified_hash)
        self.assertEqual(revalidated["checkpoints"][-1]["hashes"]["src/app.py"], self.app_hash())
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "ready")

    def unlink_watched_app_after_record(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
        )
        verified_hash = self.app_hash()
        (self.repo / "src" / "app.py").unlink()
        return owner, {"src/app.py": verified_hash}

    def test_missing_watched_path_with_indeterminable_head_state_is_drift(self):
        owner, watched = self.unlink_watched_app_after_record()
        self.assertEqual(self.error_code("start", owner=owner, step=2, watched_files=watched), "unexplained_drift")

    def test_missing_watched_path_with_failed_git_launch_is_drift(self):
        owner, watched = self.unlink_watched_app_after_record()
        with patch.object(run_state.subprocess, "run", side_effect=OSError("git unavailable")):
            self.assertEqual(self.error_code("start", owner=owner, step=2, watched_files=watched), "unexplained_drift")

    def watched_directory_symlink_after_record(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
        )
        (self.repo / "plugins/code-flow/skills/spec-new").mkdir(parents=True)
        (self.repo / "skills").mkdir()
        link = self.repo / "skills" / "spec-new"
        os.symlink("../plugins/code-flow/skills/spec-new", link)
        return owner, link, hashlib.sha256(os.fsencode(os.readlink(link))).hexdigest()

    def test_watched_directory_symlink_untouched_has_no_drift(self):
        owner, _, link_digest = self.watched_directory_symlink_after_record()
        self.mutate("start", owner=owner, step=2, watched_files={"skills/spec-new": link_digest})
        code, state = self.call("show")
        self.assertEqual(state["steps"]["2"]["status"], "running")
        self.assertEqual(state["close_out"]["status"], "pending")

    def test_watched_directory_symlink_retargeted_is_drift(self):
        owner, link, link_digest = self.watched_directory_symlink_after_record()
        (self.repo / "plugins/code-flow/skills/other").mkdir(parents=True)
        link.unlink()
        os.symlink("../plugins/code-flow/skills/other", link)
        self.assertEqual(
            self.error_code("start", owner=owner, step=2, watched_files={"skills/spec-new": link_digest}),
            "unexplained_drift",
        )

    def test_watched_directory_symlink_deleted_is_drift(self):
        owner, link, link_digest = self.watched_directory_symlink_after_record()
        link.unlink()
        self.assertEqual(
            self.error_code("start", owner=owner, step=2, watched_files={"skills/spec-new": link_digest}),
            "unexplained_drift",
        )

    def test_watched_real_directory_reports_usage_without_traceback(self):
        owner, _, _ = self.watched_directory_symlink_after_record()
        request = self.identity_request(
            expected_revision=self.revision, owner=owner, step=2, watched_files={"src": "0" * 64})
        code, payload = self.call("start", request)
        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_legacy_done_step_with_latest_drift_flag_can_be_recovered(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
        )
        legacy = self.call("show")[1]
        del legacy["pending_revalidation"]
        del legacy["identity_history"]
        legacy["steps"]["1"]["evidence"][-1]["flags"] = ["needs_revalidation"]
        legacy["close_out"]["status"] = "blocked"
        self.assertEqual(legacy["steps"]["1"]["status"], "done")
        key = run_state.state_key(legacy["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        migrated = self.call("show")[1]
        self.assertEqual(migrated["pending_revalidation"], ["1"])
        self.assertEqual(migrated["steps"]["1"]["status"], "needs-manual-check")

        self.mutate(
            "recover", owner=owner, step=1, outcome="done",
            evidence={"checks": [passing_check()], "hashes": {"src/app.py": self.app_hash()}},
        )

        recovered = self.call("show")[1]
        self.assertEqual(recovered["pending_revalidation"], [])
        self.assertEqual(recovered["steps"]["1"]["status"], "done")
        self.assertEqual(recovered["steps"]["1"]["evidence"][0], legacy["steps"]["1"]["evidence"][0])
        self.assertEqual(recovered["steps"]["1"]["evidence"][-1]["flags"], [])
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "ready")


class RunStateValidationTests(RunStateTestBase):
    def test_init_rejects_incomplete_or_contradictory_plan_graph_without_writing(self):
        self.plan.write_text(fixture_plan([1, 2], {2: [1]}), encoding="utf-8")
        cases = (
            ({"step_ids": [1]}, "step_graph_mismatch"),
            ({"step_ids": [1, 2], "step_dependencies": {}}, "step_graph_mismatch"),
            ({"step_ids": [1, 2], "step_dependencies": {"2": [1]}}, "step_graph_mismatch"),
            ({"step_ids": [1, 2], "step_dependencies": {"1": [], "2": []}}, "step_graph_mismatch"),
            ({"step_ids": [1, 1, 2]}, "step_graph_mismatch"),
            ({"step_ids": [1, 2, 3]}, "step_graph_mismatch"),
            ({"step_ids": [1, 2], "step_dependencies": {"2": [1], "3": []}}, "step_graph_mismatch"),
            ({"step_ids": [1, 2], "step_dependencies": {"2": [1, 1]}}, "step_graph_mismatch"),
            ({"step_ids": [1, 2], "step_dependencies": {"2": [9]}}, "unknown_dependency"),
            ({"step_ids": [1, 2], "step_dependencies": {"2": [2]}}, "self_dependency"),
            ({"step_ids": [1, 2], "step_dependencies": {"1": [2], "2": [1]}}, "cyclic_dependency"),
        )
        for fields, expected_error in cases:
            with self.subTest(fields=fields):
                fresh_root = self.state_root / "fresh"
                code, payload = self.call("init", self.identity_request() | fields, state_root=fresh_root)
                self.assertEqual((code, payload["error"]["code"]), (1, expected_error))
                self.assertEqual(list(fresh_root.glob("*.json")), [])

    def test_init_derives_complete_graph_when_fields_are_omitted(self):
        self.plan.write_text(fixture_plan([1, 2], {2: [1]}), encoding="utf-8")
        for index, fields in enumerate(({}, {"step_ids": [2, 1]}, {"step_dependencies": {"1": [], "2": [1]}})):
            with self.subTest(fields=fields):
                fresh_root = self.state_root / f"derived-{index}"
                request = self.identity_request() | fields
                code, initialized = self.call("init", request, state_root=fresh_root)
                self.assertEqual(code, 0, initialized)
                code, state = self.call("show", self.identity_request(), state_root=fresh_root)
                self.assertEqual(code, 0, state)
                self.assertEqual(state["steps"], {
                    "1": {"status": "pending", "evidence": [], "depends_on": []},
                    "2": {"status": "pending", "evidence": [], "depends_on": ["1"]},
                })

    def test_legacy_invalid_readiness_is_downgraded_without_read_only_writes(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})
        self.mutate("release", owner=owner)
        original = self.call("show")[1]
        state_path = next(self.state_root.glob("*.json"))
        cases = (
            (("steps", "2", "status"), "pending", "steps_incomplete"),
            (("accepted_digest",), None, "not_authorized"),
            (("accepted_digest",), "e" * 64, "review_stale"),
            (("review",), {"digest": None, "result": None, "receipts": []}, "review_missing"),
            (("review",), {
                "digest": "d" * 64, "result": "needs revision",
                "receipts": [{"digest": "d" * 64, "result": "needs revision"}],
            }, "review_unclean"),
            (("pending_proposal",), {
                "prior_digest": "d" * 64, "replacement_digest": "e" * 64,
                "prior_criteria": ["A1"], "new_criteria": ["A1", "A2"],
                "prior_criterion_text": {}, "criterion_text": {},
                "affected_steps": [], "invalidated": [], "decision": None,
                "step_ids": None, "step_dependencies": {},
            }, "authorization_pending"),
            (("pending_revalidation",), ["1"], "needs_revalidation"),
            (("close_out", "checks"), [], "checks_incomplete"),
            (("close_out", "checks"), [{"kind": "auto", "exit_status": 1, "status": "failed"}], "checks_incomplete"),
            (("close_out", "checks"), [passing_check() | {"exit_status": 1, "status": "passed"}], "checks_incomplete"),
            (("close_out", "checks"), [passing_check() | {"kind": "manual", "status": "passed"}], "checks_incomplete"),
            (("close_out", "checks"), [passing_check() | {"timed_out": True, "status": "passed"}], "checks_incomplete"),
            (("close_out", "final_hashes"), {}, "missing_hashes"),
            (("close_out", "final_hashes"), {"src/app.py": ""}, "missing_hashes"),
        )
        for path, value, error in cases:
            with self.subTest(path=path, value=value):
                state = json.loads(json.dumps(original))
                target = state
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                state_path.write_text(json.dumps(state), encoding="utf-8")
                before = state_path.read_bytes()
                self.revision = state["revision"]
                code, loaded = self.call("show")
                self.assertEqual(code, 0, loaded)
                self.assertEqual(loaded["close_out"]["status"], "blocked")
                self.assertIn(error, loaded["close_out"]["reason"])
                self.assertEqual(loaded["close_out"]["final_hashes"], {})
                for field in ("revision", "steps", "review", "checkpoints", "ownership", "amendments", "identity_history"):
                    self.assertEqual(loaded[field], state[field])
                self.assertEqual(loaded["close_out"]["checks"], state["close_out"]["checks"])
                self.assertEqual(state_path.read_bytes(), before)
                resumed_owner = self.claim_owner()
                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["close_out"], loaded["close_out"])
                self.assertEqual(persisted["ownership"]["owner"], resumed_owner)
                self.assertEqual(persisted["steps"], loaded["steps"])

    def test_legacy_ready_with_invented_receipt_is_corrupt_without_writes(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})
        state_path = next(self.state_root.glob("*.json"))
        state = self.call("show")[1]
        state["review"]["receipts"] = []
        state_path.write_text(json.dumps(state), encoding="utf-8")
        before = state_path.read_bytes()
        for command in ("show", "claim", "close"):
            code, payload = self.call(command, self.identity_request(
                expected_revision=self.revision, owner=owner, result="ready",
                checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
            ))
            self.assertEqual((code, payload["error"]["code"]), (2, "corrupt_state"))
            self.assertEqual(state_path.read_bytes(), before)

    def test_legacy_ready_is_checked_after_revalidation_migration(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})
        state_path = next(self.state_root.glob("*.json"))
        legacy = self.call("show")[1]
        del legacy["pending_revalidation"]
        del legacy["identity_history"]
        legacy["steps"]["1"]["evidence"][-1]["flags"] = ["needs_revalidation"]
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        before = state_path.read_bytes()
        code, loaded = self.call("show")
        self.assertEqual(code, 0, loaded)
        self.assertEqual(loaded["pending_revalidation"], ["1"])
        self.assertEqual(loaded["steps"]["1"]["status"], "needs-manual-check")
        self.assertEqual(loaded["close_out"]["status"], "blocked")
        self.assertIn("needs_revalidation", loaded["close_out"]["reason"])
        self.assertEqual(loaded["close_out"]["checks"], legacy["close_out"]["checks"])
        self.assertEqual(loaded["checkpoints"], legacy["checkpoints"])
        self.assertEqual(state_path.read_bytes(), before)

    def test_init_cannot_skip_inspection_of_a_missing_plan(self):
        request = self.identity_request(step_ids=[1])
        request["identity"]["plan_realpath"] = str(self.repo / "plans" / "missing.md")
        fresh_root = self.state_root / "fresh"
        code, payload = self.call("init", request, state_root=fresh_root)
        self.assertEqual((code, payload["error"]["code"]), (1, "invalid_plan"))
        self.assertEqual(list(fresh_root.glob("*.json")), [])

    def test_malformed_consumed_request_fields_return_usage_without_writes(self):
        state_path = next(self.state_root.glob("*.json"))
        before = state_path.read_bytes()
        cases = (
            ("init", {"step_ids": None}),
            ("init", {"step_ids": [1], "step_dependencies": [1]}),
            ("init", {"step_ids": [1], "step_dependencies": {"1": None}}),
            ("init", {"step_ids": [True]}),
            ("review", {"digest": [], "result": "clean"}),
            ("accept", {"digest": "d", "criteria": [1]}),
            ("accept", {"digest": "d", "criteria": ["A1"], "criterion_text": []}),
            ("claim", {"expected_revision": True}),
            ("release", {"owner": "owner", "watched_files": [1]}),
            ("start", {"owner": "owner", "step": {}}),
            ("record", {"owner": "owner", "step": 1, "handback": [1]}),
            ("record", {"owner": "owner", "step": 1, "handback": "done", "checks": [1]}),
            ("block", {"owner": [], "step": 1}),
            ("takeover", {"previous_owner": "owner", "evidence": []}),
            ("recover", {"owner": "owner", "step": 1, "outcome": [], "evidence": {}}),
            ("recover", {"owner": "owner", "step": 1, "outcome": "done", "evidence": {"checks": [1]}}),
            ("recover", {"owner": "owner", "step": 1, "outcome": "done", "evidence": {"hashes": []}}),
            ("amend", {"owner": "owner", "prior_digest": "d", "replacement_digest": "e", "affected_steps": {}}),
            ("close", {"owner": "owner", "result": [], "checks": []}),
            ("close", {"owner": "owner", "checks": [{"argv": [1]}]}),
            ("close", {"owner": "owner", "checks": [{"exit_status": False}]}),
            ("close", {"owner": "owner", "checks": [{"timed_out": "true"}]}),
            ("close", {"owner": "owner", "hashes": {"src/app.py": []}}),
        )
        for command, fields in cases:
            with self.subTest(command=command, fields=fields):
                code, payload = self.call(command, self.identity_request(expected_revision=self.revision) | fields)
                self.assertEqual(code, 2, payload)
                self.assertEqual(payload["error"]["code"], "usage")
                self.assertEqual(state_path.read_bytes(), before)
        fresh_root = self.state_root / "fresh"
        code, payload = self.call("init", self.identity_request() | {"step_ids": None}, state_root=fresh_root)
        self.assertEqual((code, payload["error"]["code"]), (2, "usage"))
        self.assertFalse(fresh_root.exists())

    def test_malformed_versioned_nested_state_returns_corrupt_state_without_writes(self):
        state_path = next(self.state_root.glob("*.json"))
        original = self.call("show")[1]
        cases = (
            (("version",), 2),
            (("revision",), True),
            (("identity", "head"), []),
            (("ownership",), []),
            (("ownership", "history"), {}),
            (("ownership", "state"), "unknown"),
            (("review",), []),
            (("review",), {"receipts": []}),
            (("review", "receipts"), None),
            (("accepted_criteria",), [1]),
            (("accepted_criterion_text",), []),
            (("steps",), []),
            (("steps", "1"), []),
            (("steps", "1", "status"), []),
            (("steps", "1", "evidence"), [1]),
            (("steps", "1", "evidence"), [{"flags": None}]),
            (("steps", "1", "evidence"), [{"checks": [1]}]),
            (("steps", "1", "depends_on"), None),
            (("close_out",), []),
            (("close_out", "final_hashes"), []),
            (("close_out", "checks"), [1]),
            (("close_out", "checks"), [{"timed_out": "true"}]),
            (("checkpoints",), [{"hashes": []}]),
            (("amendments",), {}),
            (("identity_history",), {}),
            (("pending_revalidation",), {}),
            (("pending_proposal",), []),
            (("pending_proposal",), {"replacement_digest": "e"}),
            (("pending_proposal",), {
                "prior_digest": "d", "replacement_digest": "e", "decision": None,
                "affected_steps": [1], "step_dependencies": [1],
            }),
        )
        for path, value in cases:
            with self.subTest(path=path, value=value):
                state = json.loads(json.dumps(original))
                target = state
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                state_path.write_text(json.dumps(state), encoding="utf-8")
                before = state_path.read_bytes()
                for command in ("show", "claim"):
                    code, payload = self.call(command, self.identity_request(expected_revision=self.revision))
                    self.assertEqual(code, 2, payload)
                    self.assertEqual(payload["error"]["code"], "corrupt_state")
                    self.assertEqual(state_path.read_bytes(), before)

    def test_malformed_nested_history_cannot_authorize_close_or_mutate_state(self):
        owner = self.claim_owner()
        request = self.identity_request(expected_revision=self.revision, owner=owner, prior_head=self.head)
        request["identity"]["head"] = "a" * 40
        code, reconciled = self.call("reconcile-head", request)
        self.assertEqual(code, 0, reconciled)
        self.revision = reconciled["revision"]
        self.head = request["identity"]["head"]
        self.accept_plan()
        self.mutate("amend", owner=owner, prior_digest="d" * 64,
                    replacement_digest="e" * 64, affected_steps=[], decision="clarify implementation")
        self.mutate("review", digest="e" * 64, result="clean")
        self.complete_pending_steps(owner)
        original = self.call("show")[1]
        state_path = next(self.state_root.glob("*.json"))
        cases = (
            (("review", "receipts"), []),
            (("review", "receipts"), [{}]),
            (("review", "receipts"), [{"digest": "e" * 64, "result": []}]),
            (("review", "receipts"), [{}, {"digest": "e" * 64, "result": "clean"}]),
            (("review", "receipts"), [{"digest": "d" * 64, "result": "clean"}]),
            (("review", "receipts"), [{"digest": "e" * 64, "result": "needs revision"}]),
            (("review", "digest"), None),
            (("review", "result"), None),
            (("ownership", "history"), [{}]),
            (("ownership", "history"), [{"kind": "unknown"}]),
            (("ownership", "history"), [{"kind": "release", "reason": []}]),
            (("ownership", "history"), [{"kind": "release"}]),
            (("identity_history",), [{}]),
            (("identity_history", 0, "kind"), "unknown"),
            (("identity_history", 0, "prior_head"), []),
            (("identity_history", 0, "head"), None),
            (("identity_history", 0, "owner"), []),
            (("identity_history", 0, "revision"), True),
            (("amendments",), [{}]),
            (("amendments", 0, "prior_digest"), []),
            (("amendments", 0, "replacement_digest"), []),
            (("amendments", 0, "prior_criteria"), [1]),
            (("amendments", 0, "prior_criterion_text"), []),
            (("amendments", 0, "new_criteria"), None),
            (("amendments", 0, "criterion_text"), []),
            (("amendments", 0, "affected_steps"), {}),
            (("amendments", 0, "invalidated"), {}),
            (("amendments", 0, "decision"), []),
            (("steps", "1", "evidence"), [{}]),
            (("steps", "1", "evidence", 0, "status"), None),
            (("steps", "1", "evidence", 0, "handback"), []),
            (("steps", "1", "evidence", 0, "checks"), [{}]),
            (("steps", "1", "evidence", 0, "checks"), [{"status": "unknown"}]),
            (("close_out", "checks"), [{}]),
            (("close_out", "reason"), []),
        )
        for path, value in cases:
            with self.subTest(path=path, value=value):
                state = json.loads(json.dumps(original))
                target = state
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                state_path.write_text(json.dumps(state), encoding="utf-8")
                before = state_path.read_bytes()
                for command in ("show", "claim", "close"):
                    code, payload = self.call(command, self.identity_request(
                        expected_revision=self.revision, owner=owner, result="ready",
                        checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
                    ))
                    self.assertEqual((code, payload["error"]["code"]), (2, "corrupt_state"))
                    self.assertEqual(state_path.read_bytes(), before)


class RunStateHeadReconciliationTests(RunStateTestBase):
    def reconcile_request(self, owner, **overrides):
        request = self.identity_request(
            expected_revision=self.revision,
            owner=owner,
            prior_head=self.head,
            **overrides,
        )
        request["identity"]["head"] = "a" * 40
        return request

    def test_reconcile_preserves_baseline_history_and_marks_evidence(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], changed_files=["src/app.py"],
            hashes={"src/app.py": self.app_hash()},
        )
        self.mutate(
            "amend", owner=owner, prior_digest="d" * 64,
            replacement_digest="e" * 64, affected_steps=[2],
            decision="clarify remaining implementation",
        )
        self.mutate(
            "amend", owner=owner, prior_digest="e" * 64,
            replacement_digest="f" * 64, affected_steps=[2],
            new_criteria=["A1", "A2"], decision="propose added behavior",
        )
        self.mutate(
            "close", owner=owner, result="blocked", checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        before = self.call("show")[1]
        request = self.reconcile_request(owner)

        code, payload = self.call("reconcile-head", request)

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["revision"], self.revision + 1)
        code, after = self.call("show", {"identity": request["identity"]})
        self.assertEqual(code, 0, after)
        expected = json.loads(json.dumps(before))
        expected["revision"] += 1
        expected["identity"]["head"] = "a" * 40
        expected["steps"]["1"]["status"] = "needs-manual-check"
        expected["steps"]["1"]["evidence"][-1]["flags"] = ["needs_revalidation"]
        expected["pending_revalidation"] = ["1"]
        expected["close_out"]["status"] = "blocked"
        expected["close_out"]["final_hashes"] = {}
        expected.setdefault("identity_history", []).append({
            "kind": "reconcile-head", "prior_head": self.head,
            "head": "a" * 40, "revision": self.revision + 1, "owner": owner,
        })
        self.assertEqual(after, expected)
        self.assertEqual(self.call("show")[1]["error"]["code"], "head_changed")
        code, payload = self.call("close", {
            "identity": request["identity"], "expected_revision": after["revision"],
            "owner": owner, "result": "ready", "checks": [passing_check()],
            "hashes": {"src/app.py": self.app_hash()},
        })
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["error"]["code"], "needs_revalidation")
        self.assertEqual(self.call("show", {"identity": request["identity"]})[1], after)

    def test_recover_done_revalidates_reconciled_evidence_and_allows_close(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], changed_files=["src/app.py"],
            hashes={"src/app.py": self.app_hash()},
        )
        checkpoint = self.call("show")[1]["checkpoints"][0]
        request = self.reconcile_request(owner)
        code, payload = self.call("reconcile-head", request)
        self.assertEqual(code, 0, payload)
        self.revision = payload["revision"]
        self.head = request["identity"]["head"]

        self.mutate(
            "recover", owner=owner, step=1, outcome="done",
            evidence={
                "checks": [passing_check("verify greeting on reconciled HEAD")],
                "changed_files": ["src/app.py"],
                "hashes": {"src/app.py": self.app_hash()},
            },
        )
        revalidated = self.call("show")[1]
        self.assertEqual(revalidated["steps"]["1"]["status"], "done")
        self.assertEqual(revalidated["steps"]["1"]["evidence"][0]["flags"], ["needs_revalidation"])
        self.assertEqual(revalidated["steps"]["1"]["evidence"][-1]["flags"], [])
        self.assertEqual(revalidated["pending_revalidation"], [])
        self.assertEqual(revalidated["checkpoints"][0], checkpoint)
        self.assertEqual(len(revalidated["checkpoints"]), 2)
        self.assertEqual(revalidated["close_out"]["status"], "blocked")

        self.complete_pending_steps(owner)
        revalidated = self.call("show")[1]
        self.mutate(
            "close", owner=owner, result="ready", checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )

        closed = self.call("show")[1]
        self.assertEqual(closed["close_out"]["status"], "ready")
        self.assertEqual(closed["steps"], revalidated["steps"])
        self.assertEqual(closed["checkpoints"], revalidated["checkpoints"])

    def assert_recovery_keeps_revalidation_required(self, outcome):
        owner = self.reconcile_completed_step()

        self.mutate("recover", owner=owner, step=1, outcome=outcome)

        state = self.call("show")[1]
        self.assertEqual(state["steps"]["1"]["evidence"][-1]["flags"], ["needs_revalidation"])
        self.assert_close_needs_revalidation(owner)

    def test_recover_pending_keeps_revalidation_required(self):
        self.assert_recovery_keeps_revalidation_required("pending")

    def test_recover_blocked_keeps_revalidation_required(self):
        self.assert_recovery_keeps_revalidation_required("blocked")

    def test_block_does_not_bypass_revalidation(self):
        owner = self.reconcile_completed_step()

        self.mutate("block", owner=owner, step=1, reason="revalidation interrupted")

        self.assert_close_needs_revalidation(owner)

    def reconcile_completed_step(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="verified greeting",
            checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
        )
        request = self.reconcile_request(owner)
        code, payload = self.call("reconcile-head", request)
        self.assertEqual(code, 0, payload)
        self.revision = payload["revision"]
        self.head = request["identity"]["head"]
        return owner

    def test_amend_cannot_remove_revalidation_until_verified_record(self):
        owner = self.reconcile_completed_step()
        flagged_evidence = self.call("show")[1]["steps"]["1"]["evidence"]

        self.mutate(
            "amend", owner=owner, prior_digest="d" * 64,
            replacement_digest="e" * 64, affected_steps=[1],
            decision="clarify implementation without changing acceptance",
        )

        self.assert_close_needs_revalidation(owner)
        amended = self.call("show")[1]
        self.assertEqual(amended["pending_revalidation"], ["1"])
        self.assertEqual(amended["steps"]["1"]["evidence"], flagged_evidence)
        self.mutate("review", digest="e" * 64, result="clean")
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="revalidation failed",
            checks=[{"command": "verify greeting", "exit_status": 1, "kind": "auto"}],
        )
        self.assertEqual(self.call("show")[1]["pending_revalidation"], ["1"])
        self.assert_close_needs_revalidation(owner)
        self.mutate("recover", owner=owner, step=1, outcome="pending")
        self.start_step(owner, 1)
        self.mutate(
            "record", owner=owner, step=1, handback="revalidated greeting",
            checks=[passing_check()], hashes={"src/app.py": self.app_hash()},
        )
        revalidated = self.call("show")[1]
        self.assertEqual(revalidated["pending_revalidation"], [])
        self.assertEqual(revalidated["steps"]["1"]["evidence"][0]["flags"], ["needs_revalidation"])
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "ready")

    def test_recover_without_verified_done_keeps_durable_obligation(self):
        owner = self.reconcile_completed_step()
        self.assertEqual(
            self.error_code(
                "recover", owner=owner, step=1, outcome="done",
                evidence={"checks": [passing_check()]},
            ),
            "missing_hashes",
        )
        self.assertEqual(self.call("show")[1]["pending_revalidation"], ["1"])
        self.mutate("recover", owner=owner, step=1, outcome="blocked")
        self.assertEqual(self.call("show")[1]["pending_revalidation"], ["1"])

    def test_durable_obligation_blocks_close_without_evidence(self):
        owner = self.reconcile_completed_step()
        state = self.call("show")[1]
        state["steps"]["1"]["evidence"] = []
        key = run_state.state_key(state["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_close_needs_revalidation(owner)

    def test_legacy_revalidated_history_does_not_restore_obligation(self):
        owner = self.reconcile_completed_step()
        self.mutate(
            "recover", owner=owner, step=1, outcome="done",
            evidence={"checks": [passing_check()], "hashes": {"src/app.py": self.app_hash()}},
        )
        self.complete_pending_steps(owner)
        state = self.call("show")[1]
        del state["pending_revalidation"]
        key = run_state.state_key(state["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.mutate("close", owner=owner, result="ready", checks=[passing_check()],
                    hashes={"src/app.py": self.app_hash()})

        closed = self.call("show")[1]
        self.assertEqual(closed["pending_revalidation"], [])
        self.assertEqual(closed["steps"], state["steps"])

    def test_legacy_flagged_state_gets_durable_revalidation_obligation(self):
        owner = self.reconcile_completed_step()
        state = self.call("show")[1]
        state.pop("pending_revalidation", None)
        key = run_state.state_key(state["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.mutate(
            "amend", owner=owner, prior_digest="d" * 64,
            replacement_digest="e" * 64, affected_steps=[1],
            decision="clarify implementation without changing acceptance",
        )

        self.assertEqual(self.call("show")[1]["pending_revalidation"], ["1"])
        self.assert_close_needs_revalidation(owner)

    def test_reconcile_rejects_mismatched_identity_revision_and_owner(self):
        cases = [
            ("prior_head", None, "b" * 40, "head_changed"),
            ("expected_revision", None, -1, "usage"),
            ("expected_revision", None, None, "usage"),
            ("owner", None, "wrong-owner", "owner_mismatch"),
            ("identity", "git_dir", "/other/.git", "identity_mismatch"),
            ("identity", "branch", "other", "branch_changed"),
            ("identity", "worktree_root", "/other", "identity_mismatch"),
            ("identity", "plan_realpath", "/other/plan.md", "state_missing"),
        ]
        owner = self.claim_owner()
        before = self.call("show")[1]
        for field, identity_field, value, error in cases:
            with self.subTest(field=field, identity_field=identity_field):
                request = self.reconcile_request(owner)
                target = request["identity"] if identity_field else request
                target[identity_field or field] = value
                code, payload = self.call("reconcile-head", request)
                self.assertEqual(code, 2 if error == "usage" else 1, payload)
                self.assertEqual(payload["error"]["code"], error)
                self.assertEqual(self.call("show")[1], before)

    def test_reconcile_refuses_any_running_step(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 2)
        before = self.call("show")[1]

        code, payload = self.call("reconcile-head", self.reconcile_request(owner))

        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["error"]["code"], "step_already_running")
        self.assertEqual(self.call("show")[1], before)

    def test_reconcile_requires_active_owner(self):
        owner = self.claim_owner()
        self.mutate("release", owner=owner)
        before = self.call("show")[1]

        code, payload = self.call("reconcile-head", self.reconcile_request(owner))

        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["error"]["code"], "owner_mismatch")
        self.assertEqual(self.call("show")[1], before)

    def test_reconcile_requires_prior_head_and_owner_fields(self):
        owner = self.claim_owner()
        before = self.call("show")[1]
        for field in ("prior_head", "owner"):
            with self.subTest(field=field):
                request = self.reconcile_request(owner)
                del request[field]
                code, payload = self.call("reconcile-head", request)
                self.assertEqual(code, 2, payload)
                self.assertEqual(payload["error"]["code"], "usage")
                self.assertEqual(self.call("show")[1], before)

    def test_reconcile_requires_string_prior_head(self):
        owner = self.claim_owner()
        request = self.reconcile_request(owner)
        request["prior_head"] = None
        before = self.call("show")[1]

        code, payload = self.call("reconcile-head", request)

        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["error"]["code"], "usage")
        self.assertEqual(self.call("show")[1], before)

    def test_reconcile_requires_a_changed_head(self):
        owner = self.claim_owner()
        request = self.reconcile_request(owner)
        request["identity"]["head"] = self.head
        before = self.call("show")[1]

        code, payload = self.call("reconcile-head", request)

        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["error"]["code"], "head_unchanged")
        self.assertEqual(self.call("show")[1], before)

    def test_reconcile_reads_legacy_state_and_appends_identity_history(self):
        owner = self.claim_owner()
        state = self.call("show")[1]
        state.pop("identity_history", None)
        key = run_state.state_key(state["identity"])
        state_path = self.state_root / f"{key}.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        request = self.reconcile_request(owner)
        code, payload = self.call("reconcile-head", request)
        self.assertEqual(code, 0, payload)
        request["prior_head"] = request["identity"]["head"]
        request["identity"]["head"] = "b" * 40
        request["expected_revision"] = payload["revision"]

        code, payload = self.call("reconcile-head", request)

        self.assertEqual(code, 0, payload)
        after = self.call("show", {"identity": request["identity"]})[1]
        self.assertEqual(len(after["identity_history"]), 2)
        self.assertEqual(after["identity_history"][0]["prior_head"], self.head)
        self.assertEqual(after["identity_history"][1]["prior_head"], "a" * 40)
        self.assertEqual(after["identity_history"][1]["head"], "b" * 40)


class RunStateDependencyTests(RunStateTestBase):
    DEFAULT_STEP_IDS = [1, 2]
    DEFAULT_STEP_DEPENDENCIES = {1: [], 2: [1]}

    def test_dependency_readiness_blocks_start(self):
        owner = self.mutate("claim")["owner"]
        self.accept_plan()
        self.assertEqual(self.error_code("start", owner=owner, step=2), "dependency_not_done")

    def test_dependency_done_allows_start(self):
        owner = self.mutate("claim")["owner"]
        self.accept_plan()
        self.mutate("start", owner=owner, step=1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": hashlib.sha256((self.repo / "src" / "app.py").read_bytes()).hexdigest()},
        )
        self.mutate("start", owner=owner, step=2)
        code, state = self.call("show")
        self.assertEqual(state["steps"]["2"]["status"], "running")

    def test_duplicate_dispatch_of_running_step_is_refused(self):
        owner = self.mutate("claim")["owner"]
        self.accept_plan()
        self.mutate("start", owner=owner, step=1)
        self.assertEqual(self.error_code("start", owner=owner, step=1), "step_not_pending")


class RunStateAmendmentTests(RunStateTestBase):
    DEFAULT_STEP_IDS = [1, 2, 3]
    DEFAULT_STEP_DEPENDENCIES = {1: [], 2: [1], 3: [1, 2]}

    def mutate(self, command, **overrides):
        if command == "amend" and "owner" not in overrides:
            state = self.call("show")[1]
            if state["ownership"]["state"] == "released":
                overrides["owner"] = super().mutate("claim")["owner"]
            else:
                overrides["owner"] = state["ownership"]["owner"]
        return super().mutate(command, **overrides)

    def test_amend_requires_owner_token(self):
        code, payload = self.call(
            "amend",
            self.identity_request(
                expected_revision=self.revision,
                prior_digest=None,
                replacement_digest="e" * 64,
                affected_steps=[],
            ),
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "usage")
        owner = self.claim_owner()
        self.assertEqual(
            self.error_code(
                "amend",
                owner="not-" + owner,
                prior_digest=None,
                replacement_digest="e" * 64,
                affected_steps=[],
            ),
            "owner_mismatch",
        )

    def test_amend_rejects_unknown_criteria(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=digest,
                replacement_digest="e" * 64,
                affected_steps=[2],
                new_criteria=["A1", "B2"],
            ),
            "unknown_criterion",
        )

    def test_amend_changes_dependencies(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.mutate("start", owner=owner, step=1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[3],
            decision="step 3 no longer needs step 2",
            step_ids=[1, 2, 3],
            step_dependencies={3: [1]},
        )
        self.mutate("review", digest=new_digest, result="clean")
        code, state = self.call("show")
        self.assertEqual(state["steps"]["3"]["depends_on"], ["1"])
        self.mutate("start", owner=owner, step=3)
        code, state = self.call("show")
        self.assertEqual(state["steps"]["3"]["status"], "running")

    def test_amend_preserves_completed_evidence(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.mutate("start", owner=owner, step=1)
        self.mutate(
            "record",
            owner=owner,
            step=1,
            handback="changed greet",
            changed_files=["src/app.py"],
            checks=[passing_check()],
            hashes={"src/app.py": self.app_hash()},
        )
        saved_evidence = self.call("show")[1]["steps"]["1"]["evidence"]
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            decision="adjust remaining work",
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "done")
        self.assertEqual(state["steps"]["1"]["evidence"], saved_evidence)
        self.assertEqual(state["steps"]["2"]["status"], "pending")

    def test_amend_preserves_dependencies_of_unnamed_steps(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.mutate(
            "amend",
            owner=owner,
            prior_digest=digest,
            replacement_digest="e" * 64,
            affected_steps=[3],
            decision="step 3 no longer needs step 2",
            step_ids=[1, 2, 3],
            step_dependencies={3: [1]},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["2"]["depends_on"], ["1"])
        self.assertEqual(state["steps"]["3"]["depends_on"], ["1"])

    def test_amend_rejects_invalid_dependencies(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=digest,
                replacement_digest="e" * 64,
                affected_steps=[],
                step_ids=[1, 2, 3],
                step_dependencies={3: [9]},
            ),
            "unknown_dependency",
        )
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=digest,
                replacement_digest="f" * 64,
                affected_steps=[],
                step_ids=[1, 2, 3],
                step_dependencies={3: [3]},
            ),
            "self_dependency",
        )
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=digest,
                replacement_digest="g" * 64,
                affected_steps=[],
                step_ids=[1, 2, 3],
                step_dependencies={2: [3], 3: [2]},
            ),
            "cyclic_dependency",
        )

    def test_amend_rejects_removed_or_renumbered_step_ids(self):
        owner = self.claim_owner()
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=None,
                replacement_digest="e" * 64,
                affected_steps=[2],
                step_ids=[1],
            ),
            "step_removed",
        )
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=None,
                replacement_digest="f" * 64,
                affected_steps=[2],
                step_ids=[1, 3],
            ),
            "step_removed",
        )

    def test_amend_appends_new_step_ids(self):
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=None,
            replacement_digest=new_digest,
            affected_steps=[],
            step_ids=[1, 2, 3, 4],
            step_dependencies={4: [1]},
        )
        code, state = self.call("show")
        self.assertEqual(state["steps"]["4"]["status"], "pending")
        self.assertEqual(state["steps"]["4"]["depends_on"], ["1"])
        self.assertEqual(state["accepted_digest"], new_digest)

    def test_amend_affected_steps_can_name_newly_appended_step(self):
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=None,
            replacement_digest=new_digest,
            affected_steps=[4],
            step_ids=[1, 2, 3, 4],
            step_dependencies={4: [1]},
        )
        code, state = self.call("show")
        self.assertEqual(set(state["steps"].keys()), {"1", "2", "3", "4"})
        self.assertEqual(state["steps"]["4"]["status"], "pending")
        self.assertEqual(state["steps"]["4"]["depends_on"], ["1"])
        self.assertEqual(state["accepted_digest"], new_digest)

    def test_amend_rejects_unknown_affected_step(self):
        owner = self.claim_owner()
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=None,
                replacement_digest="e" * 64,
                affected_steps=[99],
            ),
            "unknown_step",
        )

    def test_amend_rejects_unknown_step_before_baseline_change_proposal(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        self.assertEqual(
            self.error_code(
                "amend",
                owner=owner,
                prior_digest=digest,
                replacement_digest="e" * 64,
                affected_steps=[99],
                new_criteria=["A1", "A2"],
                decision="unknown step blocks proposal",
            ),
            "unknown_step",
        )
        code, state = self.call("show")
        self.assertIsNone(state["pending_proposal"])
        self.mutate("start", owner=owner, step=1)
        code, state = self.call("show")
        self.assertEqual(state["steps"]["1"]["status"], "running")

    def test_amend_creates_new_step_omitted_from_dependency_map(self):
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=None,
            replacement_digest=new_digest,
            affected_steps=[],
            step_ids=[1, 2, 3, 4],
            step_dependencies={3: [1]},
        )
        code, state = self.call("show")
        self.assertEqual(set(state["steps"].keys()), {"1", "2", "3", "4"})
        self.assertEqual(state["steps"]["4"]["status"], "pending")
        self.assertEqual(state["steps"]["4"]["depends_on"], [])
        self.assertEqual(state["steps"]["3"]["depends_on"], ["1"])
        self.assertEqual(state["accepted_digest"], new_digest)

    def test_baseline_change_blocks_start_until_authorized(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            new_criteria=["A1", "A2"],
            decision="user requested wider scope",
        )
        code, state = self.call("show")
        self.assertEqual(state["accepted_digest"], digest)
        self.assertEqual(state["accepted_criteria"], ["A1"])
        self.assertEqual(state["pending_proposal"]["replacement_digest"], new_digest)
        self.assertEqual(self.error_code("start", owner=owner, step=2), "authorization_pending")

    def test_rejected_amendment_leaves_baseline_and_history(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            new_criteria=["A1", "A2"],
            decision="user requested wider scope",
        )
        saved_state = self.call("show")[1]
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            authorization="reject",
        )
        code, state = self.call("show")
        self.assertIsNone(state["pending_proposal"])
        self.assertEqual(state["accepted_digest"], digest)
        self.assertEqual(state["accepted_criteria"], ["A1"])
        self.assertEqual(state["amendments"], saved_state["amendments"])
        self.mutate("start", owner=owner, step=1)

    def test_approved_baseline_change_applies_and_unblocks(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            new_criteria=["A1", "A2"],
            decision="user approved wider scope",
            step_ids=[1, 2, 3],
            step_dependencies={2: []},
        )
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            authorization="approve",
        )
        self.mutate("review", digest=new_digest, result="clean")
        code, state = self.call("show")
        self.assertEqual(state["accepted_digest"], new_digest)
        self.assertEqual(state["accepted_criteria"], ["A1", "A2"])
        self.assertIsNone(state["pending_proposal"])
        self.assertEqual(state["steps"]["2"]["status"], "pending")
        self.mutate("start", owner=owner, step=2)

    def test_amendment_invalidates_review_receipt(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            decision="reword step 2",
        )
        code, state = self.call("show")
        self.assertEqual(state["review"]["digest"], digest)
        self.assertEqual(state["accepted_digest"], new_digest)
        self.assertEqual(self.error_code("start", owner=owner, step=2), "review_stale")

    def test_reworded_criterion_opens_authorization_gate(self):
        owner = self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"], criterion_text={"A1": "original requirement"})
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            new_criteria=["A1"],
            criterion_text={"A1": "reworded requirement"},
            decision="clarify acceptance",
        )
        code, state = self.call("show")
        self.assertEqual(state["accepted_digest"], digest)
        self.assertEqual(state["accepted_criteria"], ["A1"])
        self.assertEqual(state["accepted_criterion_text"], {"A1": "original requirement"})
        self.assertEqual(state["pending_proposal"]["replacement_digest"], new_digest)
        self.assertEqual(state["pending_proposal"]["prior_criterion_text"], {"A1": "original requirement"})
        self.assertEqual(state["pending_proposal"]["criterion_text"], {"A1": "reworded requirement"})
        self.assertEqual(self.error_code("start", owner=owner, step=2), "authorization_pending")

    def test_unknown_criterion_text_treated_as_unchanged(self):
        self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1"])
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            new_criteria=["A1"],
            criterion_text={"A1": "reworded requirement"},
            decision="clarify acceptance",
        )
        code, state = self.call("show")
        self.assertEqual(state["accepted_digest"], new_digest)
        self.assertEqual(state["pending_proposal"], None)
        self.assertEqual(state["accepted_criterion_text"], {})
        self.assertEqual(state["amendments"][-1]["criterion_text"], {"A1": "reworded requirement"})

    def test_criterion_text_carried_through_approved_amendment(self):
        self.claim_owner()
        digest = "d" * 64
        self.mutate("review", digest=digest, result="clean")
        self.mutate("accept", digest=digest, criteria=["A1", "A2"], criterion_text={"A1": "first", "A2": "second"})
        new_digest = "e" * 64
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            affected_steps=[2],
            new_criteria=["A1", "A2", "A3"],
            criterion_text={"A1": "first", "A2": "second reworded", "A3": "third"},
            decision="add scope",
        )
        saved = self.call("show")[1]
        self.mutate(
            "amend",
            prior_digest=digest,
            replacement_digest=new_digest,
            authorization="approve",
        )
        code, state = self.call("show")
        self.assertEqual(state["accepted_digest"], new_digest)
        self.assertEqual(state["accepted_criteria"], ["A1", "A2", "A3"])
        self.assertEqual(state["accepted_criterion_text"], {"A1": "first", "A2": "second reworded", "A3": "third"})
        self.assertIsNone(state["pending_proposal"])
        amendment = state["amendments"][-1]
        self.assertEqual(amendment["prior_criteria"], ["A1", "A2"])
        self.assertEqual(amendment["prior_criterion_text"], {"A1": "first", "A2": "second"})
        self.assertEqual(amendment["new_criteria"], ["A1", "A2", "A3"])
        self.assertEqual(amendment["criterion_text"], {"A1": "first", "A2": "second reworded", "A3": "third"})
        self.assertEqual(saved["revision"] + 1, state["revision"])


class RunStateCallerSequenceTests(RunStateTestBase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo with spaces"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src/app.py").write_text(APP_PY, encoding="utf-8")
        (self.repo / "plans").mkdir()
        self.plan = self.repo / "plans/greeting.md"
        self.plan.write_text(fixture_plan([1, 2]), encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "master", str(self.repo)], check=True, timeout=30)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True, timeout=30)
        self.head = self.commit_fixture("initial fixture")
        self.state_root = Path(self.tmp.name) / "state with spaces"
        code, initialized = self.call("init", self.identity_request(step_ids=[1, 2]))
        self.assertEqual(code, 0, initialized)
        self.revision = initialized["revision"]

    def commit_fixture(self, message):
        subprocess.run([
            "git", "-C", str(self.repo), "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "--allow-empty", "-qm", message,
        ], check=True, timeout=30)
        return subprocess.run([
            "git", "-C", str(self.repo), "rev-parse", "HEAD",
        ], check=True, capture_output=True, text=True, timeout=30).stdout.strip()

    def call(self, command, request=None, state_root=None):
        result = subprocess.run([
            sys.executable, str(SCRIPT), "--state-root", str(state_root or self.state_root), command,
        ], input=json.dumps(request if request is not None else self.identity_request()),
            capture_output=True, text=True, timeout=30)
        return result.returncode, json.loads(result.stdout or result.stderr)

    def current_evidence(self):
        command = [sys.executable, "-c", "import runpy; assert runpy.run_path('src/app.py')['greet']() == 'hello'"]
        result = subprocess.run(command, cwd=self.repo, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {
            "checks": [{
                "command": command,
                "argv": command,
                "cwd": str(self.repo),
                "exit_status": result.returncode,
                "log_path": "/tmp/current-evidence.log",
                "input_hashes": {"src/app.py": self.app_hash()},
                "kind": "auto",
            }],
            "changed_files": ["src/app.py"], "hashes": {"src/app.py": self.app_hash()},
        }

    def completed_fixture(self):
        owner = self.claim_owner()
        self.accept_plan()
        self.start_step(owner, 1)
        self.mutate("record", owner=owner, step=1, handback="verified greeting", **self.current_evidence())
        return owner

    def discover_stored_identity(self, message="same branch external commit"):
        target = self.identity_request()["identity"]
        target["head"] = self.commit_fixture(message)
        code, changed = self.call("show", {"identity": target})
        self.assertEqual(code, 1, changed)
        self.assertEqual(changed["error"]["code"], "head_changed")
        stored = {**target, "head": changed["error"]["prior_head"]}
        code, state = self.call("show", {"identity": stored})
        self.assertEqual(code, 0, state)
        self.assertEqual(state["revision"], self.revision)
        return target, stored, state

    def reconcile_target(self, target, stored, owner):
        code, reconciled = self.call("reconcile-head", {
            "identity": target, "prior_head": stored["head"],
            "owner": owner, "expected_revision": self.revision,
        })
        self.assertEqual(code, 0, reconciled)
        self.revision = reconciled["revision"]
        self.head = target["head"]
        code, state = self.call("show")
        self.assertEqual(code, 0, state)
        return state

    def test_released_caller_revalidates_current_checks_and_hashes_before_ready(self):
        owner = self.completed_fixture()
        self.mutate("release", owner=owner)
        target, stored, before = self.discover_stored_identity()
        self.assertEqual(before["ownership"]["state"], "released")
        owner = self.mutate("claim")["owner"]

        reconciled = self.reconcile_target(target, stored, owner)

        self.assertEqual(reconciled["pending_revalidation"], ["1"])
        self.assertEqual(reconciled["accepted_digest"], before["accepted_digest"])
        self.assertEqual(reconciled["checkpoints"], before["checkpoints"])
        self.assert_close_needs_revalidation(owner)
        cases = [
            ("failed", {"checks": [{"kind": "auto", "exit_status": 1}]}, "step_not_done"),
            ("manual", {"checks": [{"kind": "manual"}]}, "step_not_done"),
            ("unavailable", {"checks": [{"kind": "planned"}]}, "step_not_done"),
            ("incomplete", {"checks": []}, "step_not_done"),
            ("missing hashes", {"checks": [passing_check()]}, "missing_hashes"),
        ]
        for label, evidence, error in cases:
            with self.subTest(verification=label):
                self.assertEqual(self.error_code("recover", owner=owner, step=1, outcome="done",
                                                 evidence=evidence), error)
                self.assertEqual(self.call("show")[1], reconciled)
        self.mutate("recover", owner=owner, step=1, outcome="done", evidence=self.current_evidence())
        verified = self.call("show")[1]
        self.assertEqual(verified["pending_revalidation"], [])
        self.assertEqual(verified["steps"]["1"]["evidence"][0]["flags"], ["needs_revalidation"])
        self.assertEqual(verified["checkpoints"][0], before["checkpoints"][0])
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", **self.current_evidence())
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "ready")

    def test_active_caller_inspects_running_stale_and_immutable_refusals(self):
        owner = self.completed_fixture()
        self.start_step(owner, 2)
        target, stored, before = self.discover_stored_identity()
        request = {"identity": target, "prior_head": stored["head"],
                   "expected_revision": before["revision"], "owner": owner}
        code, refused = self.call("reconcile-head", request)
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "step_already_running")
        self.assertEqual(self.call("show")[1], before)
        self.assertEqual(self.error_code("takeover", previous_owner=owner, evidence={}),
                         "recovery_question_required")
        owner = self.mutate("takeover", previous_owner=owner,
                            evidence={"ended_session": "fixture session ended"})["owner"]
        self.mutate("recover", owner=owner, step=2, outcome="pending")
        inspected = self.call("show")[1]
        code, refused = self.call("reconcile-head", {**request, "owner": owner})
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "stale_state")
        cases = [
            ("worktree_root", str(self.repo / "other"), "identity_mismatch"),
            ("git_dir", str(self.repo / "other.git"), "identity_mismatch"),
            ("branch", "other-branch", "branch_changed"),
            ("plan_realpath", str(self.repo / "plans/other.md"), "state_missing"),
        ]
        for field, value, error in cases:
            with self.subTest(identity_field=field):
                code, refused = self.call("reconcile-head", {
                    **request, "owner": owner, "expected_revision": self.revision,
                    "identity": {**target, field: value},
                })
                self.assertEqual(code, 1, refused)
                self.assertEqual(refused["error"]["code"], error)
                self.assertEqual(self.call("show")[1], inspected)
        state = self.reconcile_target(target, stored, owner)
        self.assertEqual(state["ownership"]["owner"], owner)
        self.assertEqual(state["steps"]["2"]["status"], "pending")
        self.assertEqual(state["pending_revalidation"], ["1", "2"])
        self.mutate("recover", owner=owner, step=1, outcome="done", evidence=self.current_evidence())
        self.assertEqual(self.call("show")[1]["pending_revalidation"], ["2"])
        self.assert_close_needs_revalidation(owner)
        self.assertEqual(self.error_code("recover", owner=owner, step=2, outcome="done",
                                         evidence=self.current_evidence()), "step_not_running")
        self.start_step(owner, 2)
        self.mutate("record", owner=owner, step=2, handback="verified interrupted work",
                    **self.current_evidence())
        self.mutate("close", owner=owner, result="ready", **self.current_evidence())
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "ready")

    def test_amendment_preserves_obligations_until_reviewed_execution(self):
        owner = self.completed_fixture()
        target, stored, before = self.discover_stored_identity()
        reconciled = self.reconcile_target(target, stored, owner)
        self.assertEqual(reconciled["ownership"], before["ownership"])
        self.mutate("amend", owner=owner, prior_digest="d" * 64, replacement_digest="e" * 64,
                    affected_steps=[1], decision="clarify unfinished verification", step_ids=[1, 2])
        amended = self.call("show")[1]
        self.assertEqual(amended["pending_revalidation"], ["1"])
        self.assertEqual(amended["steps"]["1"]["status"], "pending")
        self.assertEqual(amended["steps"]["1"]["evidence"], reconciled["steps"]["1"]["evidence"])
        self.assertEqual(amended["checkpoints"], reconciled["checkpoints"])
        self.assertEqual(self.error_code("start", owner=owner, step=1), "review_stale")
        self.assert_close_needs_revalidation(owner)
        self.mutate("review", digest="e" * 64, result="clean")
        self.start_step(owner, 1)
        self.mutate("record", owner=owner, step=1, handback="verified amended step", **self.current_evidence())
        self.assertEqual(self.call("show")[1]["pending_revalidation"], [])
        self.assertEqual(self.call("show")[1]["steps"]["2"]["status"], "pending")

    def test_legacy_flagged_completion_retains_history_after_cli_recovery(self):
        owner = self.completed_fixture()
        legacy = self.call("show")[1]
        del legacy["pending_revalidation"]
        del legacy["identity_history"]
        legacy["steps"]["1"]["evidence"][-1]["flags"] = ["needs_revalidation"]
        state_path = next(self.state_root.glob("*.json"))
        state_path.write_text(json.dumps(legacy), encoding="utf-8")

        code, loaded = self.call("show")

        self.assertEqual(code, 0, loaded)
        self.assertEqual(loaded["pending_revalidation"], ["1"])
        self.assertEqual(loaded["steps"]["1"]["status"], "needs-manual-check")
        self.assert_close_needs_revalidation(owner)
        self.mutate("recover", owner=owner, step=1, outcome="done", evidence=self.current_evidence())
        recovered = self.call("show")[1]
        self.assertEqual(recovered["pending_revalidation"], [])
        self.assertEqual(recovered["steps"]["1"]["evidence"][0], legacy["steps"]["1"]["evidence"][0])
        self.complete_pending_steps(owner)
        self.mutate("close", owner=owner, result="ready", **self.current_evidence())
        self.assertEqual(self.call("show")[1]["close_out"]["status"], "ready")

    def test_second_reconciliation_keeps_amended_pending_step_behind_review(self):
        owner = self.completed_fixture()
        target, stored, _ = self.discover_stored_identity()
        self.reconcile_target(target, stored, owner)
        self.mutate("amend", owner=owner, prior_digest="d" * 64,
                    replacement_digest="e" * 64, affected_steps=[1],
                    decision="revise unfinished verification", step_ids=[1, 2])
        amended = self.call("show")[1]

        target, stored, _ = self.discover_stored_identity()
        reconciled = self.reconcile_target(target, stored, owner)

        self.assertEqual(reconciled["steps"]["1"]["status"], "pending")
        self.assertEqual(reconciled["pending_revalidation"], ["1"])
        self.assertEqual(reconciled["steps"]["1"]["evidence"], amended["steps"]["1"]["evidence"])
        self.assertEqual(reconciled["checkpoints"], amended["checkpoints"])
        self.assertEqual(reconciled["accepted_digest"], "e" * 64)
        self.assertEqual(reconciled["ownership"], amended["ownership"])
        self.assertEqual(self.error_code("recover", owner=owner, step=1, outcome="done",
                                         evidence=self.current_evidence()), "step_not_running")
        self.assertEqual(self.error_code("start", owner=owner, step=1), "review_stale")
        self.assertEqual(self.error_code("close", owner=owner, result="ready",
                                         **self.current_evidence()), "needs_revalidation")

        legacy = self.call("show")[1]
        del legacy["pending_revalidation"]
        state_path = next(self.state_root.glob("*.json"))
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(self.call("show")[1]["steps"]["1"]["status"], "pending")
        self.assertEqual(self.call("show")[1]["pending_revalidation"], ["1"])
        self.assertEqual(self.error_code("recover", owner=owner, step=1, outcome="done",
                                         evidence=self.current_evidence()), "step_not_running")

        self.mutate("review", digest="e" * 64, result="clean")
        self.start_step(owner, 1)
        self.mutate("record", owner=owner, step=1, handback="verified revised behavior",
                    **self.current_evidence())
        verified = self.call("show")[1]
        self.assertEqual(verified["pending_revalidation"], [])
        self.assertEqual(verified["steps"]["1"]["status"], "done")
        self.assertEqual(verified["steps"]["1"]["evidence"][:-1], amended["steps"]["1"]["evidence"])

    def test_reconciled_committed_deletion_of_watched_path_is_not_drift(self):
        owner = self.completed_fixture()
        watched = self.call("show")[1]["checkpoints"][-1]["hashes"]
        subprocess.run(["git", "-C", str(self.repo), "rm", "-q", "src/app.py"], check=True, timeout=30)
        target, stored, _ = self.discover_stored_identity("delete watched path")
        self.reconcile_target(target, stored, owner)
        self.mutate("start", owner=owner, step=2, watched_files=watched)

    def test_uncommitted_deletion_of_watched_path_is_drift(self):
        owner = self.completed_fixture()
        watched = self.call("show")[1]["checkpoints"][-1]["hashes"]
        subprocess.run(["git", "-C", str(self.repo), "rm", "-q", "src/app.py"], check=True, timeout=30)
        self.assertEqual(
            self.error_code("start", owner=owner, step=2, watched_files=watched),
            "unexplained_drift",
        )


class RunStateDescribeTests(unittest.TestCase):
    def test_command_table_covers_exactly_the_mutation_handlers_plus_reads(self):
        self.assertEqual(
            set(run_state.COMMANDS),
            set(run_state.MUTATION_HANDLERS) | {"init", "show"},
        )
        self.assertEqual(set(run_state.KNOWN_COMMANDS), set(run_state.COMMANDS))
        for spec in run_state.COMMANDS.values():
            self.assertEqual(set(spec), {"required", "optional"})
            self.assertFalse(set(spec["required"]) & set(spec["optional"]))

    def test_every_required_field_is_enforced(self):
        identity, state_root = self.identity_and_state_root()
        for command, spec in run_state.COMMANDS.items():
            if command in ("init", "show"):
                continue
            for field in spec["required"]:
                request = {"identity": identity, "expected_revision": 0}
                request.update({name: "x" for name in spec["required"] if name != field})
                code, payload = run_state.run(command, request, state_root)
                self.assertEqual(code, 2, (command, field, payload))
                self.assertEqual(payload["error"]["code"], "usage", (command, field))
                self.assertIn(field, payload["error"]["message"])

    def describe_output(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "describe"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"commands"})
        return payload["commands"]

    def identity_and_state_root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "repo"
        repo.mkdir()
        plan_file = repo / "plan.md"
        plan_file.write_text("# Plan\n", encoding="utf-8")
        identity = {
            "worktree_root": str(repo),
            "git_dir": str(repo / ".git"),
            "branch": "master",
            "head": "",
            "plan_realpath": str(plan_file.resolve()),
        }
        return identity, Path(tmp.name) / "planning-state"

    def test_describe_prints_the_command_table(self):
        commands = self.describe_output()
        for command, spec in run_state.COMMANDS.items():
            entry = commands[command]
            self.assertEqual(set(entry), {"required", "optional", "shapes"}, command)
            self.assertEqual(entry["required"], spec["required"], command)
            self.assertEqual(entry["optional"], spec["optional"], command)

    def test_describe_documents_takeover_evidence_shapes(self):
        self.assertEqual(
            self.describe_output()["takeover"]["shapes"],
            {
                "ended_session": {"types": ["str", "bool"]},
                "user_statement": {"types": ["str"]},
                "reason": {"types": ["str"]},
            },
        )

    def test_describe_documents_recover_evidence_shapes(self):
        self.assertEqual(
            self.describe_output()["recover"]["shapes"],
            {
                "checks": {"types": ["check list"]},
                "changed_files": {"types": ["str list"]},
                "hashes": {"types": ["str map"]},
            },
        )

    def test_describe_documents_close_archive_shape(self):
        self.assertEqual(
            self.describe_output()["close"]["shapes"],
            {
                "checks.archive": {
                    "types": ["object"],
                    "fields": {
                        "original_path": ["str"],
                        "archive_path": ["str"],
                        "plan_digest": ["str"],
                        "sha256": ["str"],
                    },
                    "documented_only": True,
                }
            },
        )

    def test_describe_shapes_cover_exactly_the_shared_table(self):
        for command, spec in self.describe_output().items():
            self.assertEqual(set(spec["shapes"]), set(run_state.NESTED_FIELD_SHAPES.get(command, {})), command)

    def test_evidence_shapes_are_validated_for_takeover_and_recover(self):
        identity, state_root = self.identity_and_state_root()
        commands = (
            ("takeover", {"previous_owner": "owner"}),
            ("recover", {"owner": "owner", "step": 1, "outcome": "done"}),
        )
        invalid = {
            "ended_session": 1,
            "user_statement": 1,
            "reason": 1,
            "checks": [1],
            "changed_files": [1],
            "hashes": [],
        }
        for command, fields in commands:
            for field, value in invalid.items():
                with self.subTest(command=command, field=field):
                    request = {"identity": identity, "expected_revision": 0, "evidence": {field: value}, **fields}
                    code, payload = run_state.run(command, request, state_root)
                    self.assertEqual(code, 2, payload)
                    self.assertEqual(payload["error"]["code"], "usage")
                    self.assertIn(f"evidence.{field}", payload["error"]["message"])

    def test_close_archive_check_is_not_rejected_as_usage(self):
        identity, state_root = self.identity_and_state_root()
        request = {
            "identity": identity,
            "expected_revision": 0,
            "owner": "owner",
            "checks": [
                {
                    "archive": {
                        "original_path": "plans/greeting.md",
                        "archive_path": "plans/archive/2026-11-06-greeting.md",
                        "plan_digest": "d" * 64,
                        "sha256": "e" * 64,
                    }
                }
            ],
        }
        code, payload = run_state.run("close", request, state_root)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["error"]["code"], "state_missing")

    def test_describe_rejects_extra_arguments(self):
        for argv in (["describe", "extra"], ["--state-root", "/tmp/state", "describe"]):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *argv],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 2, argv)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "usage", argv)


class RunStateCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "plans").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.plan = self.repo / "plans" / "greeting.md"
        self.plan.write_text(fixture_plan([1]), encoding="utf-8")
        self.state_root = Path(self.tmp.name) / "codex-home" / "planning-state"
        self.head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def identity_request(self, **overrides):
        request = {
            "identity": {
                "worktree_root": str(self.repo),
                "git_dir": str(self.repo / ".git"),
                "branch": "master",
                "head": self.head,
                "plan_realpath": str(self.plan.resolve()),
            }
        }
        request.update(overrides)
        return request

    def run_cli(self, command, request):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--state-root", str(self.state_root), command],
            input=json.dumps(request),
            capture_output=True,
            text=True,
        )

    def test_malformed_stdin_json_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--state-root", str(self.state_root), "show"],
            input="{ not json",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "usage")

    def test_interactive_stdin_exits_two_without_reading(self):
        class InteractiveInput:
            def isatty(self):
                return True

            def read(self, *args):
                raise AssertionError("interactive stdin must not be read")

        output = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(output.close)
        with patch.object(run_state.sys, "stdin", InteractiveInput()), patch.object(
            run_state.sys, "stdout", output
        ), patch.object(run_state.sys, "stderr", output):
            result = run_state.main(["--state-root", str(self.state_root), "show"])
        self.assertEqual(result, 2)
        output.seek(0)
        payload = json.loads(output.read())
        self.assertEqual(payload["error"]["code"], "usage")
        self.assertIn("interactive input is not supported", payload["error"]["message"])

    def test_empty_noninteractive_stdin_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--state-root", str(self.state_root), "show"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "usage")

    def test_unknown_command_exits_two(self):
        result = self.run_cli("frobnicate", self.identity_request())
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "usage")

    def test_two_competing_writers_same_revision_exactly_one_wins(self):
        init = self.run_cli("init", self.identity_request(step_ids=[1]))
        self.assertEqual(init.returncode, 0)

        requests = [
            self.identity_request(expected_revision=0, digest="d" * 64, result="clean") for _ in range(2)
        ]
        processes = [
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "--state-root", str(self.state_root), "review"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in requests
        ]
        results = [
            process.communicate(json.dumps(request))
            for process, request in zip(processes, requests)
        ]
        codes = []
        for stdout, stderr in results:
            payload = json.loads(stdout or stderr)
            codes.append(payload)
        exit_codes = [0 if "revision" in payload else 1 for payload in codes]
        self.assertEqual(sorted(exit_codes), [0, 1])
        loser = next(payload for payload in codes if "error" in payload)
        self.assertEqual(loser["error"]["code"], "stale_state")

    def test_claim_outputs_owner_token_for_continuation(self):
        init = self.run_cli("init", self.identity_request(step_ids=[1]))
        self.assertEqual(init.returncode, 0)
        claimed = self.run_cli("claim", self.identity_request(expected_revision=0))
        self.assertEqual(claimed.returncode, 0)
        owner = json.loads(claimed.stdout)["owner"]
        shown = self.run_cli("show", self.identity_request())
        self.assertEqual(json.loads(shown.stdout)["ownership"]["owner"], owner)

    def test_head_change_cli_exposes_prior_head_for_reconciliation(self):
        init = self.run_cli("init", self.identity_request(step_ids=[1]))
        self.assertEqual(init.returncode, 0)
        current = self.identity_request()
        current["identity"]["head"] = "a" * 40
        changed = self.run_cli("show", current)
        self.assertEqual(changed.returncode, 1)
        self.assertEqual(changed.stdout, "")
        error = json.loads(changed.stderr)["error"]
        self.assertEqual(error["code"], "head_changed")
        self.assertEqual(error["prior_head"], self.head)
        prior_identity = {**current["identity"], "head": error["prior_head"]}
        prior = self.run_cli("show", {"identity": prior_identity})
        self.assertEqual(prior.returncode, 0, prior.stderr)
        claimed = self.run_cli("claim", {
            "identity": prior_identity,
            "expected_revision": json.loads(prior.stdout)["revision"],
        })
        self.assertEqual(claimed.returncode, 0)
        request = {
            **current, "expected_revision": json.loads(claimed.stdout)["revision"],
            "prior_head": error["prior_head"], "owner": json.loads(claimed.stdout)["owner"],
        }

        reconciled = self.run_cli("reconcile-head", request)

        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        self.assertEqual(json.loads(reconciled.stdout), {"revision": 2})
        shown = self.run_cli("show", {"identity": request["identity"]})
        self.assertEqual(shown.returncode, 0, shown.stderr)
        state = json.loads(shown.stdout)
        self.assertEqual(state["identity"]["head"], "a" * 40)
        self.assertEqual(state["close_out"]["status"], "blocked")
        self.assertEqual(state["identity_history"][0]["prior_head"], self.head)


if __name__ == "__main__":
    unittest.main()
