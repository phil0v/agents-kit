---
name: plan-revise
description: Amend remaining work in an accepted plan while preserving completed history and accepted criteria. Use for explicit amendments or plan-do's proven internal evidence-only anchor handoff; a standalone revision does not implement the plan.
argument-hint: "[<plan-path>] [<feedback-or-blocker>]"
---

# Amend a plan

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first. Read `<package>/references/dispatch.md` at that root and apply its `## Package resources` section for resource locations, resolving active-harness effective roles with `<package>/scripts/role-profile.py`.

Read `<package>/skills/plan-new/references/plan-format.md` for plan syntax and digest semantics. Use `<package>/skills/plan-new/scripts/plan.py` for parsing, validation, selection, digest, and anchor checks. Use `<package>/skills/plan-new/scripts/run_state.py` to record amendments, acceptance baseline decisions, and review invalidation.

From [run-state](../plan-new/references/run-state.md#state), load only: `State`, `Identity`, `Revision and locking`, `Ownership`, `Step lifecycle`, `Checkpoints and drift`, `Amendments`, `CLI protocol`, `Initialization and review operations`, `Execution and ownership operations`, `Amendment operation`. Consult `Schema` or `Errors` when needed.

Scripts refuse OpenSpec projects with the `openspec_project` diagnostic; use the native OpenSpec workflow or `<package>/skills/spec-flow/SKILL.md` there.

## Trigger and boundary

Use for explicit revisions, worker blockers and plan-do's proven evidence-only anchor handoff. Do not create new plans, execute implementation, commit or write OpenSpec artifacts. `plan-new` owns planning; `plan-do` owns execution/resumption.

Without an accepted baseline, amend the draft with `prior_digest` null and acceptance criteria as `new_criteria`: proposing the first baseline uses the same user-authorization gate as changing one. Standalone revision ends with a validated, reviewable plan. A nested authorized plan-do run resumes only after acceptance and re-review.

## Nested anchor-amendment handoff

Only for plan-do's [automatic route](../plan-do/SKILL.md#automatic-anchor-amendments),
load and follow the complete [anchor-amendment contract](../plan-new/references/anchor-amendment.md)
at `<package>/skills/plan-new/references/anchor-amendment.md`. It owns proof rechecking,
rewrite limits, preservation, authorization, revision and independent-review gates,
and the return evidence required before plan-do alone resumes implementation.

## Read before rewriting

Accept the plan path and feedback or blocker verbatim. Read affected current code and the plan. Preserve existing IDs and consecutive numbering; completed contracts/evidence are immutable history. Amend unfinished steps in place; append IDs to undo or extend completed behavior.

Resolve canonical worktree root, absolute Git directory, branch/detached marker, HEAD and plan realpath. Record staged, unstaged and untracked work with `git status --porcelain`; preserve unrelated edits. Reuse state and follow HEAD changes below before amendments.

Rewrite affected remaining steps/dependencies together and keep the summary consistent. Unneeded implementation becomes explicit verification under the same ID and unchanged acceptance baseline.

## HEAD changes

Before amendment mutations, load and follow the shared [HEAD reconciliation procedure](../plan-new/references/run-state.md#head-reconciliation) on `head_changed`. Retain every `pending_revalidation` obligation for the amendment and handoff to plan-do; standalone revision leaves implementation verification to plan-do.

## Acceptance baseline

Preserve accepted requirements. For changed behavior present exact before/after text for user authorization; block execution pending a decision and retain the former accepted revision. A blocker never authorizes weaker tests or removed requirements.

Take ownership under run-state Ownership before `amend`: `claim` released ownership; takeover requires ended-run evidence or the recovery question. Without the active token amendments return `owner_mismatch`.

Pass acceptance identifiers and exact text from `## Acceptance` to `amend`. Follow loaded Amendments: unchanged criteria apply immediately; changed criteria/text block execution until decision. Approval replaces the baseline and invalidates requested results; rejection preserves baseline/history/execution authorization.

Preserve `pending_revalidation`, including reset steps and legacy flagged evidence, plus historical flags/checkpoints. Hand unresolved verification to `plan-do`; standalone revision performs no implementation and fabricates no recovery evidence. Pending steps need their reviewed start/record path; readiness requires current verification of every obligation.

## Validation and review

Run `<package>/skills/plan-new/scripts/plan.py preflight <plan> [--steps <IDs>]` for changed and remaining steps. Resolve all diagnostics before review.

Dispatch the plan-reviewer only after the rewritten plan is reconciled into the repository of record and `preflight` passes over it: an isolated reviewer reads a spawn-time snapshot of the coordinator's worktree, so a dispatch that precedes reconciliation reviews a stale revision. A reviewer that reports a digest mismatch against the current plan is aborted without a verdict and re-dispatched after reconciliation; its result is never recorded.

A changed revision invalidates the former receipt. Send the plan path and owed IDs (changed or newly inconsistent remaining steps) to `<package>/roles/plan-reviewer.toml`, covering changed/added steps, dependency effects and whole-plan Coverage/Design. Follow plan-new's [bounded independent review](../plan-new/SKILL.md#review).

For the final digest use its [Review receipt procedure](../plan-new/SKILL.md#review-receipt). Unresolved findings leave an explicitly unready draft with actionable blockers.

## Amendment dispatch

Delegate the scoped rewrite to `<package>/roles/worker.toml` with the plan-format reference, verbatim feedback/blocker (or nested handoff), affected IDs and assigned plan paths. Add no role; edit only the plan in place. Plan-do owns resumed implementation after amendment/review gates pass.

## Constraints

Stay on the current branch; create no worktree, merge, rebase or commit. Leave checkboxes unchanged: they do not affect the digest and completion evidence belongs to `plan-do`.

Keep the plan in place. End with its repository-relative link, summary, decisions awaiting authorization and blockers.
