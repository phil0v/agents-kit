---
name: spec-flow
description: Implement one existing OpenSpec change, verify it, run wrap-up, and commit it, optionally archiving afterward. Use for requests to run a change through its full implementation workflow.
argument-hint: "[<change-or-tasks-path>] [--no-commit] [--archive]"
---

# Run one OpenSpec change

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first.

Read `<package>/references/dispatch.md` at that root. Use its `## Package resources` section to locate resources.

Follow the load-once and compaction rules in that section. Resolve each role for the active harness with `<package>/scripts/role-profile.py` as specified in [Effective profiles](../../references/dispatch.md#effective-profiles).

## Select the change

Accept one change name or its `openspec/changes/<name>/tasks.md` path. Accept only `--no-commit` and `--archive` as flags. If no change is given, list active non-draft changes. Select the only eligible change or ask which to run. A batch always names its change.

## Preflight

Complete preflight before edits. For a batch preflight-only request, apply nothing. Return the resolved name, capability paths, and any failures.

### Run the preflight script

From the target repository's working directory, run `<package>/skills/spec-flow/scripts/spec-preflight.py` with `python3` and the change name or `tasks.md` path. With no argument, the script selects the only eligible change.

The script finds the repository root and the change in `openspec/changes/`. It requires `tasks.md` and excludes `archive/`, drafts, archived changes, and symlinks that lead outside `openspec/changes/`. It runs strict validation and the apply instructions from the root. Stop and show the evidence for a failed command, blocked apply state, unreadable result, or zero tasks.

| Exit code | Action |
| --- | --- |
| `0` | Use the resolved change. Keep the `change: <name> — <relpath>`, `validate: [<level>] …`, `tasks: <done>/<total> done`, and any `missing: <path>` lines. Keep validation warnings visible. |
| `1` | Stop. Show the script's stderr lines, with the `stop:` line last. |
| `2` | Report a usage error. |
| `3` | List the eligible changes and ask which to run. |
| `4` | Report that no open change exists. |

### Check integration and record the baseline

1. Read the project instructions. Find and read the generated `openspec-apply-change` and `openspec-verify-change` skills in the project's configured locations or skill catalog. With `--archive`, also require `openspec-archive-change`.
2. Require `<package>/skills/wrap-up/SKILL.md` and the bundled worker, reviewer, style-reviewer, plan-reviewer, and tool-runner. Follow Capability preflight and Role resources in the dispatch reference.
3. If an integration is missing, stop before artifact writes. Report the missing skill and the installed CLI's setup guidance: `openspec init --tools codex` for Codex; `openspec config profile` to enable missing workflows, then `openspec update`. Follow the generated skills and project schema. Do not reconstruct artifacts by hand.
4. Record the starting branch, HEAD, and staged, unstaged, and untracked work. Stay on the current branch. Preserve unrelated edits and the index. Stop for overlap that prevents safe separation. Capability paths are the relative directories below the change's `specs/` with `spec.md`, including nested directories.
5. Before implementation, read and record the agreed requirements, acceptance criteria, and scenario meanings from the user's request and change artifacts. Keep this baseline through verification, wrap-up, and archive. Correct structure or clarify wording without changing meaning. Do not delete or weaken requirements or scenarios or reduce their coverage to pass implementation or verification. Change meaning only with explicit user authorization and an updated baseline. Otherwise, report the mismatch as unresolved.

## Apply and verify

### 1. Apply the change

Follow `openspec-apply-change` for the selected change. If all tasks are complete, skip implementation and continue to verification.

1. Use one `worker` per independent workstream, not per task.
2. Just before dispatch, read the producing code once: the entities, serializer groups, and DTO field shapes that consumers use. Pin each contract's field names, types, nullability, and id kinds from the code in each consumer's prompt.
3. Dispatch workstreams whose mutual dependencies are fully pinned in one wave, not in dependency order. If existing code cannot pin a dependency, run the producer first. With isolated workers, new code a consumer must compile against (such as an import or type) is never fully pinned: the consumer does not have it until merge.
4. Pass each task group and the acceptance baseline. Follow the exact-check-argv rule in Role resources in `<package>/references/dispatch.md`. In each worker's validation argv, include task-named checks and the formatter and linter checks that close-out and review enforce on the worker's changed files.
5. After rate limiting, retain successful results. Retry only failed or incomplete workstreams. For a wrong pin, stop the affected workstream, report the contract correction, and derive the corrected pin from the code before resuming.
6. Apply artifact edits within the change directory as the schema directs. Tick only completed tasks. Pause for questions or blockers. Resume the same change after the answer.

### 2. Run wrap-up with initial verification

Follow `<package>/skills/wrap-up/SKILL.md` with `--no-commit`. Scope it to this change's implementation and artifact edits. Include pre-existing edits only if they belong to the change and ownership is clear. Use unrelated work only as context.

- Give wrap-up the caller verification duty and agreed acceptance baseline. Use its fused initial round for verification as specified in Inspection groups and gates in `<package>/references/closeout.md`. If wrap-up edits code after Round 1, attach that duty to its post-fix verification round. Do not verify separately before wrap-up.
- List the loaded references in the dispatch prompt: `references already loaded in this conversation, unchanged since load: <paths>`. Follow the inheritance rule in Package resources in the dispatch reference.
- Follow Run step 1 in `<package>/references/closeout.md` for review dispatch waves and rate-limit fallback.

Before continuing, require complete file coverage, a complete acceptance checklist with all affected criteria met against the agreed OpenSpec baseline (including post-fix verification if wrap-up edited code after Round 1), and passing available checks. A final style gap that wrap-up reports as a limitation does not make coverage incomplete. Stop for any unresolved or declined Critical or Major finding or unresolved or declined code-design finding from the final style pass.

### 3. Rerun verification only if needed

Skip the standalone `openspec-verify-change` rerun if `wrap-up` left the active change directory under `openspec/` untouched after Round 1 and completed post-fix verification with all affected criteria met against the agreed OpenSpec baseline. Rerun standalone `openspec-verify-change` against that baseline only if `wrap-up` edited any file in the change directory after Round 1 or left an OpenSpec artifact check unverified. Check the implementation against the change's artifacts. Do not repeat a completed review or verification pass if its scope and inputs have not changed.

1. If the rerun reports a critical issue (a blocking result), do one correction round. Resolve supported code or artifact issues within scope. Use the project schema. If a requirement needs a user decision, pause.
2. If that round changes files, run the linters and tests on the changed files before you rerun verification. Run the style gate as specified in Pre-round style gate in `<package>/references/closeout.md`. Use the files that the correction round changed as its file list. Write them as a JSON array file outside the worktree. Put reports, logs, and `TMPDIR` in wrap-up's run artifacts directory if it still exists. If it does not exist, use a new `mktemp -d` directory outside the worktree. Select, run, and reuse checks as specified in Selection, Execution, and Reuse within a run in `<package>/references/checks.md`. Fix failures through `worker` in the same correction round. If a failure cannot be fixed in scope, stop. Do not start a new review round.
3. After that round, rerun verification.
4. If a blocking result remains or appears after that round, or verification is incomplete, stop. Do not start another correction round. Report warnings and suggestions explicitly.

### 4. Check task completion and artifacts

Read apply progress again and rerun strict validation. Require valid artifacts, complete final verification, and all tasks complete except explicit post-deploy or manual operations (for example, production data repair). Report these as pending manual steps. Tick them only on explicit user confirmation. Report missing test tools as a limitation, not a pass.

## Commit and archive

An explicit user request to run this workflow authorizes commits for the selected change only. A user-invoked batch can carry this authorization. Do not commit with `--no-commit`. Pass it for automatic close-out. Implicit skill selection does not authorize a commit. Never push.

### Commit the implementation

If `--no-commit` is given, leave the verified edits uncommitted and report `ready: <name> — not committed`. If `--archive` is also given, skip archive and say so. Do not run the commit steps below.

Otherwise, run these steps only with commit authorization:

1. Inspect the final diff. Stop before staging if you cannot isolate the change safely. Stage only this change's edits. Preserve unrelated staged work.
2. If there is nothing to commit, report `halted: <name> — nothing to commit`. Otherwise, commit with a one-line English message. Verify the contents and record the hash.

### Archive only if requested

Archive only with `--archive` and an implementation commit from this run.

1. Run `<package>/skills/spec-flow/scripts/spec-preflight.py` with `--archive-verdict` and the change name from the repository root.
2. Use the verdict:
   - `archive: stop`: Stop the archive.
   - `archive: ask — <reasons>`: Ask the user once whether to archive regardless. If they decline, skip the archive.
   - `archive: clean`: Proceed.
3. If you proceed, let the installed `openspec-archive-change` skill perform archive operations. Follow its spec synchronization and confirmation rules.
4. Run relevant checks and strict validation over the resulting main specs. Commit only archive and spec-sync edits in a separate commit. On failure, stop, preserve the implementation commit, and report the archive state.

## Report the outcome

End with one outcome:

- `committed: <name> — <hash>` (append the archive hash when applicable)
- `ready: <name> — not committed`
- `halted: <name> — <reason>`

Include checks, verification findings, and limitations. If a committed change still needs archive and the archive skill is available, give `$openspec-archive-change <name>` as the next step. Keep the outcome in the conversation for batch callers.
