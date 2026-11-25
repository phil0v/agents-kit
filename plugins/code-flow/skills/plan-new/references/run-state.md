# Run-state contract

`skills/plan-new/scripts/run_state.py` owns durable plan execution state.
Read the operation sections selected by the calling workflow; consult Schema
and Errors when interpreting a state document or failure. Plan syntax and
digest semantics remain in [plan-format.md](plan-format.md).

## State

Durable execution state lives under `${CODEX_HOME:-${XDG_STATE_HOME:-~/.local/state}}/planning-state/`
as `<sha256(worktree-root + "\0" + plan-realpath)>.json`, with a sibling
`<key>.lock` file used for every mutation. Temporary writes use
`<key>.json.tmp.<random>` and are atomically renamed into place; leftover
temporary files are ignored.

## Identity

The state key is `sha256(worktree_root + "\0" + plan_realpath)`. The stored identity
contains:

- `worktree_root` — absolute canonical worktree root.
- `git_dir` — absolute Git directory.
- `branch` — current branch name or detached marker.
- `head` — current HEAD commit SHA.
- `plan_realpath` — absolute path to the plan file.

Every load validates the request identity against the stored identity.
A mismatched `worktree_root`, `git_dir`, or `plan_realpath` returns
`identity_mismatch`; a changed branch returns `branch_changed`; a changed
HEAD returns `head_changed` with the stored HEAD in `error.prior_head`.
The CLI preserves this field in its stderr JSON. State from another
worktree or a moved plan is never silently reused.

`init` refuses to create state only when another state file already records
the same `plan_realpath` under a different `worktree_root` or `git_dir`; that
is the only case that would silently reuse one plan's state for a different
identity. A second, different plan in the same repository gets its own keyed
file. `init` holds a state-root lock.

## HEAD reconciliation

`reconcile-head` explicitly replaces only the stored HEAD. Its `identity` is
the current target identity, and `prior_head` must match the stored HEAD.
The worktree root, Git directory, branch, and plan realpath still have to
match. The command requires the active owner token and current
`expected_revision`, and refuses an unchanged HEAD or any running step.

Before dispatch, acceptance or amendment mutations, reconcile a changed HEAD:

1. Call `show` with the current target identity. Only on `head_changed`, read
   `error.prior_head` from stderr JSON and call `show` with a copy of that
   identity replacing only `head` with the reported stored HEAD.
2. Use the stored revision and ownership: `claim` released ownership with that
   stored identity, continue this session's active token, or `takeover` with
   the stored identity and ended-run evidence under Ownership below. Without
   evidence, ask the recovery question and stop dependent work.
3. Call `reconcile-head` with the original current target identity, reported
   `prior_head`, active `owner`, and latest `expected_revision` returned by
   the stored-identity operations. Then call `show` with the current identity
   and inspect every `pending_revalidation` obligation under Checkpoints and drift.

An `identity_mismatch` or `branch_changed` blocks reconciliation. A
`step_already_running` refusal requires inspection and interrupted-call
recovery under the stored identity before retrying; never rerun blindly.
A `stale_state` refusal requires reloading and inspecting state before retrying.
Never recreate state, bypass ownership, change immutable identity fields, or
replace the accepted baseline to resolve a refusal. Preserve review receipts,
authorization proposals, evidence, checkpoints and identity history.

## Schema

```json
{
  "version": 1,
  "revision": 0,
  "identity": { },
  "identity_history": [],
  "steps": {
    "1": {"status": "pending", "evidence": [], "depends_on": []}
  },
  "review": {"digest": null, "result": null, "receipts": []},
  "accepted_digest": null,
  "accepted_criteria": [],
  "accepted_criterion_text": {},
  "checkpoints": [],
  "close_out": {"status": "pending", "final_hashes": {}, "checks": [], "reason": null},
  "ownership": {"state": "released", "owner": null, "history": []},
  "amendments": [],
  "pending_proposal": null,
  "pending_revalidation": []
}
```

- `revision` — optimistic-concurrency counter; every successful mutation
  increments it.
- `identity_history` — append-only HEAD reconciliation records with `kind`,
  `prior_head`, target `head`, resulting `revision`, and active `owner`.
  Older version 1 states may omit this list; reconciliation creates it.
