# Plan format

The shared contract for implementation plans. `plan-new` owns this contract and
`skills/plan-new/scripts/plan.py` is its only parser and validator; `plan-do` and
`plan-revise` link here instead of restating these rules.

A plan is English Markdown saved at `plans/<slug>.md`, or
`plans/<ticket>/<slug>.md` when the request supplies a ticket.
`plans/backlog/` is a reserved unticketed location, never a ticket grouping.

## Completed plan archive

After every required step is proven `done`, `pending_revalidation` is empty,
and the single wrap-up close-out is clean, plan-do archives the completed plan
before its own scoped commit. Move `plans/<slug>.md` to
`plans/archive/YYYY-MM-DD-<slug>.md`, using the local completion date. Preserve
ticket grouping by moving `plans/<ticket>/<slug>.md` to
`plans/<ticket>/archive/YYYY-MM-DD-<slug>.md`. A plan under the reserved
unticketed `plans/backlog/` location archives to `plans/archive/` like a root
plan; `plans/backlog/archive/` is never a valid archive destination. Keep the
plan body unchanged, including criteria, decisions, progress and history;
update current pointers without rewriting historical evidence. Include both
move paths in the scoped commit.

Under `--no-commit`, leave the plan at its active path. Draft, blocked or
incomplete plans and standalone plan-revise runs never archive. An archived
plan is terminal evidence, excluded from active selection and execution;
explicit `validate` and `inspect` remain available for historical inspection.

Keep run state under its original identity and key. Before moving, retain the
original identity, plan digest and content SHA-256. After moving, verify the
same digest and bytes and persist a passing archive verification check through
the existing `close` request's `checks` metadata, alongside the actual wrap-up
checks. The check entry's `archive` object records repository-relative
`original_path`, `archive_path`, `plan_digest` and `sha256`. Store final hashes for the archived
path and other scoped files; preserve earlier checkpoints and evidence at
their original paths. This receipt is terminal evidence, never permission to
execute under an archived identity. Do not rekey, recreate or migrate state,
change `plan_realpath`, or bypass identity checks. Inspect the receipt using
the original identity; a subsequent HEAD change still requires normal explicit
reconciliation, without making the archive an active plan.

Never overwrite an archive destination. If it already exists, resume only when
the original state's archive receipt proves this exact original/archive path
pair, digest and SHA-256, the source is absent, and the target matches both
recorded hashes. Otherwise stop with the collision. A failed move, verification
or state receipt blocks readiness and commit; preserve both evidence and files
for recovery. Before readiness, persist the final receipt using the original
identity, active owner and current revision. If interrupted after the move but
before a receipt, report the incomplete archive rather than guessing identity
or treating target existence as success.

### Terminal archive recovery

Before active-plan validation, an explicit missing original path or archived
path enters this terminal preflight. An archive path identifies a candidate
original only through the exact dated layout above; confirm that mapping from
the existing original state's receipt. Load `show` using the original
`plan_realpath` and state key, never the archive as identity. Missing state or
receipt, an invalid path pair, a remaining source or a mismatched target blocks
recovery; never `init`, rekey, migrate or invent an identity to continue.

Require the receipt's exact original/archive paths, digest and SHA-256 to match
the target, every required state step `done`, `pending_revalidation` empty,
and `close_out.status` `ready` with complete passing wrap-up and archive checks.
Verify all final file hashes. Run `plan.py validate` and `plan.py inspect` on
the archived body solely as terminal evidence: compare its digest, completed
steps, acceptance identifiers and recorded criterion text with the existing
accepted baseline and matching clean review. Those parser commands do not
change state identity. Missing or stale acceptance/review evidence blocks
terminal recovery rather than starting review or execution again.

Keep the normal identity, branch, HEAD, ownership and revision gates. Before
any mutation, continue the active owner or claim released ownership; takeover
requires ended-run evidence. Use the latest `expected_revision`. On
`head_changed`, use the shared run-state HEAD procedure to inspect the original
stored identity. A read-only already-committed report requires Git proof of a
reachable scoped commit after the stored HEAD whose parent is that HEAD, whose
paths exactly match the recorded scope, whose original plan is deleted and
archive blob matches the receipt, and whose scoped blobs/modes match the
verified final files. Report the hash and any outstanding state HEAD mismatch
without mutating state. Mere target existence is not commit proof. Otherwise
follow normal explicit HEAD reconciliation before mutations; any resulting
revalidation obligation blocks this terminal route.

