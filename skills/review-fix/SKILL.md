---
name: review-fix
description: Run a read-only review, fix every finding, verify mechanical claims against real tools, and re-review the fix diff against the findings. Use when asked to review and fix, fix review results, apply review feedback, or run the review-fix loop on a change. Not for review-only requests or close-out with a commit (use wrap-up).
argument-hint: "[<scope>]"
---

# Review-fix

Three separate passes with separate evidence. Never merge them: a fix written inside the review pass inherits the reviewer's blind spots, and a re-review that re-audits from scratch loses the findings it must check against. Read-only review rules bind the review pass; edit tools are used only in the fix pass. Never commit; committing is a separate authorized step.

## Review

1. Resolve the review scope exactly (paths, diff, or uncommitted work) and route it to the matching reviewer: the `code-reviewer` skill for code, configuration, and agent files. If that skill is unavailable, stop and report the missing dependency rather than reviewing without its rule set. Read the routed reference before judging.
2. Keep the review read-only. Every finding carries a citable file and line, the triggering input or path, and a concrete consequence. Drop what cannot be cited, silently.
3. Report the verdict line by severity, then numbered findings with suggested replacements. Stop here and report if there are no actionable findings.

## Fix

1. List each finding, its file and line, and its exact replacement in one message. Wait for one confirmation of the set. Then apply the confirmed findings only. Keep any declined or unanswered finding open and include it in the final report. Skip this gate only if the request already approved the set (for example, "fix all" after the review).
2. Apply one targeted edit per confirmed finding, taken from its suggested replacement. Do not rewrite surrounding content, and do not fix anything the review did not raise; report adjacent cleanup as follow-ups.
3. Execute every mechanical claim the fix introduces (a command, flag, or API behavior) against the real tool in a scratch environment rather than asserting it. If the claim fails, rework the fix and re-confirm it before continuing.
4. Re-check coherence at each touched spot: skeletons, examples, and sibling lines that restate the fixed rule must match it.

## Re-review

1. Scope the re-review to the previous fix round's changes only — its diff plus the surrounding lines needed to confirm a finding is closed. When the fix round applied no edits, skip the re-review and go straight to the final report with all findings open. Do not re-audit the rest of the file; anything outside the fix diff is out of scope even if suspicious, and is reported as a follow-up instead.
2. Check each fix against the finding it closed: the named branch now exists where the gap was, no two clauses clash on the triggering input, and the fix introduced no new contradiction or dead reference.
3. New actionable findings from the re-review loop back to the fix pass. Loop at most three rounds; a finding that survives two fix attempts is not converging — stop, revert nothing, keep the working tree as is, and report it with both failed attempts and their evidence.
4. When a round closes clean, report the verdict by severity, per-finding confirmation with line numbers, the rounds taken, declined or unconfirmed findings left open, out-of-scope observations as follow-ups, evidence executed during the fix passes, and remaining assumptions. State what was not run.