- `steps.<id>.status` — one of `pending`, `running`, `done`, `blocked`,
  `needs-manual-check`.
- `steps.<id>.evidence` — list of recorded attempts; each entry has `checks`,
  `flags`, and `status`.
- `review.digest` / `review.result` — latest review receipt.
- `review.receipts` — all review receipts, newest last.
- `accepted_digest` / `accepted_criteria` / `accepted_criterion_text` — user-authorized execution baseline. `accepted_criteria` is the list of criterion identifiers; `accepted_criterion_text` is an optional mapping from identifier to the exact criterion text at the time of acceptance. The text is used to detect reworded requirements that keep the same identifier.
- `checkpoints` — verified changed-file sets with content hashes.
- `close_out.status` — one of `pending`, `ready`, `blocked`.
- `close_out.checks` — processed checks from the latest close-out attempt.
- `close_out.reason` — reason persisted when close-out is `blocked`.
- `ownership.state` — `active` or `released`.
- `ownership.owner` — opaque token for the active session.
- `ownership.history` — ownership transitions with `kind`.
- `amendments` — authorized plan changes with `prior_digest`.
- `pending_proposal` — an amendment proposal awaiting user authorization when
  the acceptance baseline would change.
- `pending_revalidation` — step IDs whose evidence needs verification after
  drift or HEAD reconciliation. These obligations survive amendments and
  evidence resets. Older version 1 states may omit this list; loading them
  infers unresolved obligations from evidence flags and step status.
  Completed steps with unresolved flags move to `needs-manual-check` so
  `recover` can verify them. Histories with clean latest evidence stay
  completed even when earlier evidence has revalidation flags.

## Revision and locking

Every mutation acquires an exclusive `fcntl.flock` on the `.lock` file before
validation and holds it through the atomic write.
Mutations require `expected_revision`; a mismatch after the lock is acquired
returns `stale_state`. Reads use a shared lock. The lock file is never
removed, so atomic replacement never changes its inode.

## Ownership

`claim` returns a new owner token and marks ownership `active`. The same
owner token authorizes subsequent mutations without repeated approval. A
second `claim` while ownership is active returns `owner_active`. `release`
records a normal stop. `takeover` replaces an active owner after explicit
evidence that the previous run ended (`ended_session` result or a
`user_statement`); without evidence it returns `recovery_question_required`
and leaves state unchanged. Wrong owner tokens return `owner_mismatch`.

## Step lifecycle

`start` moves a `pending` step to `running` only when the plan has an
accepted digest, a review receipt whose digest matches the accepted digest,
and a clean review verdict. It refuses with `not_authorized`,
`review_missing`, `review_stale`, or `review_unclean` when any of those gates
is not met. Only one step may be running at a time (`step_already_running`).
`record` records a worker handback and check results; `done` requires
passing checks, a non-empty handback, and hashes for relevant files. `block`
marks a step `blocked`. `recover` reconciles a `running`, `blocked`, or
`needs-manual-check` step to `done`, `pending`, or `blocked` before another
`start`; a `done` recover outcome requires passing processed checks and
relevant-file hashes.

Check kinds determine completion:

- `auto` — `exit_status` 0 is `passed` only when `timed_out` is not true;
  anything else is `failed`. Optional `timed_out` must be a boolean.
- `manual` — status `needs-manual-check`.
- `planned` — status `gap`; the step cannot be `done`.
- `exempt` — status `passed`; use for a step whose contract is
  `test: none -- <reason>`.

An empty check list is never `done`. A step with any failed check becomes
`blocked`; a step with any manual or planned check becomes
`needs-manual-check`.

## Checkpoints and drift

`record` and `recover` add a checkpoint for `done` outcomes containing the
verified changed-file set and content hashes. Intentional edits within the
next authorized call advance the checkpoint without erasing history.

