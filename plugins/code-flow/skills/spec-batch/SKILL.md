---
name: spec-batch
description: Run several explicitly named OpenSpec changes sequentially through spec-flow, with preflight for the whole batch and a stop at the first unsuccessful change. Use for a batch of full implementation workflows.
argument-hint: "<change-or-tasks-path>... [--no-commit] [--archive]"
---

# Run OpenSpec changes in order

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first. Read `<package>/references/dispatch.md` at that root and apply its `## Package resources` section for resource locations.

Accept one or more change names or paths to their `tasks.md`, plus `--no-commit` and `--archive`. Reject other flags and require explicit changes; preserve the supplied order.

Read `<package>/skills/spec-flow/SKILL.md`. It owns preflight, apply, verification, commits, archive, and each change's outcome. Stop if it is missing.

## Preflight the whole batch

First run `<package>/skills/spec-flow/scripts/spec-preflight.py --batch-arguments` with `python3`, passing a JSON object on stdin containing `changes` (the supplied names or paths in order, as a nonempty array of nonempty strings), `no_commit` (boolean), and `archive` (boolean). This argument-only mode runs before any per-change preflight or apply and does not inspect or modify the project. Exit 0 returns those fields plus `archive_skipped`; exit 2 returns a JSON `error: invalid_batch_arguments` and message for invalid input. Exit 1 with `error: unsupported_batch_no_commit` rejects multiple changes with `--no-commit`: report the reason once and stop before per-change preflight, suggesting separate single-change invocations. Never override `--no-commit` or start a partial batch. For a supported single change with both flags, report `archive_skipped: true` and leave archive skipped as spec-flow requires.

Before implementing any change, run spec-flow's preflight alone for every argument, collecting failures rather than stopping at the first. Deduplicate project-wide failures. Reject arguments resolving to the same change and any pair sharing a capability path; compare complete relative paths, including nested capabilities. Report all failures together and stop with nothing applied. Every argument must resolve successfully before the batch runs.

## Run sequentially

1. Announce `batch: <i>/<total> <name> — running`, then follow `<package>/skills/spec-flow/SKILL.md` for that change. Pass `--archive` as supplied. Forward `--no-commit` to spec-flow unchanged for a supported single change; there it means "leave the verified edits uncommitted" and skips archive even when requested.
2. Wait for the flow's final outcome. A question pauses this change and the batch; the user's answer resumes it before any later change starts. Keep each flow's outcome and commit hashes in the conversation.
3. Advance only after `committed` for the expected change. A `ready` result is successful only for a single-change invocation under `--no-commit`. Any other result, missing or mismatched outcome, failed archive, or blocker halts the batch; mark all later changes `not run`.
4. After compaction or interruption, reconcile the conversation's last outcome with the current branch, worktree, commits, and OpenSpec task/archive state. Continue an unfinished flow rather than starting it again. If completion cannot be established, stop and report the uncertainty; never infer success from task checkboxes alone.

End with one line per requested change in order: committed with hashes, ready and uncommitted, halted with reason, or not run. For a halted batch, give a rerun command containing the halted and not-run changes with the original flags, noting any partial commit or archive that needs reconciliation first. List next archive steps only for committed changes that remain active. Stay on the current branch and never push.
