---
name: wrap-up
description: Finish an existing change with one combined style and correctness review round that traces findings to root causes, delegated fixes for every finding, checks, a combined final review round, and an authorized scoped commit. Use for close-out, not review-only requests.
argument-hint: "[<scope>] [--gate] [--no-commit]"
---

# Wrap up a change

## Package resources

1. Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first.
2. Read `<package>/references/dispatch.md`. Follow its `## Package resources` section for resource locations, load-once, and compaction. If supplied, use `references already loaded in this conversation, unchanged since load: <paths>` instead of reading those references again.
3. Resolve roles for the active harness with `<package>/scripts/role-profile.py` as specified in [Effective profiles](../../references/dispatch.md#effective-profiles). Complete [Capability preflight](../../references/dispatch.md#capability-preflight) before any edits, including the style gate.

## Coordination and authorization

As main agent, coordinate work and verify results. Use the bundled `worker` for edits, `style-reviewer` and `reviewer` for read-only inspection, and direct execution or the bundled `tool-runner` for checks. Follow Role resources in the dispatch reference for worker inputs. Let workers collect their own code context. Without worker confirmation that edits are visible in the repository of record, treat the batch as unverified.

Follow Round evidence in `closeout.md` for the dispositions ledger and Run for reports in the conversation. Group fixes by root cause across groups before partitioning by file, as specified in Run step 2.

Accept an optional scope, `--gate`, and `--no-commit`. Record commit authorization before starting:

- An explicit request to run this skill authorizes its scoped commit unless the user supplies `--no-commit`. Follow any separate, explicit instruction to leave the work uncommitted.
- Use `--no-commit` for automatic close-out. Implicit skill selection alone does not authorize a commit.
- Pass `--no-commit` from a nested workflow. The calling workflow owns its commit policy.

## Scope

1. Use the named plan, branch, commit, range, or paths. Without a named scope, use the current task's changed files, or uncommitted work if there is no active task. If the tree is clean and no change is identifiable, ask for scope and stop.
2. Inspect staged, unstaged, and untracked work with `git status --porcelain`. Preserve unrelated changes. Generate the scoped file list from that output by script, not by hand.
3. Resolve exact diff commands before dispatch. Keep them throughout review.

   | Scope | Diff |
   | --- | --- |
   | Uncommitted work | `git diff --find-renames HEAD -- <paths>` |
   | Single commit | Only the diff the commit introduced, with rename detection |
   | Branch | `git diff --find-renames <merge-base>..HEAD -- <paths>` with the resolved comparison branch |
   | Range | `git diff --find-renames <range> -- <paths>` with the supplied endpoints, not the worktree diff |

4. Inspect both paths for each rename and the deletion side for each removal. Read scoped untracked files separately.
   - On a branch with no commits, inspect staged additions and scoped untracked files.
   - Stop if you cannot resolve the repository, scope, or comparison. Report what is missing.
5. For a named committed change:
   - Record initial local diffs before editing. Use existing local edits and later commits only as context.
   - Give each inspector the original scoped diff and this run's edits as separate evidence. Identify the edits by comparing current and initial local diffs.
   - Check diff coverage for every scoped path. Read untracked paths and paths with no diff content separately.
   - Verify fixes against current files, not only the original commit diff.
   - Preserve unrelated edits. Stop and report overlap that prevents a safe scoped fix.

## Requirements and checks

1. Read the repository instructions. Identify requirements, plan constraints, and outstanding required gates. Follow Selection in `<package>/references/checks.md` for checks on the final scoped changes.
2. Delegate clearly owed in-scope implementation to `worker` before inspection, subject to `--gate` below. If a missing requirement needs a user decision, ask and stop.
3. Build the acceptance checklist from user requirements and plan artifacts. Keep each source and identifier. Assign stable identifiers in the conversation where missing. Keep the agreed meaning unless the user explicitly authorizes a change. Record if there are no criteria.

## Close-out procedure

Read `<package>/references/closeout.md` before close-out. Follow Inspection groups and gates for coverage, acceptance, and mutation checks. Follow Run for inspection, fixes, retries, pass limits, and final verification, including dispatch waves, reuse of validated manifests on replacement calls, and final-round skip conditions.

### If the user supplies `--gate`

Follow Gate in `closeout.md`: inspect first without a gate report, verify and present findings, then ask whether to fix them. Until approval, do not run the style gate or any project formatter, linter, or check. Do not edit, delegate fixes, or stage files. If the user declines, report and stop without changing the worktree or index or committing.

On approval, run the normal gate and worker fix loop, then continue from fix delegation as Gate specifies. Gate approval does not change commit authorization. Without `--gate`, run the normal gate and fix loop before initial inspection. In both cases, follow Pre-round style gate for the complete scoped file list and when to attach its verified report.

### Dispatch each round

Follow Run step 1 in `closeout.md` for manifest contents and the single-group no-manifest exception. Apply the dispatch prompt requirements in [Review-round manifests](../../references/dispatch.md#review-round-manifests) to every review dispatch, including inline rounds. Follow its cleanup rules when you use a manifest.

1. For a manifest round, after `review-manifest.py init`, diff `round_scope` against the generated scoped file list. Confirm that each staged or untracked task-owned path belongs to one group, subject to the exclusions in Inspection groups and gates. Then validate with `<package>/scripts/review-manifest.py` before dispatch.
2. If you cite the plan baseline, identify its digest as the `plan.py` normalized digest, verified with `plan.py validate`, not the file's `shasum`.
3. If a fix adds a path, follow Run step 2 for coordinator-only regeneration and validation before the next dispatch. Include the path in the round's checks and follow-up review. Leave unrelated paths untouched.

## Readiness

Before readiness or commit, require complete file coverage and acceptance verification under `closeout.md`. Report a gap that qualifies for the final-style exception in Run step 4 as a limitation. A final-round dispatch that cannot start or returns no result still blocks the run. Stop for unresolved or declined Critical or Major findings or unresolved or declined code-design findings from the final style pass.

Require passing available relevant checks on the final scoped changes. Follow Execution and Reuse within a run in `<package>/references/checks.md`. Report missing test tools as a limitation, not a pass. Missing required evidence blocks readiness and commit. Passing checks do not replace review or acceptance evidence.

## Commit and finish

A failed or incomplete close-out leaves the work uncommitted.

- If `--no-commit` applies, the invocation is automatic, or there is no authorization, leave the verified edits uncommitted.
- Otherwise, inspect the final task diff and index. Stop before staging if you cannot isolate the edits safely. Report the overlap. Stage only this run's scoped edits. Commit with a one-line English message and no `Co-Authored-By`. Verify the commit's paths and diff against the recorded scope. Confirm that unrelated staged work is intact.
- If there are no new scoped edits to commit, report that fact. Reviewing an existing commit does not authorize rewriting it. Never push.

Report:

- What changed.
- The checks and their results.
- Checked versus assigned file counts.
- The consolidated acceptance checklist with evidence.
- Remaining findings or blockers, with the root cause of each unresolved item.
- The verified commit hash or the reason the work remains uncommitted.
