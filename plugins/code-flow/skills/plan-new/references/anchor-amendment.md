# Automatic anchor-amendment contract

Load only for an automatic anchor amendment inside an authorized plan-do run,
including resume. The callers retain their ordinary ownership, authorization,
revision, validation, independent-review and current-verification gates.
Standalone plan-revise requests use its normal amendment workflow.

## Eligibility and provenance

Require no running step, a reviewed accepted plan matching the displayed digest,
and empty `pending_revalidation`. Resolve HEAD changes and current verification
first. Establish provenance for every original anchor diagnostic by inspecting
all of:

- the completed-step contract and handback;
- its verified changed-file set and relevant scoped diff;
- recorded checkpoint hashes and current source;
- the exact original quotation at the verified source revision or an available
  recorded checkpoint.

The quotation's deliberate removal or replacement must be attributable to that
run's own verified completed step and accepted contract. A matching path or
completion checkbox alone proves nothing. If the original source cannot be
recovered from the verified revision or checkpoint, provenance is unavailable.

Unexplained external edits even on the same path, ambiguous anchors, missing
provenance, or changed remaining-step prerequisites, dependencies, expected
behavior or accepted criteria block automatic routing. Inspect or request the
necessary user decision. Never guess a quotation by similarity or treat changed
requirements as evidence-only drift. Unresolved revalidation, ownership conflicts,
stale revisions and failed validation/review also block dispatch under the
existing recovery rules. Moved unique exact matches already pass anchor checks
and need no amendment.

## Handoff

Only after proving eligibility, plan-do loads and follows
`<package>/skills/plan-revise/SKILL.md` and its selected references. Pass:

- original diagnostics and affected remaining step IDs, including steps relying
  on an obsolete Context claim;
- completed-step provenance with source revision/checkpoint evidence;
- unchanged accepted criterion identifiers and exact text;
- current identity, `owner`, latest `expected_revision` and `prior_digest`;
- the outer run's existing commit authorization, including `--no-commit`.

Continue the same owner token; never claim or release a nested session. This
proven evidence-only route requires no repeated user confirmation. Plan-revise
rechecks the supplied proof against current source before delegating rewriting.

## Rewrite and independent review

Limit the delegated rewrite to obsolete anchors and their factual explanation.
Preserve completed step contracts, evidence, IDs, dependencies and history, plus
all remaining prerequisites, behavior and acceptance meaning. Under `amend`,
restrict `affected_steps` and `invalidated` to unfinished work and close-out; a
Context-only repair may have no affected step IDs. Never reset a completed step
to repair an obsolete Context reference.

Make obsolete Context claims explicit historical evidence pinned to the verified
source revision or available recorded checkpoint, without asserting them as
current. Anchor current claims only to exact current source supporting the same
claim; a replacement anchor must not give an old claim new semantics.

Reuse plan-revise's revision-checked `amend`, delegated rewrite, bounded independent
review and plan-new's shared [clean-receipt procedure](../SKILL.md#review-receipt).
Unchanged criteria use the immediate amendment path without repeated confirmation;
changed requirements follow the existing user-authorization gate. A changed digest
invalidates the old receipt. Do not edit the plan directly, introduce state
commands or bypass review to repair drift.

## Resumption

Plan-revise returns the new digest, current revision, clean receipt,
validation/anchor results and preservation evidence. Plan-do alone resumes
implementation. Before another `start`, require:

- successful validation and anchor checks for Context and every remaining step;
- complete finding-free independent review and a clean receipt for the new digest;
- fresh `show` confirming unchanged identity/ownership, accepted criteria and their
  exact text, preserved completion history/dependencies and current revision;
- no pending authorization proposal and empty `pending_revalidation`.

Resume the lowest-numbered eligible step under the original commit authorization
only after every gate passes. Failed or incomplete handoffs leave execution
blocked; changing requirements requires the existing authorization decision.
