#!/usr/bin/env python3
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path


def _load_plan_module():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("plan_state_plan_module", here / "plan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan_module = _load_plan_module()


COMMANDS = {
    "init": {"required": [], "optional": ["step_ids", "step_dependencies"]},
    "show": {"required": [], "optional": []},
    "review": {"required": ["digest", "result"], "optional": []},
    "accept": {"required": ["digest", "criteria"], "optional": ["criterion_text"]},
    "claim": {"required": [], "optional": []},
    "release": {"required": ["owner"], "optional": ["reason", "watched_files"]},
    "takeover": {"required": ["previous_owner"], "optional": ["evidence"]},
    "recover": {"required": ["owner", "step", "outcome"], "optional": ["evidence"]},
    "start": {"required": ["owner", "step"], "optional": ["watched_files"]},
    "record": {"required": ["owner", "step", "handback"], "optional": ["changed_files", "checks", "hashes"]},
    "block": {"required": ["owner", "step"], "optional": ["reason"]},
    "amend": {
        "required": ["owner", "prior_digest", "replacement_digest", "affected_steps"],
        "optional": [
            "authorization",
            "criterion_text",
            "decision",
            "invalidated",
            "new_criteria",
            "step_dependencies",
            "step_ids",
        ],
    },
    "reconcile-head": {"required": ["owner", "prior_head"], "optional": []},
    "close": {"required": ["owner"], "optional": ["checks", "hashes", "reason", "result"]},
}

MAX_HANDBACK_BYTES = 4096

IDENTITY_FIELDS = ("worktree_root", "git_dir", "branch", "head", "plan_realpath")

_STATE_REQUIRED_KEYS = {
    "version",
    "revision",
    "identity",
    "steps",
    "review",
    "accepted_digest",
    "accepted_criteria",
    "accepted_criterion_text",
    "checkpoints",
    "close_out",
    "ownership",
    "amendments",
    "pending_proposal",
}


def _validate_identity_shape(identity):
    if not isinstance(identity, dict):
        return _error("usage", "identity must be a JSON object")
    for field in IDENTITY_FIELDS:
        value = identity.get(field)
        if not isinstance(value, str):
            return _error("usage", f"identity.{field} is required and must be a string")
    return None


def state_key(identity):
    key_material = identity["worktree_root"] + "\0" + identity["plan_realpath"]
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


def _state_paths(state_root, key):
    state_root = Path(state_root)
    return state_root / f"{key}.json", state_root / f"{key}.lock"


def _initial_state(identity, step_ids, step_dependencies=None):
    steps = {}
    dependencies = step_dependencies or {}
    for step_id in step_ids:
        steps[str(step_id)] = {
            "status": "pending",
            "evidence": [],
            "depends_on": [str(d) for d in dependencies.get(str(step_id), [])],
        }
    return {
        "version": 1,
        "revision": 0,
        "identity": identity,
        "identity_history": [],
        "steps": steps,
        "review": {"digest": None, "result": None, "receipts": []},
        "accepted_digest": None,
        "accepted_criteria": [],
        "accepted_criterion_text": {},
        "checkpoints": [],
        "close_out": {"status": "pending", "final_hashes": {}},
        "ownership": {"state": "released", "owner": None, "history": []},
        "amendments": [],
        "pending_proposal": None,
        "pending_revalidation": [],
    }


def _load_state(state_path):
    if not state_path.exists():
        return None, {"error": {"code": "state_missing", "message": "no state exists for this identity"}}
    try:
        text = state_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, {"error": {"code": "corrupt_state", "message": f"cannot read state file: {exc}"}}
    try:
        state = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, {"error": {"code": "corrupt_state", "message": f"state file is not valid JSON: {exc}"}}
    shape_error = _validate_state_shape(state)
    if shape_error:
        return None, shape_error
    if "pending_revalidation" not in state:
        state["pending_revalidation"] = [
            step_id for step_id, step in state["steps"].items()
            if any("needs_revalidation" in evidence.get("flags", []) for evidence in step["evidence"])
            and (step["status"] != "done" or "needs_revalidation" in step["evidence"][-1].get("flags", []))
        ]
        for step_id in state["pending_revalidation"]:
            if state["steps"][step_id]["status"] == "done":
                state["steps"][step_id]["status"] = "needs-manual-check"
    if state["close_out"]["status"] == "ready":
        error = _readiness_error(state, state["close_out"].get("checks", []), state["close_out"]["final_hashes"])
        if error:
            state["close_out"]["status"] = "blocked"
            state["close_out"]["final_hashes"] = {}
            state["close_out"]["reason"] = f"readiness invalidated: {error['error']['code']}: {error['error']['message']}"
    return state, None


def _validate_identity(state, identity):
    stored = state["identity"]
    for field in ("worktree_root", "git_dir", "plan_realpath"):
        if stored.get(field) != identity.get(field):
            return {"error": {"code": "identity_mismatch", "message": f"{field} does not match stored identity"}}
    if stored.get("branch") != identity.get("branch"):
        return {"error": {"code": "branch_changed", "message": "branch changed since state was recorded"}}
    if stored.get("head") != identity.get("head"):
        return {"error": {
            "code": "head_changed",
            "message": "HEAD changed since state was recorded",
            "prior_head": stored.get("head"),
        }}
    return None


def _validate_state_shape(state):
    if not isinstance(state, dict):
        return _error("corrupt_state", "state file is not a JSON object")
    missing = _STATE_REQUIRED_KEYS - set(state.keys())
    if missing:
        return _error("corrupt_state", f"state file is missing required field: {sorted(missing)[0]}")
    if type(state["version"]) is not int or state["version"] != 1:
        return _error("corrupt_state", "unsupported state version")
    if not _is_nonnegative_integer(state["revision"]) or _validate_identity_shape(state["identity"]):
        return _error("corrupt_state", "invalid state revision or identity")
    if not isinstance(state["steps"], dict):
        return _error("corrupt_state", "steps must be an object")
    for step in state["steps"].values():
        if (
            not isinstance(step, dict)
            or step.get("status") not in ("pending", "running", "done", "blocked", "needs-manual-check")
            or not _is_object_list(step.get("evidence"))
            or not _is_string_list(step.get("depends_on"))
        ):
            return _error("corrupt_state", "invalid step state")
        for evidence in step["evidence"]:
            if (
                evidence.get("status") not in ("done", "pending", "blocked", "needs-manual-check", "interrupted-partial")
                or not _is_string_list(evidence.get("flags"))
                or not _is_processed_check_list(evidence.get("checks"))
                or ("handback" in evidence and not isinstance(evidence["handback"], str))
                or not _is_nullable_string(evidence.get("reason"))
            ):
                return _error("corrupt_state", "invalid step evidence")
    review = state["review"]
    ownership = state["ownership"]
    close_out = state["close_out"]
    if (
        not isinstance(review, dict)
        or not {"digest", "result"}.issubset(review)
        or not _is_nullable_string(review.get("digest"))
        or not _is_nullable_string(review.get("result"))
        or not _is_object_list(review.get("receipts"))
        or not isinstance(ownership, dict)
        or ownership.get("state") not in ("active", "released")
        or not _is_nullable_string(ownership.get("owner"))
        or (ownership.get("state") == "active" and not ownership.get("owner"))
        or not _is_object_list(ownership.get("history"))
        or not isinstance(close_out, dict)
        or close_out.get("status") not in ("pending", "ready", "blocked")
        or not _is_string_map(close_out.get("final_hashes"))
        or not _is_processed_check_list(close_out.get("checks", []))
        or not _is_nullable_string(close_out.get("reason"))
    ):
        return _error("corrupt_state", "invalid review, ownership or close-out state")
    for receipt in review["receipts"]:
        if not all(isinstance(receipt.get(field), str) for field in ("digest", "result")):
            return _error("corrupt_state", "invalid review receipt")
    latest_review = review["receipts"][-1] if review["receipts"] else {"digest": None, "result": None}
    if any(review[field] != latest_review[field] for field in ("digest", "result")):
        return _error("corrupt_state", "review summary does not match its latest receipt")
    for entry in ownership["history"]:
        if (
            entry.get("kind") not in ("claim", "release", "takeover")
            or (entry["kind"] == "release" and ("reason" not in entry or not _is_nullable_string(entry["reason"])))
        ):
            return _error("corrupt_state", "invalid ownership transition")
    if (
        not _is_nullable_string(state["accepted_digest"])
        or not _is_string_list(state["accepted_criteria"])
        or not _is_string_map(state["accepted_criterion_text"])
        or not _is_object_list(state["checkpoints"])
        or not _is_object_list(state["amendments"])
        or not _is_object_list(state.get("identity_history", []))
        or not _is_string_list(state.get("pending_revalidation", []))
    ):
        return _error("corrupt_state", "invalid acceptance or evidence history")
    for checkpoint in state["checkpoints"]:
        if not _is_string_list(checkpoint.get("changed_files")) or not _is_string_map(checkpoint.get("hashes")):
            return _error("corrupt_state", "invalid checkpoint")
    for entry in state.get("identity_history", []):
        if (
            entry.get("kind") != "reconcile-head"
            or not all(isinstance(entry.get(field), str) for field in ("prior_head", "head", "owner"))
            or not _is_nonnegative_integer(entry.get("revision"))
        ):
            return _error("corrupt_state", "invalid identity transition")
    for amendment in state["amendments"]:
        if not _is_amendment(amendment):
            return _error("corrupt_state", "invalid amendment history")
    proposal = state["pending_proposal"]
    if proposal is not None:
        if (
            not _is_amendment(proposal)
            or "step_ids" not in proposal
            or (proposal["step_ids"] is not None and not _is_step_id_list(proposal["step_ids"]))
            or not isinstance(proposal.get("step_dependencies"), dict)
            or not all(_is_step_id_list(deps) for deps in proposal["step_dependencies"].values())
        ):
            return _error("corrupt_state", "invalid pending proposal")
    return None


@contextmanager
def _locked(lock_path, exclusive):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_temp_file(tmp, payload):
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_state(state_path, state):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2)
    tmp = state_path.parent / f"{state_path.name}.tmp.{secrets.token_hex(8)}"
    try:
        _write_temp_file(tmp, payload)
        os.replace(tmp, state_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(state_path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _generate_owner():
    return secrets.token_urlsafe(16)


def _file_hash(path):
    if os.path.islink(path):
        return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_digest(path):
    try:
        parsed = plan_module.parse_plan(str(path))
        return plan_module.plan_digest(parsed)
    except (OSError, ValueError, KeyError):
        return None


def _absent_from_head(worktree_root, relative_path):
    try:
        probe = subprocess.run(
            ["git", "-C", str(worktree_root), "rev-parse", "--verify", "--quiet", f"HEAD:{relative_path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return probe.returncode == 1


def _has_drift(worktree_root, watched_files, plan_realpath):
    root = Path(worktree_root)
    plan_real = os.path.realpath(plan_realpath)
    for relative_path, expected_hash in watched_files.items():
        full = root / relative_path
        if not full.exists():
            if _absent_from_head(worktree_root, relative_path):
                continue
            return True
        if os.path.realpath(full) == plan_real:
            current_digest = _plan_digest(full)
            if current_digest is None or current_digest != expected_hash:
                return True
            continue
        if _file_hash(full) != expected_hash:
            return True
    return False


def _watched_drift(state, watched_files):
    try:
        drifted = _has_drift(
            state["identity"]["worktree_root"], watched_files,
            state["identity"]["plan_realpath"])
    except OSError as error:
        return None, _error("usage", f"cannot hash watched path: {error}")
    return drifted, None


def _mark_drift(state):
    state["close_out"]["status"] = "blocked"
    state["close_out"]["final_hashes"] = {}
    for step_id, step in state["steps"].items():
        if step["evidence"]:
            if step["status"] != "pending":
                step["status"] = "needs-manual-check"
            if step_id not in state["pending_revalidation"]:
                state["pending_revalidation"].append(step_id)
            flags = step["evidence"][-1].setdefault("flags", [])
            if "needs_revalidation" not in flags:
                flags.append("needs_revalidation")


CHECK_EVIDENCE_FIELDS = ("argv", "cwd", "exit_status", "log_path", "input_hashes")


def _process_checks(checks):
    processed = []
    for check in checks:
        kind = check.get("kind", "auto")
        exit_status = check.get("exit_status")
        if kind == "auto":
            status = "passed" if exit_status == 0 and not check.get("timed_out", False) else "failed"
        elif kind == "manual":
            status = "needs-manual-check"
        elif kind == "planned":
            status = "gap"
        elif kind == "exempt":
            status = "passed"
        else:
            status = "failed"
        processed.append({**check, "status": status})
    return processed


def _validate_check_evidence(checks):
    for check in checks:
        if check.get("status") != "passed" or check.get("kind", "auto") != "auto":
            continue
        for field in CHECK_EVIDENCE_FIELDS:
            if field not in check:
                return _error("incomplete_check_evidence", f"passed check is missing reuse evidence: {field}")
        if not isinstance(check["argv"], list) or not check["argv"]:
            return _error("incomplete_check_evidence", "passed check argv must be a non-empty array")
        if not isinstance(check["cwd"], str):
            return _error("incomplete_check_evidence", "passed check cwd must be a string")
        if not isinstance(check["exit_status"], int) or isinstance(check["exit_status"], bool):
            return _error("incomplete_check_evidence", "passed check exit_status must be an integer")
        if not isinstance(check["log_path"], str):
            return _error("incomplete_check_evidence", "passed check log_path must be a string")
        if not isinstance(check["input_hashes"], dict):
            return _error("incomplete_check_evidence", "passed check input_hashes must be an object")
    return None


def _step_status_from_checks(checks):
    if not checks:
        return "blocked"
    if any(check["status"] == "failed" for check in checks):
        return "blocked"
    if any(check["status"] == "needs-manual-check" for check in checks):
        return "needs-manual-check"
    if any(check["status"] == "gap" for check in checks):
        return "needs-manual-check"
    return "done"


def _error(code, message):
    return {"error": {"code": code, "message": message}}


def _find_conflicting_identity(state_root, identity, current_key):
    state_root = Path(state_root)
    if not state_root.exists():
        return None
    plan_realpath = identity.get("plan_realpath")
    for path in state_root.iterdir():
        if not path.is_file() or path.suffix != ".json" or ".tmp." in path.name:
            continue
        if path.stem == current_key:
            continue
        try:
            other = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(other, dict) or not isinstance(other.get("identity"), dict):
            continue
        other_identity = other["identity"]
        if other_identity.get("plan_realpath") != plan_realpath:
            continue
        if (
            other_identity.get("worktree_root") != identity.get("worktree_root")
            or other_identity.get("git_dir") != identity.get("git_dir")
        ):
            return other_identity
    return None


def _validate_step_dependencies(step_ids, step_dependencies):
    id_set = {str(step_id) for step_id in step_ids}
    normalized = _normalize_dependencies(step_dependencies)
    for step_id, deps in normalized.items():
        if step_id not in id_set:
            continue
        for dep in deps:
            if dep not in id_set:
                return _error("unknown_dependency", f"step {step_id} depends on unknown step {dep}")
            if dep == step_id:
                return _error("self_dependency", f"step {step_id} depends on itself")
    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            return _error("cyclic_dependency", f"step {node} is part of a dependency cycle")
        if node in visited or node not in id_set:
            return None
        visiting.add(node)
        for dep in normalized.get(node, []):
            if dep not in id_set:
                continue
            error = visit(dep)
            if error:
                return error
        visiting.remove(node)
        visited.add(node)
        return None

    for step_id in id_set:
        error = visit(step_id)
        if error:
            return error
    return None


def cmd_init(request, state_path, state_root):
    identity = request["identity"]
    if _find_conflicting_identity(state_root, identity, state_key(identity)):
        return 1, _error("identity_mismatch", "identity conflicts with existing state for a different plan or worktree"), False
    if state_path.exists():
        return 1, _error("state_exists", "state already exists for this identity"), False
    try:
        parsed = plan_module.parse_plan(identity["plan_realpath"])
    except (OSError, ValueError) as exc:
        return 1, _error("invalid_plan", f"cannot inspect plan for initialization: {exc}"), False
    if any(step["done"] for step in parsed["steps"]):
        return 1, _error(
            "completion_state_missing",
            "checked plan steps require existing durable state; restore state before continuing",
        ), False
    canonical_ids = [str(step["id"]) for step in parsed["steps"]]
    canonical_dependencies = {str(step["id"]): [str(dep) for dep in step["depends_on"]] for step in parsed["steps"]}
    if not canonical_ids or len(set(canonical_ids)) != len(canonical_ids):
        return 1, _error("invalid_plan", "initialization requires nonempty, unique plan step IDs"), False
    error = _validate_step_dependencies(canonical_ids, canonical_dependencies)
    if error:
        return 1, error, False
    step_ids = [str(step_id) for step_id in request.get("step_ids", canonical_ids)]
    dependencies = _normalize_dependencies(request.get("step_dependencies", canonical_dependencies))
    error = _validate_step_dependencies(step_ids, dependencies)
    if error:
        return 1, error, False
    if (
        len(step_ids) != len(set(step_ids))
        or set(step_ids) != set(canonical_ids)
        or set(dependencies) != set(canonical_ids)
        or any(set(dependencies.get(step_id, [])) != set(canonical_dependencies[step_id]) for step_id in canonical_ids)
        or any(len(deps) != len(set(deps)) for deps in dependencies.values())
    ):
        return 1, _error("step_graph_mismatch", "supplied step IDs and dependencies must match the complete parsed plan"), False
    state = _initial_state(identity, canonical_ids, canonical_dependencies)
    _write_state(state_path, state)
    return 0, {"revision": state["revision"]}, False


def cmd_review(state, request):
    state["review"]["digest"] = request["digest"]
    state["review"]["result"] = request["result"]
    state["review"]["receipts"].append({"digest": request["digest"], "result": request["result"]})
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def cmd_accept(state, request):
    state["accepted_digest"] = request["digest"]
    state["accepted_criteria"] = request["criteria"]
    state["accepted_criterion_text"] = request.get("criterion_text", {})
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def cmd_claim(state, request):
    if state["ownership"]["state"] == "active":
        return state, _error("owner_active", "an owner is already active"), 1, False
    owner = _generate_owner()
    state["ownership"]["state"] = "active"
    state["ownership"]["owner"] = owner
    state["ownership"]["history"].append({"kind": "claim"})
    state["revision"] += 1
    return state, {"revision": state["revision"], "owner": owner}, 0, True


def cmd_release(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    watched_files = request.get("watched_files")
    if watched_files:
        drifted, hash_error = _watched_drift(state, watched_files)
        if hash_error is not None:
            return state, hash_error, 2, False
        if drifted:
            _mark_drift(state)
            state["revision"] += 1
            return state, _error("unexplained_drift", "watched files changed outside the verified set"), 1, True
    state["ownership"]["state"] = "released"
    state["ownership"]["history"].append({"kind": "release", "reason": request.get("reason")})
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def cmd_takeover(state, request):
    if state["ownership"]["state"] != "active" or state["ownership"]["owner"] != request["previous_owner"]:
        return state, _error("owner_mismatch", "previous_owner does not match the active owner"), 1, False
    evidence = request.get("evidence", {})
    if not evidence.get("ended_session") and not evidence.get("user_statement"):
        return state, _error("recovery_question_required", "ended-run evidence is required to take over"), 1, False
    owner = _generate_owner()
    state["ownership"]["state"] = "active"
    state["ownership"]["owner"] = owner
    state["ownership"]["history"].append({"kind": "takeover"})
    state["revision"] += 1
    return state, {"revision": state["revision"], "owner": owner}, 0, True


RECOVER_OUTCOMES = {"done", "pending", "blocked"}


def cmd_recover(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    step, error = _require_step_field(request)
    if error:
        return state, error, 2, False
    step, error = _get_step(state, step)
    if error:
        return state, error, 1, False
    if state["steps"][step]["status"] not in ("running", "blocked", "needs-manual-check"):
        return state, _error("step_not_running", "step is not in a recoverable state"), 1, False
    outcome = request["outcome"]
    if outcome not in RECOVER_OUTCOMES:
        return state, _error("usage", f"outcome must be one of {sorted(RECOVER_OUTCOMES)}"), 2, False
    evidence = request.get("evidence", {})
    processed = _process_checks(evidence.get("checks", []))
    step_status = _step_status_from_checks(processed)
    if outcome == "done" and step_status != "done":
        return state, _error("step_not_done", "recover checks did not all pass"), 1, False
    if outcome == "done":
        evidence_error = _validate_check_evidence(processed)
        if evidence_error:
            return state, evidence_error, 1, False
    entry = {
        "checks": processed,
        "flags": [],
        "status": "interrupted-partial" if outcome == "pending" else outcome,
    }
    prior_evidence = state["steps"][step]["evidence"]
    if outcome != "done" and prior_evidence and "needs_revalidation" in prior_evidence[-1].get("flags", []):
        entry["flags"].append("needs_revalidation")
    if outcome == "blocked" and evidence.get("reason"):
        entry["reason"] = evidence["reason"]
    state["steps"][step]["evidence"].append(entry)
    state["steps"][step]["status"] = outcome
    if outcome == "done":
        if not evidence.get("hashes"):
            return state, _error("missing_hashes", "done outcome requires hashes for relevant files"), 1, False
        state["checkpoints"].append({
            "changed_files": evidence.get("changed_files", []),
            "hashes": evidence["hashes"],
        })
        if step in state["pending_revalidation"]:
            state["pending_revalidation"].remove(step)
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def _dependencies_done(state, step):
    for dep in state["steps"][step].get("depends_on", []):
        if state["steps"].get(dep, {}).get("status") != "done":
            return False
    return True


def cmd_start(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    error = _require_accepted_review(state)
    if error:
        return state, error, 1, False
    step, error = _require_step_field(request)
    if error:
        return state, error, 2, False
    step, error = _get_step(state, step)
    if error:
        return state, error, 1, False
    if state["steps"][step]["status"] != "pending":
        return state, _error("step_not_pending", "step is not pending"), 1, False
    if any(step_data["status"] == "running" for step_data in state["steps"].values()):
        return state, _error("step_already_running", "another step is already running"), 1, False
    if not _dependencies_done(state, step):
        return state, _error("dependency_not_done", f"step {step} has a dependency that is not done"), 1, False
    watched_files = request.get("watched_files")
    if watched_files:
        drifted, hash_error = _watched_drift(state, watched_files)
        if hash_error is not None:
            return state, hash_error, 2, False
        if drifted:
            _mark_drift(state)
            state["revision"] += 1
            return state, _error("unexplained_drift", "watched files changed outside the verified set"), 1, True
    state["steps"][step]["status"] = "running"
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def cmd_record(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    step, error = _require_step_field(request)
    if error:
        return state, error, 2, False
    step, error = _get_step(state, step)
    if error:
        return state, error, 1, False
    if state["steps"][step]["status"] != "running":
        return state, _error("step_not_running", "step is not running"), 1, False
    if not request.get("handback"):
        return state, _error("missing_handback", "handback is required"), 1, False
    if len(request["handback"].encode("utf-8")) > MAX_HANDBACK_BYTES:
        return state, _error(
            "oversized_handback",
            f"handback exceeds {MAX_HANDBACK_BYTES} bytes; evidence lives by reference in changed_files and checks, not inline",
        ), 1, False
    processed = _process_checks(request.get("checks", []))
    evidence_error = _validate_check_evidence(processed)
    if evidence_error:
        return state, evidence_error, 1, False
    step_status = _step_status_from_checks(processed)
    state["steps"][step]["evidence"].append({
        "checks": processed,
        "flags": [],
        "status": step_status,
        "handback": request["handback"],
    })
    state["steps"][step]["status"] = step_status
    if step_status == "done":
        if not request.get("hashes"):
            return state, _error("missing_hashes", "done outcome requires hashes for relevant files"), 1, False
        state["checkpoints"].append({
            "changed_files": request.get("changed_files", []),
            "hashes": request["hashes"],
        })
        if step in state["pending_revalidation"]:
            state["pending_revalidation"].remove(step)
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def cmd_block(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    step, error = _require_step_field(request)
    if error:
        return state, error, 2, False
    step, error = _get_step(state, step)
    if error:
        return state, error, 1, False
    state["steps"][step]["status"] = "blocked"
    state["steps"][step]["evidence"].append({
        "checks": [],
        "flags": [],
        "status": "blocked",
        "reason": request.get("reason"),
    })
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


def _get_step(state, step_id):
    step = str(step_id)
    if step not in state["steps"]:
        return None, _error("unknown_step", f"step {step} is not defined")
    return step, None


def _require_step_field(request):
    step = request.get("step")
    if step is None:
        return None, _error("usage", "request is missing required field: step")
    return step, None


def _require_active_owner(state, owner):
    if state["ownership"]["state"] != "active" or state["ownership"]["owner"] != owner:
        return _error("owner_mismatch", "owner token does not match")
    return None


def _require_accepted_review(state):
    if state["pending_proposal"] is not None:
        return _error("authorization_pending", "a proposed acceptance baseline change is awaiting user authorization")
    if not state["accepted_digest"]:
        return _error("not_authorized", "no accepted plan revision")
    if state["review"]["digest"] is None:
        return _error("review_missing", "no review receipt for the accepted revision")
    if state["review"]["digest"] != state["accepted_digest"]:
        return _error("review_stale", "review receipt does not match the accepted plan revision")
    if state["review"]["result"] != "clean":
        return _error("review_unclean", "review verdict was not clean")
    return None


def _readiness_error(state, checks, hashes):
    if state["pending_revalidation"]:
        return _error("needs_revalidation", "step evidence requires revalidation before close-out")
    error = _require_accepted_review(state)
    if error:
        return error
    if not state["steps"] or any(step["status"] != "done" for step in state["steps"].values()):
        return _error("steps_incomplete", "all plan steps must be done before close-out")
    if not checks or _process_checks(checks) != checks or any(check["status"] != "passed" for check in checks):
        return _error("checks_incomplete", "ready close-out requires complete passing checks")
    error = _validate_check_evidence(checks)
    if error:
        return error
    if not hashes or any(not path or not digest for path, digest in hashes.items()):
        return _error("missing_hashes", "ready close-out requires hashes for relevant files")
    return None


def _validate_affected_and_invalidated(state, affected_steps, invalidated, valid_ids=None):
    valid_steps = set(state["steps"].keys()) if valid_ids is None else set(valid_ids)
    for step_id in affected_steps:
        if str(step_id) not in valid_steps:
            return _error("unknown_step", f"step {step_id} is not defined")
    for item in invalidated:
        if str(item) == "close-out":
            continue
        if str(item) not in valid_steps:
            return _error("unknown_step", f"step {item} is not defined")
    return None


def _normalize_dependencies(step_dependencies):
    normalized = {}
    for step_id, deps in (step_dependencies or {}).items():
        normalized[str(step_id)] = [str(d) for d in deps]
    return normalized


def _apply_amend_effects(state, affected_steps, invalidated, step_ids, step_dependencies):
    if step_ids is not None:
        deps_map = _normalize_dependencies(step_dependencies)
        for step_id in step_ids:
            step = str(step_id)
            if step not in state["steps"]:
                state["steps"][step] = {"status": "pending", "evidence": [], "depends_on": deps_map.get(step, [])}
            elif step in deps_map:
                state["steps"][step]["depends_on"] = deps_map[step]
    to_reset = {str(step_id) for step_id in affected_steps}
    for item in invalidated:
        if str(item) in state["steps"]:
            to_reset.add(str(item))
    for step in to_reset:
        if step not in state["steps"]:
            return _error("unknown_step", f"step {step} is not defined")
        state["steps"][step]["status"] = "pending"
        if not any("needs_revalidation" in evidence.get("flags", []) for evidence in state["steps"][step]["evidence"]):
            state["steps"][step]["evidence"] = []
    if "close-out" in invalidated or state["close_out"]["status"] == "ready":
        state["close_out"]["status"] = "pending"
        state["close_out"]["final_hashes"] = {}
    return None


def _proposal_from_request(state, request):
    return {
        "prior_digest": request["prior_digest"],
        "prior_criteria": list(state["accepted_criteria"]),
        "prior_criterion_text": dict(state.get("accepted_criterion_text", {})),
        "replacement_digest": request["replacement_digest"],
        "new_criteria": request.get("new_criteria", list(state["accepted_criteria"])),
        "criterion_text": request.get("criterion_text", {}),
        "affected_steps": request.get("affected_steps", []),
        "decision": request.get("decision"),
        "invalidated": request.get("invalidated", []),
        "step_ids": request.get("step_ids"),
        "step_dependencies": request.get("step_dependencies", {}),
    }


def _baseline_changed(prior_criteria, prior_text, new_criteria, new_text):
    if set(prior_criteria) != set(new_criteria):
        return True
    prior_text = prior_text or {}
    new_text = new_text or {}
    for criterion in set(prior_criteria) & set(new_criteria):
        old = prior_text.get(criterion)
        updated = new_text.get(criterion)
        if old is not None and updated is not None and old != updated:
            return True
    return False


def _cmd_amend_authorize(state, request, replacement, pending):
    if pending is None:
        return state, _error("no_pending_proposal", "no amendment proposal is awaiting authorization"), 1, False
    if pending["replacement_digest"] != replacement:
        return state, _error("proposal_mismatch", "replacement_digest does not match the pending proposal"), 1, False
    if request["authorization"] == "reject":
        state["pending_proposal"] = None
        state["revision"] += 1
        return state, {"revision": state["revision"], "status": "rejected"}, 0, True
    state["accepted_digest"] = pending["replacement_digest"]
    state["accepted_criteria"] = list(pending.get("new_criteria", state["accepted_criteria"]))
    state["accepted_criterion_text"] = dict(pending.get("criterion_text", state.get("accepted_criterion_text", {})))
    error = _apply_amend_effects(
        state,
        pending["affected_steps"],
        pending.get("invalidated", []),
        pending.get("step_ids"),
        pending.get("step_dependencies", {}),
    )
    if error:
        return state, error, 1, False
    state["amendments"].append({
        "prior_digest": pending["prior_digest"],
        "prior_criteria": pending.get("prior_criteria", []),
        "prior_criterion_text": pending.get("prior_criterion_text", {}),
        "replacement_digest": pending["replacement_digest"],
        "new_criteria": pending.get("new_criteria", []),
        "criterion_text": pending.get("criterion_text", {}),
        "affected_steps": pending["affected_steps"],
        "decision": pending["decision"],
        "invalidated": pending.get("invalidated", []),
    })
    state["pending_proposal"] = None
    state["revision"] += 1
    return state, {"revision": state["revision"], "status": "approved"}, 0, True


def _cmd_amend_propose_or_apply(state, request, replacement):
    step_ids = request.get("step_ids")
    if step_ids is not None:
        existing = set(state["steps"].keys())
        provided = {str(step_id) for step_id in step_ids}
        if not existing.issubset(provided):
            return state, _error("step_removed", "an existing step ID is missing after the amendment"), 1, False
    valid_step_ids = [str(step_id) for step_id in step_ids] if step_ids is not None else list(state["steps"].keys())
    error = _validate_step_dependencies(valid_step_ids, request.get("step_dependencies", {}))
    if error:
        return state, error, 1, False
    error = _validate_affected_and_invalidated(
        state,
        request.get("affected_steps", []),
        request.get("invalidated", []),
        valid_step_ids,
    )
    if error:
        return state, error, 1, False
    proposal = _proposal_from_request(state, request)
    new_criteria = proposal["new_criteria"]
    baseline_changed = _baseline_changed(
        state["accepted_criteria"],
        state.get("accepted_criterion_text"),
        new_criteria,
        proposal.get("criterion_text"),
    )
    if baseline_changed:
        state["pending_proposal"] = proposal
        state["revision"] += 1
        return state, {"revision": state["revision"], "status": "authorization_pending"}, 0, True
    state["accepted_digest"] = replacement
    error = _apply_amend_effects(state, proposal["affected_steps"], proposal["invalidated"], step_ids, proposal["step_dependencies"])
    if error:
        return state, error, 1, False
    state["amendments"].append({
        "prior_digest": proposal["prior_digest"],
        "prior_criteria": proposal["prior_criteria"],
        "prior_criterion_text": proposal.get("prior_criterion_text", {}),
        "replacement_digest": replacement,
        "new_criteria": new_criteria,
        "criterion_text": proposal.get("criterion_text", {}),
        "affected_steps": proposal["affected_steps"],
        "decision": proposal["decision"],
        "invalidated": proposal["invalidated"],
    })
    state["revision"] += 1
    return state, {"revision": state["revision"], "status": "applied"}, 0, True


def cmd_amend(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    prior_digest = request["prior_digest"]
    if state["accepted_digest"] != prior_digest:
        return state, _error("prior_digest_mismatch", "prior_digest does not match accepted digest"), 1, False
    replacement = request["replacement_digest"]
    new_criteria = request.get("new_criteria")
    if new_criteria is not None:
        for criterion in new_criteria:
            if not re.fullmatch(r"A\d+", criterion):
                return state, _error("unknown_criterion", f"{criterion} is not a valid acceptance identifier"), 1, False
    authorization = request.get("authorization")
    if authorization in ("approve", "reject"):
        return _cmd_amend_authorize(state, request, replacement, state.get("pending_proposal"))
    return _cmd_amend_propose_or_apply(state, request, replacement)


def cmd_reconcile_head(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    if any(step["status"] == "running" for step in state["steps"].values()):
        return state, _error("step_already_running", "a step is still running"), 1, False
    head = request["identity"]["head"]
    if head == request["prior_head"]:
        return state, _error("head_unchanged", "target HEAD must differ from prior_head"), 1, False
    _mark_drift(state)
    state["identity"]["head"] = head
    state["revision"] += 1
    state.setdefault("identity_history", []).append({
        "kind": "reconcile-head",
        "prior_head": request["prior_head"],
        "head": head,
        "revision": state["revision"],
        "owner": request["owner"],
    })
    return state, {"revision": state["revision"]}, 0, True


def cmd_close(state, request):
    error = _require_active_owner(state, request["owner"])
    if error:
        return state, error, 1, False
    if state["pending_revalidation"]:
        return state, _error("needs_revalidation", "step evidence requires revalidation before close-out"), 1, False
    caller_result = request.get("result")
    processed = _process_checks(request.get("checks", []))
    evidence_error = _validate_check_evidence(processed)
    if evidence_error:
        return state, evidence_error, 1, False
    if caller_result == "ready" and processed and all(check["status"] == "passed" for check in processed):
        error = _readiness_error(state, processed, request.get("hashes", {}))
        if error:
            return state, error, 1, False
    state["close_out"]["checks"] = processed
    if caller_result == "ready" and processed and all(check["status"] == "passed" for check in processed):
        state["close_out"]["status"] = "ready"
        state["close_out"]["final_hashes"] = request.get("hashes", {})
        state["close_out"].pop("reason", None)
    else:
        state["close_out"]["status"] = "blocked"
        state["close_out"]["final_hashes"] = {}
        reason = request.get("reason")
        if reason:
            state["close_out"]["reason"] = reason
    state["revision"] += 1
    return state, {"revision": state["revision"]}, 0, True


MUTATION_HANDLERS = {
    "review": cmd_review,
    "accept": cmd_accept,
    "claim": cmd_claim,
    "release": cmd_release,
    "takeover": cmd_takeover,
    "recover": cmd_recover,
    "start": cmd_start,
    "record": cmd_record,
    "block": cmd_block,
    "amend": cmd_amend,
    "reconcile-head": cmd_reconcile_head,
    "close": cmd_close,
}

KNOWN_COMMANDS = set(COMMANDS)


def _is_nonnegative_integer(value):
    return type(value) is int and value >= 0


def _is_nullable_string(value):
    return value is None or isinstance(value, str)


def _is_string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_step_id(value):
    return (type(value) is int and value > 0) or (isinstance(value, str) and bool(value))


def _is_step_id_list(value):
    return isinstance(value, list) and all(_is_step_id(item) for item in value)


def _is_string_map(value):
    return isinstance(value, dict) and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())


def _is_object_list(value):
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _is_check_list(value):
    if not _is_object_list(value):
        return False
    for check in value:
        if (
            ("kind" in check and check["kind"] not in ("auto", "manual", "planned", "exempt"))
            or ("exit_status" in check and check["exit_status"] is not None and type(check["exit_status"]) is not int)
            or ("timed_out" in check and not isinstance(check["timed_out"], bool))
            or ("argv" in check and not _is_string_list(check["argv"]))
            or any(field in check and not isinstance(check[field], str) for field in ("cwd", "log_path"))
            or ("input_hashes" in check and not _is_string_map(check["input_hashes"]))
        ):
            return False
    return True


def _is_processed_check_list(value):
    return (
        _is_check_list(value)
        and all(check.get("status") in ("passed", "failed", "needs-manual-check", "gap") for check in value)
        and _validate_check_evidence(value) is None
    )


def _is_amendment(value):
    return (
        isinstance(value, dict)
        and {"prior_digest", "decision"}.issubset(value)
        and _is_nullable_string(value["prior_digest"])
        and _is_nullable_string(value["decision"])
        and isinstance(value.get("replacement_digest"), str)
        and all(_is_string_list(value.get(field)) for field in ("prior_criteria", "new_criteria"))
        and all(_is_string_map(value.get(field)) for field in ("prior_criterion_text", "criterion_text"))
        and all(_is_step_id_list(value.get(field)) for field in ("affected_steps", "invalidated"))
    )


FIELD_TYPE_PREDICATES = {
    "str": lambda value: isinstance(value, str),
    "bool": lambda value: isinstance(value, bool),
    "str list": _is_string_list,
    "str map": _is_string_map,
    "check list": _is_check_list,
}


NESTED_FIELD_SHAPES = {
    "takeover": {
        "ended_session": ["str", "bool"],
        "user_statement": ["str"],
        "reason": ["str"],
    },
    "recover": {
        "checks": ["check list"],
        "changed_files": ["str list"],
        "hashes": ["str map"],
    },
    "close": {
        "checks.archive": {
            "types": ["object"],
            "fields": {
                "original_path": ["str"],
                "archive_path": ["str"],
                "plan_digest": ["str"],
                "sha256": ["str"],
            },
            "documented_only": True,
        },
    },
}


def _validate_payload_fields(command, request):
    validators = {
        "step_ids": _is_step_id_list,
        "step_dependencies": lambda value: isinstance(value, dict) and all(_is_step_id_list(deps) for deps in value.values()),
        "step": _is_step_id,
        "criteria": _is_string_list,
        "new_criteria": _is_string_list,
        "criterion_text": _is_string_map,
        "affected_steps": _is_step_id_list,
        "invalidated": _is_step_id_list,
        "changed_files": _is_string_list,
        "checks": _is_check_list,
        "hashes": _is_string_map,
        "watched_files": _is_string_map,
        "expected_revision": _is_nonnegative_integer,
    }
    for field in (
        "owner", "previous_owner", "digest", "replacement_digest",
        "prior_head", "handback", "outcome", "result", "authorization",
    ):
        validators[field] = lambda value: isinstance(value, str)
    for field in ("prior_digest", "reason", "decision"):
        validators[field] = _is_nullable_string
    consumed = set(COMMANDS[command]["required"] + COMMANDS[command]["optional"]) | {"expected_revision"}
    for field in consumed & request.keys():
        if field in validators and not validators[field](request[field]):
            return _error("usage", f"{field} has an invalid type or value")
    if "evidence" in request and command in ("recover", "takeover"):
        evidence = request["evidence"]
        if not isinstance(evidence, dict):
            return _error("usage", "evidence must be an object")
        for command_shapes in NESTED_FIELD_SHAPES.values():
            for field, shape in command_shapes.items():
                if isinstance(shape, dict):
                    continue
                if field in evidence and not any(
                    FIELD_TYPE_PREDICATES[name](evidence[field]) for name in shape
                ):
                    return _error("usage", f"evidence.{field} has an invalid type or value")
    return None


def _validate_request(command, request):
    required = set(COMMANDS[command]["required"])
    if command == "amend" and request.get("authorization") in ("approve", "reject"):
        required.discard("affected_steps")
    missing = sorted(required - set(request.keys()))
    if missing:
        return _error("usage", f"request is missing required field: {missing[0]}")
    return _validate_payload_fields(command, request)


def _openspec_refusal(identity):
    root = plan_module.git_toplevel(identity["worktree_root"])
    if not plan_module.is_openspec_repo(root):
        return None
    openspec_path = os.path.join(os.path.realpath(root), "openspec")
    return _error("openspec_project", f"{plan_module.OPENSPEC_REFUSAL}: {openspec_path}")


def run(command, request, state_root):
    if command not in KNOWN_COMMANDS:
        return 2, _error("usage", f"unknown command: {command}")
    if not isinstance(request, dict) or "identity" not in request:
        return 2, _error("usage", "request must be a JSON object with an identity field")
    error = _validate_request(command, request)
    if error:
        return (2 if error["error"]["code"] == "usage" else 1), error
    identity = request["identity"]
    shape_error = _validate_identity_shape(identity)
    if shape_error:
        return 2, shape_error
    openspec_error = _openspec_refusal(identity)
    if openspec_error:
        return 1, openspec_error
    key = state_key(identity)
    state_path, lock_path = _state_paths(state_root, key)
    init_lock = _locked(Path(state_root) / "init.lock", exclusive=True) if command == "init" else nullcontext()

    with init_lock:
        with _locked(lock_path, exclusive=(command != "show")):
            if command == "init":
                exit_code, payload, _ = cmd_init(request, state_path, state_root)
                return exit_code, payload
            if not state_path.exists():
                if _find_conflicting_identity(state_root, identity, key):
                    return 1, _error("identity_mismatch", "identity conflicts with existing state for a different plan or worktree")
                return 1, _error("state_missing", "no state exists for this identity")
            state, error = _load_state(state_path)
            if error:
                return (2 if error["error"]["code"] == "corrupt_state" else 1), error
            expected_identity = {**identity, "head": request["prior_head"]} if command == "reconcile-head" else identity
            error = _validate_identity(state, expected_identity)
            if error:
                return 1, error
            if command == "show":
                return 0, state
            expected = request.get("expected_revision")
            if expected is None:
                return 1, _error("stale_state", "expected_revision is required")
            if state["revision"] != expected:
                return 1, _error("stale_state", "expected revision does not match current revision")
            handler = MUTATION_HANDLERS[command]
            new_state, payload, exit_code, write = handler(state, request)
            if write:
                _write_state(state_path, new_state)
            return exit_code, payload


def describe_commands():
    commands = {}
    for command, spec in COMMANDS.items():
        commands[command] = {
            "required": spec["required"],
            "optional": spec["optional"],
            "shapes": {
                field: ({"types": shape} if isinstance(shape, list) else shape)
                for field, shape in NESTED_FIELD_SHAPES.get(command, {}).items()
            },
        }
    return commands


def _emit(payload):
    print(json.dumps(payload, indent=2))


def _emit_error(code, message, **details):
    print(json.dumps({"error": {"code": code, "message": message, **details}}), file=sys.stderr)


def main(argv):
    if argv == ["describe"]:
        _emit({"commands": describe_commands()})
        return 0
    if len(argv) != 3 or argv[0] != "--state-root":
        _emit_error("usage", "usage: run_state.py [--state-root <dir> <command> | describe]")
        return 2
    state_root = argv[1]
    command = argv[2]
    if command not in KNOWN_COMMANDS:
        _emit_error("usage", f"unknown command: {command}")
        return 2
    if getattr(sys.stdin, "isatty", lambda: False)():
        _emit_error("usage", "stdin must contain a JSON request; interactive input is not supported")
        return 2
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _emit_error("usage", f"stdin is not valid JSON: {exc}")
        return 2
    exit_code, payload = run(command, request, state_root)
    if exit_code == 0:
        _emit(payload)
    else:
        _emit_error(**payload["error"])
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
