# Cross-review skill for code-flow
2026-11-04 · backlog

## Context

The Claude Code configuration ships `/stage cross` — a review pass by models other than the session's — and the portable package has no counterpart: `wrap-up --gate` reviews only with the bundled roles' configured models.

The dispatch reference bundles the reviewer role — independent correctness and acceptance review — and its preflight owns per-call model selection: a required model the harness cannot select is a blocker, never silently dropped.

Both `plugin.json` and `package.json` use autodiscovery, so a new package skill needs no manifest edit.

## Summary

| File | Change |
|------|--------|
| `plugins/code-flow/skills/cross-review/SKILL.md` | New skill: a scope plus a named model list dispatches one bundled reviewer per model over the scoped diff and reports findings per model without editing anything |
| `plugins/code-flow/tests/test_workflow_config.py` | Pins the flag contract: `--models` required, other flags rejected, no-edit rule |
| `plugins/code-flow/tests/test_package.py` | Adds `"cross-review"` to the exposed-workflow set the package pins |

## Steps

1. [ ] Write the cross-review skill
   depends on: none
   acceptance: A1, A2
   creates: plugins/code-flow/skills/cross-review/SKILL.md
   - Fires on explicit requests to cross-review a scope with named models; not for close-out, fixes, or plain review without models — those belong to `wrap-up` and `wrap-up --gate`.
   - Accepts an optional scope resolved exactly as `plugins/code-flow/skills/wrap-up/SKILL.md` resolves it, plus `--models=<m1>,<m2>,…` with at least one model; rejects every other flag.
   - Dispatches one bundled reviewer per named model over the scoped diff, following `plugins/code-flow/references/dispatch.md` preflight and model selection; a model the harness cannot select is a blocker, reported, never silently dropped.
   - Carries the tree guard defined in `plugins/code-flow/references/closeout.md` ("Inspection groups and gates"); on a difference it stops the run with the change named.
   - Returns each model's findings — severity, file, line, tag, verdict — grouped by model; edits nothing, commits nothing; disposition belongs to the caller.
   done when: the skill file declares name, description, triggers, boundaries, and the contract above
   test: none — the exposed-workflow pin lands in step 2, the flag-contract and no-edit pins in step 3
2. [ ] Pin the skill in the exposed-workflow set
   depends on: 1
   acceptance: A3
   - `"cross-review"` joins the exposed-workflow set pinned in `plugins/code-flow/tests/test_package.py`, whose `test_only_requested_workflows_are_exposed` assertion lists the package skills exactly.
   done when: `plugins/code-flow/tests/test_package.py` lists `cross-review` in its exposed-workflow set and the `code-flow` suite is green
   test: python3 -m unittest discover -s tests (from `plugins/code-flow`)
3. [ ] Pin the contract in tests
   depends on: 2
   acceptance: A2
   creates: plugins/code-flow/tests/test_workflow_config.py
   - `plugins/code-flow/tests/test_workflow_config.py` gains assertions: `--models` with at least one model accepted, other flags rejected, the no-edit rule stated.
   done when: the code-flow suite is green with the new assertions
   test: python3 -m unittest discover -s tests (from `plugins/code-flow`)

## Acceptance

- A1. A request naming a scope and at least one model dispatches one bundled reviewer per named model over the scoped diff and returns that model's findings.
- A2. The skill edits nothing and commits nothing; its output is findings only.
- A3. `plugins/code-flow/tests/test_package.py` lists `cross-review` in its exposed-workflow set, which pins the workflow skills the package exposes.

## Out of scope

- Fixes, close-out, and commit policy — `wrap-up` owns them; cross-review only reports.
- The `tidy` and standalone `review` stages of the Claude Code configuration.

## Verification

- python3 -m unittest discover -s tests (repository root)
- python3 -m unittest discover -s tests (from `plugins/code-flow`)