Drift is checked when `watched_files` is supplied: the request maps relative
paths to the expected SHA-256 hashes recorded in the latest checkpoint or
close-out. For the plan file itself, the expected hash is the plan digest
from `plan.py`; ticking a step-heading checkbox or moving an anchor line
therefore does not register as drift, while a semantic edit does. A watched
path absent from the working tree is
drift only while it is still tracked at the current HEAD — an uncommitted
deletion. A path deleted by an already-committed change is owned by HEAD
reconciliation: its deletion is not watched-file drift, and the revalidation
obligations recorded at reconciliation time remain its durable record. A
tracked-at-HEAD state that cannot be determined reports drift: an
indeterminable classification never silently skips a watched path. A mismatch
outside the verified set is `unexplained_drift`; the state marks affected
evidence `needs_revalidation`, moves non-pending evidence-bearing steps to the
recoverable `needs-manual-check` status, and sets `close_out` to `blocked`.
Branch changes stop
for reconciliation (`branch_changed`); HEAD changes stop for reconciliation
(`head_changed`).

`reconcile-head` uses the same drift invalidation: it conservatively marks
the latest evidence of every step that has evidence `needs_revalidation`.
Pending steps stay pending and must pass their reviewed `start` / `record` path;
other evidence-bearing steps become recoverable `needs-manual-check`.
Reconciliation blocks close-out and clears current final hashes. Existing evidence, checkpoints,
review receipts, amendments, the accepted baseline,
and pending authorization proposals are preserved. It appends an identity
history record rather than accepting a new plan baseline. Drift and HEAD
reconciliation also add affected step IDs to `pending_revalidation`.
`close` refuses with `needs_revalidation` while that list is nonempty,
independently of the current evidence entries.

Coordinators compare the working tree with the latest verified checkpoint before
each dispatch, passing checkpoint hashes as `watched_files`. After drift or HEAD
reconciliation, reload state and verify every `pending_revalidation` step against
its affected acceptance criteria, current HEAD and behavior. For a recoverable step,
use `recover` with `outcome: "done"` only after verification and supply current
passing `checks` and relevant-file `hashes` in `evidence`. Successful recovery
appends clean evidence and a checkpoint, preserving earlier flags/checkpoints.
Failed, manual, unavailable or incomplete verification leaves readiness blocked.
If amendment reset a step to pending, complete its reviewed `start` / `record`
path with verified current checks and hashes. Only verified `record` or `recover`
completion as `done` clears its durable obligation, including obligations inferred
from legacy flags. Once all obligations are cleared, close-out can become ready
after its own checks pass.

## Amendments

`amend` records a plan change. It requires `prior_digest` to match
`accepted_digest` (`prior_digest_mismatch` otherwise).

When the request includes `new_criteria` that differ from
`accepted_criteria`, or when the text of any criterion present in both
baselines differs from `accepted_criterion_text`, the amendment changes the
acceptance baseline. The command records the proposal in `pending_proposal`
with both the prior and replacement criteria and their text, without
replacing the accepted baseline. Execution remains blocked with
`authorization_pending` until the user approves (`authorization: "approve"`)
or rejects (`authorization: "reject"`) the proposal. A rejected amendment
leaves `accepted_digest`, `accepted_criteria`, `accepted_criterion_text`,
completed step history, and review state untouched.

When the acceptance baseline is unchanged, `amend` applies immediately:
it replaces `accepted_digest`, resets the requested steps to `pending`,
invalidates requested step or `close-out` results, and can append new step
IDs. Pass `step_ids` to guard against removal or renumbering: every step ID
already known to state must still be present, or the amendment is refused
with `step_removed`. New IDs may be appended; their dependencies are read
from `step_dependencies`.

Amendments preserve `pending_revalidation` and any evidence history carrying
revalidation flags, including when resetting those steps to `pending`.
Revalidate an amended pending step through its reviewed `start` / `record`
sequence; changing the plan or acceptance baseline does not discharge its
verification obligation.

A changed revision invalidates the former review receipt: after an amendment
`start` is refused with `review_stale` when a review receipt exists for a
different digest than the accepted one. A missing receipt is refused with
`review_missing` and an unclean verdict with `review_unclean`.

## CLI protocol

`run_state.py --state-root <dir> <command>` reads a JSON request from stdin
and prints JSON on stdout; exit 0 is success, 1 is invalid or blocked input,
2 is usage or I/O failure. Malformed stdin JSON or an unknown command exits
2 with code `usage`.

`run_state.py describe` prints the command/field table as JSON on stdout and
exits 0; it is the machine-readable source for every command's required and
optional request fields.

