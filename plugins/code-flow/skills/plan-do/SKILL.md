---
name: plan-do
description: Execute a reviewed implementation plan sequentially, resume interrupted work from durable state, automatically route proven internal evidence-only anchor drift through plan-revise, and close out once through wrap-up. Use for explicit execution requests, not standalone planning or amendments.
argument-hint: "[<plan-path>] [--no-commit]"
---

# Execute and resume a plan

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first.

Read `<package>/references/dispatch.md` at that root. Follow [Package resources](../../references/dispatch.md#package-resources) for resource locations, load-once, and compaction reload. Use `<package>/scripts/role-profile.py` as specified in [Effective profiles](../../references/dispatch.md#effective-profiles).

Read [plan format](../plan-new/references/plan-format.md) for syntax and [Digest and review](../plan-new/references/plan-format.md#digest-and-review) for digest rules. Use `<package>/skills/plan-new/scripts/plan.py` for parsing, validation, selection, digests, and anchor checks. Use `<package>/skills/plan-new/scripts/run_state.py` for durable state.

From [run-state](../plan-new/references/run-state.md#state), load only: `State`, `Identity`, `Revision and locking`, `Ownership`, `Step lifecycle`, `Checkpoints and drift`, `CLI protocol`, `Initialization and review operations`, `Execution and ownership operations`. Consult `Schema` or `Errors` when needed.

## Trigger and boundary

Run only on an explicit request to execute or resume an accepted plan. Do not edit plan contracts. Use `plan-new` for planning and `plan-revise` for contract changes, including automatic anchor amendments. Scripts refuse OpenSpec projects with `openspec_project`. Use the native workflow or `<package>/skills/spec-flow/SKILL.md` separately; do not start either from this skill.

## Preflight

1. Accept a plan path and `--no-commit`. Reject other flags. Explicit invocation authorizes this run's scoped commit unless `--no-commit` applies.
2. Without a path, run `<package>/skills/plan-new/scripts/plan.py select <repository>`. Use the sole candidate. For multiple candidates, list them in sorted order and ask which to run. If no choice is possible, stop with the list.
3. Use direct read-only Git commands to find the canonical worktree root, absolute Git directory, branch or detached marker, and HEAD. Record staged, unstaged, and untracked work with `git status --porcelain`. Preserve unrelated edits. Block unsafe overlaps.
4. Before validation, follow [terminal archive recovery](../plan-new/references/plan-format.md#terminal-archive-recovery) for missing or archived paths.
5. Run `<package>/skills/plan-new/scripts/plan.py preflight <plan> [--steps <IDs>]` as the entry check for Context and remaining steps. Keep the original diagnostics. Stop on validation errors.
6. Read `## Decisions` and `## Open questions`. Block remaining steps with unresolved questions until the user resolves them. Block steps affected by changed acceptance or unexplained requirement changes until the user authorizes them.
7. Run `<package>/skills/plan-new/scripts/run_state.py --state-root <state-root> show` under the shared identity and storage rules. If it reports `head_changed`, follow [HEAD changes](#head-changes).

### New run without durable state

1. If any step is checked, stop until the original state is restored. Follow [Initialization and review operations](../plan-new/references/run-state.md#initialization-and-review-operations).
2. Before `init`, run the plan's `## Verification` commands once. Record this baseline with the preflight evidence. Before dispatch, resolve failures at the untouched HEAD or explicitly agree on their scope with the user. Use the baseline to distinguish new failures at each step.
3. If every step is unticked, use `init` under the shared rule above. Supply all `step_ids` and `step_dependencies` from `plan.py inspect`.

### Resume with durable state

Reuse the existing state. Use durable step statuses, not ticks, to select remaining work. Do not rerun the baseline.

### Review, acceptance, and ownership

1. If the displayed digest has no review or has a stale review, return to `plan-new` for re-review. Require a clean receipt before acceptance or dispatch.
2. If the digest is not accepted and the user explicitly requested execution, run `<package>/skills/plan-new/scripts/run_state.py --state-root <state-root> accept`. Supply the digest, acceptance identifiers as `criteria`, exact text as `criterion_text`, and current `expected_revision`.
3. Follow [Ownership](../plan-new/references/run-state.md#ownership): `claim` released ownership, continue with this session's active token, or require ended-run evidence for takeover. Without that evidence, ask the recovery question and stop dependent work.
4. If work is interrupted, use `recover` before another `start`.
5. Resolve revalidation under [Checkpoints and drift](#checkpoints-and-drift). Then evaluate source-anchor drift under [Automatic anchor amendments](#automatic-anchor-amendments) before dispatch.

### User pause

If the user explicitly asks to pause, stop without concluding the run:

1. Use `release` with a resume reason. Omit `watched_files` before an imminent stash or reset to prevent false drift. Include any stash commit SHA in the reason so a later pop is traceable in durable state.
2. Keep the interrupted step's status and leave unmerged worker results unmerged.
3. On resume, `claim` ownership. If a step was interrupted, use `recover` before any `start`.

## HEAD changes

On `head_changed`, load the shared [HEAD reconciliation procedure](../plan-new/references/run-state.md#head-reconciliation) and inspect the stored HEAD and state before further dispatch or acceptance mutations.

Look for a reachable commit after the stored HEAD whose parent is that HEAD and whose paths exactly match the recorded scope. Use the exact-set predicate in [terminal archive recovery](../plan-new/references/plan-format.md#terminal-archive-recovery), not containment. On a match, take the terminal path below. Otherwise, follow the shared HEAD reconciliation procedure and [Checkpoints and drift](#checkpoints-and-drift) before continuing.

### External completion

1. Verify the scoped blobs against the run's verified hashes. Re-run the affected checks at the new HEAD.
2. Follow the shared reconciliation procedure.
3. Discharge every `pending_revalidation` obligation according to the holding step's status:
   - For a recoverable step (`running`, `blocked`, `needs-manual-check`), record the re-run checks as verified `recover` evidence.
   - For a step reset to `pending`, use the reviewed `start`/`record` path. Use the external-commit verification as the handback.
4. Save close-out as `blocked` with a reason that names the external commit as `completed externally`. Run `release`. Report which blobs the run did not verify.

## Checkpoints and drift

Follow run-state [Checkpoints and drift](../plan-new/references/run-state.md#checkpoints-and-drift) for pre-dispatch `watched_files`, checkpoint updates after authorized step or close-out edits, and every `pending_revalidation` obligation after drift or HEAD reconciliation. Stop for inspection if changes before the next call or outside its verified changed-file set are unexplained.

Preserve completion history if an authorized edit changes an earlier step's quotation. Recheck Context and all remaining-step anchors before dispatch. Use [Automatic anchor amendments](#automatic-anchor-amendments) only for proven internal evidence-only drift. Changed prerequisites block dispatch for inspection or a user decision.

## Automatic anchor amendments

If source anchors drift during this authorized run, load the complete [anchor-amendment contract](../plan-new/references/anchor-amendment.md) before deciding eligibility. Follow `Eligibility and provenance`, `Handoff`, `Rewrite and independent review`, and `Resumption`, including the nested handoff to `<package>/skills/plan-revise/SKILL.md`. Keep ownership, commit authorization, and implementation dispatch in this run.

## Dispatch loop

1. Choose the lowest-numbered `pending` step with all explicit dependencies `done`.
2. If another step is `running`, recover it first.
3. Use `start` before spawning the worker. Keep the step `running` while work is in progress.

Keep all coordinator step evidence in one durable directory for the run, outside the worktree and readable by delegated agents. For example, use `mktemp -d` under the system temp directory. Store context packs, `run-check.py` logs, and the style-gate `--files-from` JSON list and report there.

Dispatch one fresh, independent `worker` at a time. Follow [Role resources](../../references/dispatch.md#role-resources) for the effective `<package>/roles/worker.toml` profile, canonical `developer_instructions` by reference, and exact-check-argv rule. Supply:

- The step ID, title, body, `depends on`, `acceptance`, and `done when`.
- The declared `test` with the interpreter resolved under [Execution](../../references/checks.md#execution).
- The exact lint command: `<resolved-python> "<package>/scripts/style-gate.py" --repo "<repository>" --check-only <assigned-paths> > "<run-dir>/step-<id>-style-gate.json"`, or the exact project lint command from `## Checks` or repository instructions. Require the worker to run that redirected command (or the project lint command) before handback.
- Common plan context above `## Steps`, assigned paths from that context and the step contract, and acceptance IDs, criterion identifiers, and exact text from `## Acceptance`.
- A fresh context pack path. Use `<package>/scripts/context-pack.py --repo "<repository>"` over the assigned paths. Regenerate the pack when paths change. Never reuse it across steps.
- The plan path, accepted digest and kind, state ownership token, and repository instructions location. Use the `plan.py` normalized digest, not raw file `shasum`. Confirm that the `digest` field from `plan.py validate` matches the accepted digest.
- The repository instructions and constraint files.

Do not gather implementation context before the call.

## Verify and record

Verify the handback against current files, the scoped diff, and the reported checks. A failed check, unavailable required tool, or missing handback prevents `done`. Keep the worker's three-attempt limit for the same check failure.

For each step, run its declared `test` and only the additional checks from [Selection](../../references/checks.md#selection) for its changed files. At completion, run required cross-step and acceptance checks. Apply [Reuse within a run](../../references/checks.md#reuse-within-a-run) to both. Set `TMPDIR` to the durable artifact directory for every check so `log_path` stays available.

Before `record`, follow [Pre-round style gate](../../references/closeout.md#pre-round-style-gate) with these step rules:

1. Read `<run-dir>/step-<id>-style-gate.json` in the durable run directory if that file exists. Recompute the SHA-256 of every file in the step's verified changed-file set.
2. Reuse that saved report if it meets the reuse condition in [Pre-round style gate](../../references/closeout.md#pre-round-style-gate) over the step's verified changed-file set. A recorded passing gate entry with valid shared reuse evidence also qualifies under that same condition. Otherwise, run `<resolved-python> "<package>/scripts/style-gate.py" --repo "<repository>" --files-from <json> > "<run-dir>/step-<id>-style-gate.json"` over the step's verified changed-file set.
3. Record the report's post-format `sha256` for formatter changes.
4. Use `worker` to fix lint diagnostics until the gate exits 0. Remaining diagnostics fail the step check; do not defer them to wrap-up.
5. Record `style-gate` with changed-file hashes as `input_hashes` and `<run-dir>/step-<id>-style-gate.json` as `log_path`.

Use `<package>/skills/plan-new/scripts/run_state.py record` under [Step lifecycle](../plan-new/references/run-state.md#step-lifecycle) and [Checkpoints and drift](../plan-new/references/run-state.md#checkpoints-and-drift) for status, checks, handback, hashes, and checkpoint. Before the first mutating call of each command in a session, get its request fields from `run_state.py describe`, not prose.

After recording completion, tick `N. [ ]` to `N. [x]` in the step heading. This does not change the digest or review receipt. Do not rerun completed steps after later authorized edits. Recover interrupted calls before dispatch.

If a step cannot execute as agreed, stop for `plan-revise`. Make routine choices within the accepted contract without further approval. If the expected results must change, ask the user to decide.

## Close-out

### Run wrap-up

When all required steps are proven `done` and `pending_revalidation` is empty:

1. Compare `gate_fingerprint` across the verified `step-<id>-style-gate.json` files:
   - If all values are equal, merge the files into `<run-dir>/wrapup-style-gate.json` in the durable run directory using the `style-gate.py` JSON schema. Keep the latest verified `files` entry per path across steps, `deadline_exceeded: false`, and that shared `gate_fingerprint`. Keep `uncovered` as the union of the step reports' `uncovered` lists. Keep the shared top-level metadata (`repo`, `check_only`, `deadline_seconds`, `rule_family_coverage`).
   - If the values differ, do not merge step reports. Instead, run `<resolved-python> "<package>/scripts/style-gate.py" --repo "<repository>" --files-from <json> > "<run-dir>/wrapup-style-gate.json"` over every plan-owned file. Follow [Pre-round style gate](../../references/closeout.md#pre-round-style-gate) for formatter updates, affected checks, and fixes. Stop on exit 2. Use `worker` to fix exit-1 lint diagnostics. Rerun the gate if files change so `<run-dir>/wrapup-style-gate.json` holds verified post-edit entries.
2. Follow `<package>/skills/wrap-up/SKILL.md` with `--no-commit` exactly once. Pass the actual plan-owned files, the acceptance checklist from the baseline, and `references already loaded in this conversation, unchanged since load: <paths>` per [Package resources](../../references/dispatch.md#package-resources). Pass `<run-dir>/wrapup-style-gate.json` when every non-excluded plan-owned file has a verified entry. Keep close-out ownership in this run.

### Decide readiness

Use `ready` only if wrap-up reports complete passing coverage and passing acceptance. Set `blocked` for:

- Required independent inspection that could not run. Report it as a limitation.
- An interrupted step that is not reconciled with `recover` before close-out, or any interruption of wrap-up.
- A check without a result.
- An unresolved Critical or Major finding.
- An unresolved or declined code-design finding from the final style pass.

If wrap-up reports a final style coverage gap as a limitation, do not count it as incomplete coverage. Missing test tools that wrap-up reports as limitations do not block readiness. Record final relevant-file hashes after close-out fixes. If they change later, set close-out to `blocked`.

### Finalize, release, and commit

Follow the [completed plan archive rules](../plan-new/references/plan-format.md#completed-plan-archive). Use `close` to save the result and any required archive receipt. Then `release` under the recorded identity even on `--no-commit` runs, before any scoped commit to avoid HEAD reconciliation for that commit.

If `--no-commit` applies, do not stage or commit the edits. Otherwise, this run owns the scoped commit after clean archive finalization:

1. Inspect the final task diff and index before staging. If you cannot isolate plan-owned edits safely, stop and report the overlap. Stage only those edits. Preserve unrelated staged work.
2. Commit with one English line and no `Co-Authored-By`. Verify the commit's paths against the recorded scope.

## Constraints

Stay on the current branch. Create no worktree. Do not merge, rebase, or push. On clean close-out, leave edits uncommitted only under `--no-commit`.

Report the outcome:

- `ready — committed <hash>` after the close-out commit lands.
- `ready — not committed` under `--no-commit`.
- `blocked — <reason>` with evidence if a step, check, drift, or unresolved decision prevents completion.