Resume only unfinished terminal receipt/commit work with the original state
and existing scoped commit authorization. Do not fall through into active
validation, initialization, acceptance, review, step dispatch or another
wrap-up. Under `--no-commit`, report verified terminal evidence without staging
or committing; do not undo the archive. Invalid receipts, collisions or
unresolved obligations block the commit and require recovery, not active
execution of the archived plan.

`plan.py` and `run_state.py` refuse OpenSpec projects — a repository whose
root contains an `openspec/` directory — with the `openspec_project`
diagnostic; use the native OpenSpec workflow or `<package>/skills/spec-flow/SKILL.md` there.

## Sections

A plan carries these `##` sections, in this order:

1. `Context` — repository facts the steps rely on; anchors are allowed here.
2. `Summary` — one row per file changed.
3. `Steps` — the numbered steps (grammar below).
4. `Acceptance` — `- A<n>. <criterion>` bullets with stable `A1`, `A2`, … identifiers.
5. `Decisions` — plan-level decisions already made.
6. `Out of scope` — what the plan does not touch.
7. `Open questions` — questions not yet answered.
8. `Verification` — the commands that prove the plan as a whole.

`Context`, `Summary`, `Steps`, `Acceptance`, and `Verification` are required.
`Decisions`, `Out of scope`, and `Open questions` are optional: omit them when
they would carry no content, since an absent section reads as empty; when
present they keep the semantics below. The order above is enforced over the
sections present — a deviation fails validation with `section_order`.

Fenced code blocks are examples: their contents are never parsed as sections,
steps, paths, or anchors.

### Migration from the removed Checklist section

Plans written under the earlier contract carried a `## Checklist` section
duplicating every step ID and title, with progress ticks living there. That
section is removed: each step heading now carries its own checkbox and the
tick records progress on the heading. A plan still containing a `## Checklist`
section fails validation with `obsolete_section`; to migrate, delete the
section and move each tick onto the matching step heading.

## Steps

Each step is one independently verifiable behavior:

```markdown
1. [ ] Add flag call — one-line title.
   depends on: none
   acceptance: A1
   creates: src/extra.py
   - Contract prose naming repository-relative paths such as `src/app.py`.
   - An exact source anchor, rests on `src/app.py:1` "def greet():".
   done when: greet calls flag
   test: python3 -m unittest tests.test_app -v
```

- Every step heading carries a checkbox: `N. [ ] <title>` while the step is
  open, `N. [x] <title>` once it is done. The checkbox is required — a bare
  `N. <title>` heading fails validation with `missing_step_checkbox` — and
  its state is progress only: `inspect` reports it as the step's `done` flag
  and the digest ignores it.
- Step IDs are consecutive integers starting at 1. Amend IDs in place and append
  new ones; this release has no step-removal or supersession grammar.
- `depends on: <IDs|none>` — comma-separated step IDs. Cycles, self-references,
  unknown IDs, and non-numeric values are rejected before execution.
- `acceptance: <A-IDs>` — comma-separated criteria this step delivers; every
  named ID must exist in `## Acceptance` and every step names at least one.
  Every criterion listed in `## Acceptance` must be named by at least one step;
  otherwise the reviewer raises a coverage gap. An amendment that adds a
  criterion must also name the step that delivers it.
- `creates: <paths>` — optional comma-separated files the step brings into
  existence. A backticked path that does not exist on disk is valid only when
  some step declares it as a creation.
- `done when: <observable result>` and `test: <command|assertion>` are required.
  `test: none -- <reason>` (em dash accepted) records an explicit exemption.
  When the deliverable is contract prose, pin short distinctive phrases plus placement
  in the `test:` greps rather than full sentences: a full-sentence pin makes every later
  style finding on that wording a plan amendment, while a phrase pin keeps the
  verification intent and leaves the wording fixable within the agreed meaning.
  A phrase pinned by a line-based grep must sit contiguous on one source line,
  with the pin length chosen to fit, so prose wrapping never splits a pinned phrase.
- Contract bullets are free prose read by the worker; everything a worker
  needs is in its step or the common content before `## Steps`.

## Prose

New plans and new amendments use ASD-STE100 for these parts: step titles,
contract bullets, `done when`, and the prose in `Context`, `Acceptance`,
`Decisions`, `Out of scope` and `Open questions`. Existing and archived plans
are not rewritten.

The limits are:

