---
name: spec-list
description: List active OpenSpec changes grouped by progress and draft status, and manage a named change's metadata through the set-metadata script arguments --priority P0|P1|P2|P3, --created YYYY-MM-DD, and --draft true|false. Use for an inventory of changes or for setting metadata on an active change.
argument-hint: "[<change> --priority P0|P1|P2|P3] [--created YYYY-MM-DD] [--draft true|false] [--full] [--short] [--batch]"
---

# List active OpenSpec changes and set change metadata

## Package resources

Resolve the package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first. Keep that root separate from the target repository and its working directory. Resolve `<package>/` paths in this skill against that root.

## Listing mode

Run `<package>/skills/spec-list/scripts/spec-list.py` with `python3` from the target repository's working directory. Run the script as the only command of its terminal call, so the tool output contains nothing but the script's own output. Without arguments the script prints one line per change with the description cut to a short summary; pass `--full` when the request asks for the detailed view, and the script prints one indented description block per change; pass `--short` when the request asks for a bare name-only list, and the script prints one name per line under the group headings; pass `--batch` when the request prepares a spec-batch run, and the script prints the unfinished change names as one space-separated line per batch — a single line when one spec-batch run can take them all, split wherever two changes share a capability path so every line is a valid batch. The script produces the whole inventory; on a nonzero exit its stderr "spec-list: …" line is the reply, quoted as the blocker. Reply with the output alone, verbatim, inside one fenced code block. No other tool calls, and no text before or after the block.

These output requirements apply only to listing requests; a metadata-setting request gets the concise result defined below instead.

## Metadata-setting mode

For a request to set or change metadata on an OpenSpec change, identify the named active change and the requested values from the request. If the change name or all metadata values are missing, ask for the missing information before doing anything else.

Metadata belongs to the `.openspec.yaml` file directly under the selected active change directory. Never edit that file directly; the script performs the write.

Accepted fields and values:

- `--priority P0|P1|P2|P3` — the change's `x-priority`.
- `--created YYYY-MM-DD` — the change's `created` date, strict four-digit year and two-digit month and day.
- `--draft true|false` — the change's `x-draft` flag.

Run `<package>/skills/spec-list/scripts/spec-list.py set-metadata <change> [--priority …] [--created …] [--draft …]` with `python3` from the target repository's working directory, passing exactly the values the user requested. On exit 0, reply with the script's success output: `Updated metadata for <change>.` On a nonzero exit, reply with the script's stderr "spec-list: …" line, quoted as the blocker.

Never implement, commit, or archive.
