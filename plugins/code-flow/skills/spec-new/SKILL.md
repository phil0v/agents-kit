---
name: spec-new
description: Create one new OpenSpec change through explore, propose, strict validation, and independent artifact review. Planning artifacts only, never code edits, no commits. Use for requests to propose or start an OpenSpec change; implementation belongs to spec-flow.
argument-hint: "[<change-request>]"
---

# Propose one new OpenSpec change

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first. Read `<package>/references/dispatch.md` at that root and apply its `## Package resources` section for resource locations.

Resolve every dispatched role through `<package>/scripts/role-profile.py` for the active harness, following `<package>/references/dispatch.md`; its effective profile owns model, effort policy, tools, and canonical instructions. Load each reference once per conversation under the dispatch reference's load-once rule; after a compaction resume, reload exactly the references this skill lists.

## Trigger and boundary

This skill fires on explicit requests to propose, start, or create one new OpenSpec change, in projects with an OpenSpec root — where `plan-new` refuses OpenSpec projects. It does not implement code, commit, archive, or start any execution workflow. Writes are confined to the new change directory under the project's `openspec/changes/` tree: planning artifacts only. Implementing an existing change belongs to `<package>/skills/spec-flow/SKILL.md`.

## Preflight

Finish these checks before writing artifacts.

- Run `openspec context --json` from the repository working directory. No OpenSpec root in the result is a blocker: report it and stop.
- Locate the project's generated `openspec-propose` skill in the project's configured skill locations or available project skills catalog. Missing integration stops the flow before artifact writes. Report the missing skill and the installed CLI's setup guidance: `openspec config profile` to enable the workflow, then `openspec update`.
- Require the bundled spec-reviewer resource listed by the dispatch reference, `<package>/roles/spec-reviewer.toml`; complete the dispatch reference's delegation preflight for it.
- Derive a kebab-case change name from the request. A name colliding with an active change asks the user.
- Record the starting branch, HEAD, staged, unstaged, and untracked work. Preserve unrelated edits and the index. Stay on the current branch.

## Explore

Before the change is created, read the repository instructions, the context field `openspec context --json` reports per the generated `openspec-propose` skill's rules, the main specs of the capabilities the request touches, relevant archived changes, and the code the request touches. Verify every claim about existing behavior against that code. Material ambiguity affecting scope, behavior, compatibility, or acceptance criteria is batched into one user message and resolved before the change is created.

## Propose

Follow the generated `openspec-propose` skill exactly — scaffold with `openspec new change "<name>"`, read `openspec status --change <name> --json` for the build order, and write each artifact from `openspec instructions <artifact-id> --change "<name>" --json` in dependency order: proposal, spec deltas per capability, design, then tasks under the default schema. Never reconstruct artifacts by hand; the generated skill owns artifact production.

## Validation

Run `openspec validate <name> --strict` before every review round and after every artifact correction, keeping the command and its output as evidence. A failed validation is corrected in the artifacts and re-run before any dispatch.

## Review

Dispatch one fresh independent agent on the spec-reviewer profile per round, per `<package>/references/dispatch.md`. The dispatch carries the canonical instructions by reference — the absolute path of `<package>/roles/spec-reviewer.toml` plus the SHA-256 of its current content — the absolute package root, the repository of record, the user's request and material decisions, the change directory, and the schema provenance naming the workflow schema and artifact instructions consumed.

Findings stay inline. Resolve them by editing the artifacts, then rerun strict validation and dispatch a follow-up covering the changed artifacts, plus whole-change coverage when a requirement changed.

Allow at most two correction rounds. When the limit is exhausted with converging findings — the latest round introduced no new finding category — present the remaining findings and a proposed ruling for each to the user; explicit approval authorizes one further correction round at a time, repeatable per approval while findings keep converging. Otherwise unresolved findings leave the change an explicitly unready draft with no clean receipt. Only the exact response `verdict: clean` from a complete review closes the round clean.

## Finish

End with one outcome: `proposed: <name> — clean`, `proposed: <name> — findings remaining`, or `halted: <name> — <reason>`. Link the created artifacts by repository-relative path, summarize material decisions, remaining findings, and validation evidence, and state that implementation has not started and no commit was made. Approval gate: only user approval of the proposed change starts the next step — `<package>/skills/spec-flow/SKILL.md` with the change name — never this skill.
