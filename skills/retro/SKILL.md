---
name: retro
description: Analyze the current conversation for concrete opportunities to improve reusable skills and workflows. Use when a person manually asks to review a session and recommend improvements to skills, procedures, or agent flows.
argument-hint: "[<focus-or-artifact>]"
---

# Analyze a session for skill and workflow improvements

Review the available conversation and work artifacts to find practical, reusable improvements to skills and workflows. This skill is advisory: do not edit skills, settings, project files, or conversation history unless the person explicitly asks for implementation.

## Review the evidence

1. Use only the conversation, tool results, and files that are actually available. State when earlier context or artifacts are missing; do not infer unseen actions or claim a pattern from one ambiguous event.
2. Trace the session from the user's goal through decisions, tool use, implementation, validation, and final outcome. Include actions by all participants and agents when visible.
3. Look for recurring or consequential friction, such as:
   - unclear or missing instructions that caused avoidable clarification or rework;
   - repeated searches, reads, commands, or tool calls that did not reduce uncertainty;
   - a workflow step that was skipped, happened in the wrong order, or produced weak evidence;
   - delegation that lost necessary context, duplicated work, or added a needless handoff;
   - a skill that was hard to discover, too broad, too narrow, or missing a useful decision rule;
   - project instructions that assume one machine's setup, such as local paths, installed tools, services, or model settings, where a portable fix exists;
   - mismatches between the requested outcome, the chosen approach, and what was validated;
   - preventable safety, scope, or communication issues.
4. Separate observed facts from interpretation. Cite concise excerpts, tool outcomes, or file paths and line numbers where available. Do not expose sensitive content unnecessarily.
5. Find the underlying rule or missing guidance, not just the one-off symptom. Treat a suggestion as reusable only when the evidence supports it; label a one-session hypothesis as tentative. Classify who owns each fix: a guidance gap belongs in the skill or reference that owns the flow; an execution skip — the rule existed and was skipped — is fixed by a mechanical verification step the flow can enforce or by the executor's own discipline, never by restating the skipped rule.

## Recommend changes

Distill the evidence into at most five high-value changes; a one-off user or project decision is not a recommendation. If the cap cuts a candidate that still clears the evidence bar, name it in one line so the person can ask for it later. Do not propose rules for preferences already handled correctly, duplicate existing guidance, or optimize harmless actions without evidence. If no substantiated improvement is found, say so and identify any evidence limitation.

Fix the general flow before project-specific instructions. Project guidance such as `AGENTS.md` is read on every computer that works on the repository, so it may state only what holds on any machine. When friction traces to a specific environment (local paths, installed tools, services, credentials, harness or model settings), route the recommendation to portable guidance — a personal or global skill, a portable procedure, or a discovery step the session can run anywhere — instead of encoding that environment into project files, and flag existing project guidance that assumes one machine's setup.

Present every recommendation in one prioritized table, one line per recommendation, ordered high to low by Priority:

| Priority | Confidence | Location | Change | Evidence |
| --- | --- | --- | --- | --- |
| high | high | `skills/<skill>/SKILL.md` | add the missing decision rule | repeated rework traced in step 3 |
| medium | tentative | review dispatch prompt | carry the scoped file list | one session, one round |
| high | high | portable skill, not project AGENTS.md | move the machine-specific step into the reusable flow | project instruction named a local path and failed elsewhere |

- **Priority** — high, medium, or low.
- **Confidence** — high, medium, or tentative; a one-session hypothesis is marked `tentative` in this column, never hedged in prose.
- **Location** — the skill, workflow, or file to change when known; prefer the portable flow over project files unless the fix is a project invariant.
- **Change** — the specific adjustment and the rework, risk, or ambiguity it should reduce.
- **Evidence** — the shortest grounded pointer: a file path with line numbers, a concise excerpt, or a tool outcome.

When the person authorizes implementation, treat each target file as owned by its repository: apply that repository's own `AGENTS.md` rules and run its affected validation — a skills repository carries its own instructions and tests — and locate the person's actual instructions file before editing it; personal instructions can live beside the harness configuration rather than in a skills repository.

## Response

Use the language of the user's request. Lead with the table, then:

1. Elaboration for at most three recommendations the person will act on now — a few lines each, adding only what the table line cannot carry.
2. One next-action line, such as drafting a skill change, revising a workflow, or collecting more examples before changing guidance.

The omit list is fixed: session retelling, restated evidence, generic productivity advice, and empty categories — no placeholder rows, priority tiers, or report sections with nothing to act on. Do not implement recommendations without explicit authorization.
