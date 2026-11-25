---
name: plan-new
description: Create and independently review an implementation plan grounded in the current repository. Use for requests to plan a change; never for execution, commits, or OpenSpec artifact writes.
argument-hint: "[<change-request>]"
---

# Create and review a plan

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first. Read `<package>/references/dispatch.md` at that root and apply its `## Package resources` section for resource locations, resolving active-harness effective roles with `<package>/scripts/role-profile.py`.

Read `<package>/skills/plan-new/references/plan-format.md` for the shared plan contract. Use `<package>/skills/plan-new/scripts/plan.py` for parsing, validation, selection, digest, and anchor checks. Use `<package>/skills/plan-new/scripts/run_state.py` to record review receipts.

From [run-state](../plan-new/references/run-state.md#state), load only: `State`, `Identity`, `Revision and locking`, `CLI protocol`, `Initialization and review operations`. Consult `Schema` or `Errors` when needed.

Scripts refuse OpenSpec projects with the `openspec_project` diagnostic; use the native OpenSpec workflow or `<package>/skills/spec-flow/SKILL.md` there.

## Trigger and boundary

This skill fires on explicit requests to plan a change, write an implementation plan, or prepare a plan before doing the work. It does not implement code, revise code, make commits, run `plan-do`, or start any execution workflow. It leaves the target repository's working tree untouched.

## Investigation

Before drafting, read the target repository's instructions, the relevant existing code, tests, callers, and at least one nearby established skill pattern. Gather repository facts with tool calls; batch genuine decisions and ask the user when choices affect scope or behavior. Prefer complete vertical slices, using additive expand/migrate/contract steps for broad migrations. For any step that makes two consumers share one source, such as a validator and the docs that describe it, or a config and the forms generated from it, state each consumer's current behavior and mark it preserved or changed, so behavior preservation is a stated decision rather than a review-round discovery. Before drafting a live-probe step, verify live-probe isolation, auth feasibility and behavior, and exact hook trigger inputs.

## Plan location

Save English plans to `plans/<slug>.md`, or `plans/<ticket>/<slug>.md` when the request supplies a ticket. Derive `<slug>` from the request. If a file already exists at the chosen path, preserve it and ask whether to overwrite, append, or choose a new name; do not silently replace an existing plan.

## Validation

Before showing the plan to the user or sending it for review, run `<package>/skills/plan-new/scripts/plan.py validate <plan>` and `<package>/skills/plan-new/scripts/plan.py anchors <plan>`. Fix every diagnostic and anchor drift in the plan. Keep the validation result as evidence.

This skill keeps the separate `validate` and `anchors` calls instead of `preflight` on purpose: it loops over individual diagnostics while fixing a draft, so it needs the per-command payloads.

## Review

Resolve durable state before selecting owed steps under [Initialization and review operations](references/run-state.md#initialization-and-review-operations). Without state, checked steps block planning until state is restored; do not infer completion or rerun them. With state, its step statuses are authoritative, regardless of progress ticks.

Send the plan path and all owed step IDs to the existing `<package>/roles/plan-reviewer.toml` role. Owed steps are those not durably `done`, or all steps of a fresh unticked plan. Follow `dispatch.md` to create a fresh independent agent using the reviewer's effective profile for the active harness, with its canonical `developer_instructions` carried by reference per `dispatch.md` Role resources, the absolute package root, the plan path, and `owed: <step numbers>`.

Resolve returned findings by editing the plan. After each round, re-validate and re-check anchors, then request a follow-up review covering the steps the latest fixes changed or made newly inconsistent, plus whole-plan Coverage and Design if any acceptance criterion or common rule changed.

Allow at most two correction rounds. When the limit is exhausted with converging findings — the latest review round introduced no new finding category — present the remaining findings and a proposed ruling for each to the user; the user's explicit approval authorizes one further correction round at a time, repeatable per approval while findings keep converging. Otherwise, if unresolved findings remain, leave the plan as an explicitly unready draft with no clean review receipt.

## Review receipt

Resolve the repository and plan identity before touching state. Initialize missing state only under the shared initialization rule with `<package>/skills/plan-new/scripts/run_state.py --state-root <state-root> init`, passing all `step_ids` and `step_dependencies` from `plan.py inspect`. `init` inspects the plan itself and refuses checked steps without durable state. Reuse existing state for the same plan; never omit completed dependencies or synthesize completion evidence.

Only record a clean review receipt for a complete review of the current revision with no actionable findings and the exact response `verdict: clean`. Missing, malformed, incomplete or `verdict: needs revision` responses produce no clean receipt, even if they contain a clean verdict elsewhere.

Map that response to `"result": "clean"` by running `<package>/skills/plan-new/scripts/run_state.py --state-root <state-root> review` with the current digest and the state's current `expected_revision`. This is a `review` mutation of the run state. Never record a receipt for an older digest: if the plan is edited after review, request a new review first.

Acceptance of the reviewed plan is not recorded here; `plan-do` records it with the run-state `accept` command at the first explicit execution request.

## Finish

End with the repository-relative plan link, step summary, material decisions and remaining blockers/findings. State that implementation has not started and no commit was made. Never automatically start `plan-do`.