- A procedural sentence has 20 words or fewer. A sentence is procedural if
  it tells the reader to do something; all other sentences are descriptive.
- A descriptive sentence has 25 words or fewer.
- Use one word for one meaning.
- Write a condition as an explicit `if` clause.
- Do not use a stand-in verb such as "it does".

A sentence ends at `.`, `?` or `!`, or at the end of a list item, table cell
or heading. A semicolon or a colon does not end a sentence.

A backticked span counts as one word. An anchor with its quoted line counts
as one word.

The wording rules do not apply to anchors and their quoted lines,
backticked spans, paths, `test:` commands and fenced code:

- `plan.py validate` checks anchors and path spans exactly, so their wording
  is fixed.
- A `test:` command runs as written, so its wording is fixed.
- Fenced code is example text.

This exclusion does not remove these parts from the word count. An anchor
with its quoted line, a backticked span and a path each count as one word
in the sentence that holds them. A `test:` command, fenced code and a link
target each count as zero words.

## Paths and anchors

- File references are repository-relative paths in backticks. Absolute paths
  (`/…`, `~/…`), escaping paths (`../…`), and symlinks that resolve outside the
  repository are rejected.
- Every backticked span containing a `/` and no whitespace is path-checked:
  it must exist on disk or appear verbatim in some step's `creates:` (exact
  string — `plugins/x` and `plugins/x/` are different tokens). Environment
  expressions such as `$AGENTS_HOME/metrics/usage.jsonl` and home paths such
  as `~/.agents` are not repository paths: name them in prose without
  backticks.
- An anchor pins a claim to source:
  `` `path:line` "exact quoted line" `` or
  `` `path:line` 'exact quoted line' ``. Use the single-quoted form when the
  source line itself contains a double quote. The quote must equal the full
  stripped source line. Anchor checks accept the quote having moved to another
  line; a quote that no longer appears is drift (`anchor_missing`), one that
  matches several lines is `anchor_ambiguous`, and a missing file is
  `anchor_source_missing`. Quotations are never rewritten by similarity.
- An anchor pins a claim about code the step does not change. A step that
  rewrites a line anchors a neighbouring line it leaves alone; a step that must
  rewrite the anchored line drops the anchor and states the claim without one.
- A line that contains both `"` and `'` cannot be anchored. Anchor a
  neighbouring line, or state the claim without an anchor. A cut quote reports
  `anchor_unparsable`.

## Open questions

A question is a bullet under `## Open questions`. Mark it `- [resolved] …` once
answered. An unresolved question carrying a `step <n>` reference blocks that
step (and only that step) until resolved.

## Digest and review

`plan_digest` is SHA-256 over the plan text normalized in exactly two ways:
checkbox state — a step heading or a bullet checkbox, `[x]` reads as `[ ]` —
and anchor line numbers. Every other byte counts, so rewording a requirement,
a dependency, a path, or a test invalidates review, while ticking a step
heading or moving an anchor does not.

## Run state

Durable execution state, identity, operations and recovery are defined in
[run-state.md](run-state.md). Load the sections selected by the calling workflow.

## CLI

`plan.py` exposes read-only commands, printing JSON on stdout; exit 0 is
success, 1 is invalid or blocked input, 2 is usage or I/O failure:

- `validate <plan> [--steps <IDs>]` — parse and validate; diagnostics carry stable codes. When `--steps` is given, the unresolved-open-question check is scoped to those step IDs, exactly as `anchors --steps` scopes the anchor check.
- `inspect <plan>` — validation plus steps in dependency order, each with its
  `done` tick state, and the digest; the payload carries no checklist key.
- `select [directory]` — the sole `plans/` candidate, or the sorted candidate
  list when zero or several exist; excludes archive directories at both root
  and ticket levels and never chooses the newest silently.
- `anchors <plan> [--steps <IDs>]` — exact-anchor checks for the given steps
  (all steps when omitted) plus the `Context` section.
- `preflight <plan> [--steps <IDs>]` — the entry check combining the OpenSpec
  refusal, `validate`, and `anchors` in one payload under `stages`, in that
  order; a failing stage skips the remaining stages.

Validation runs before any execution; a plan with diagnostics or anchor drift
is not executable.

`plan.py` and `skills/spec-list/scripts/spec-list.py` load the shared
`<package>/scripts/repository.py` helper. Install the complete package and
preserve skill symlinks; copying an individual skill directory does not
provide its package-level dependencies. See
[package resources](../../../references/dispatch.md#package-resources).