Every mutating command except `init` requires `identity` plus
`expected_revision`; `init` requires `identity`.

```json
{
  "identity": {
    "worktree_root": "/absolute/repo",
    "git_dir": "/absolute/repo/.git",
    "branch": "main",
    "head": "abc123...",
    "plan_realpath": "/absolute/repo/plans/change.md"
  },
  "expected_revision": 0
}
```

## Initialization and review operations

- `init` creates state for the identity from all `step_ids` and
  `step_dependencies` reported by `plan.py inspect`. Omitted graph fields
  are derived from the parsed plan; supplied fields must match its complete
  graph. Duplicate or unknown IDs, omitted steps, and changed or omitted
  dependencies are rejected without creating state. It reads the plan at
  `identity.plan_realpath` and refuses any checked step with
  `completion_state_missing`: restore the original durable state before
  continuing. This rule applies to both plan-new and plan-do's fresh-state
  fallback. Never translate progress ticks into durable evidence, rerun
  checked work, or remove completed dependencies to initialize a subset.
  Existing state remains authoritative; checkbox changes alone do not alter
  its statuses or the plan digest. Unknown, self-referential, and cyclic step
  dependencies are rejected. Returns `revision`.
- `show` returns the full state document.
- `review` appends a review receipt.

## Execution and ownership operations

- `accept` sets the authorized baseline. `criterion_text` maps each criterion
  identifier to its exact text and is used to detect reworded requirements.
- `claim` takes ownership and returns the `owner` token.
- `release` records a normal stop; with `watched_files` it checks drift.
- `takeover` replaces an active owner only with ended-run evidence.
- `recover` reconciles a step before another `start`; `outcome` must be
  `done`, `pending`, or `blocked` — anything else is rejected with `usage`.
- `start` moves a pending step to running; with `watched_files` it checks
  drift.
- `record` stores a worker handback and check results.
- `block` marks a step blocked.
- `reconcile-head` reconciles only HEAD, preserves the accepted baseline,
  and invalidates evidence and close-out as described above. Returns
  `revision`.
- `close` persists the processed checks and, when the result is not `ready`
  or the checks are not all passed, the reason so that `show` can explain a
  blocked close-out. A passing `ready` request additionally requires every
  step durably `done`, an accepted digest matching a clean review, no pending
  authorization or revalidation, complete passing check evidence, and nonempty
  final relevant-file hashes. Missing readiness prerequisites are rejected
  without writing state. The coordinator verifies current plan and file hashes,
  including the archive receipt under terminal recovery; state identity does
  not move to the archive path.

Loading an existing `ready` result applies the same readiness rule after legacy
revalidation migration, including recomputing check statuses from their evidence.
An inconsistent result becomes `blocked` in memory, clears its final hashes,
and reports `readiness invalidated` with the failed prerequisite in `reason`.
Steps, history and recorded checks are preserved. `show` does not write this
recovery; a subsequent authorized mutation persists it with that mutation's
revision. Malformed state fields or inconsistent review receipts instead return
`corrupt_state`; restore the original durable evidence before resuming.

## Amendment operation

- `amend` records a plan change. `criterion_text` maps criterion identifiers
  to their exact text and is used together with `new_criteria` to decide
  whether the acceptance baseline changed; a changed baseline parks the
  proposal in `pending_proposal` until `authorization` approves or rejects
  it.

## Errors

Stable error codes include `state_exists`, `state_missing`,
`corrupt_state`, `identity_mismatch`, `branch_changed`, `head_changed`,
`head_unchanged`, `needs_revalidation`,
`stale_state`, `owner_active`, `owner_mismatch`,
`recovery_question_required`, `step_not_pending`, `step_already_running`,
`step_not_running`, `missing_handback`, `oversized_handback`, `missing_hashes`, `step_not_done`,
`prior_digest_mismatch`, `unexplained_drift`, `authorization_pending`,
`not_authorized`, `review_missing`, `review_stale`, `review_unclean`,
`unknown_step`, `step_removed`, `no_pending_proposal`, `proposal_mismatch`,
`unknown_dependency`, `self_dependency`, `cyclic_dependency`,
`unknown_criterion`, `incomplete_check_evidence`, and `usage`.
