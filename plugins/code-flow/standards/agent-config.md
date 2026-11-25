Read `<package>/standards/review-protocol.md` before this reference. Resolve `<package>` from the dispatch context.

## Routing

Use when the diff is agent configuration: `SKILL.md`, subagent definitions, slash-command and prompt markdown, `CLAUDE.md`, `AGENTS.md`, and the harness settings driving them (`settings.json` permissions, hooks, model and effort keys). Hand off application code to `code-reviewer`, `symfony` or `frontend` by stack; a diff mixing config with code belongs wholly to the stack skill, these files included.

## Review categories

Run all eleven, at unequal depth: **Failure paths**, **Cross-file coherence** and **Instruction determinism** yield most real findings. The other eight are scans over the changed lines.

- **Harness contract** — keys spelled as sibling definitions spell them (`allowed-tools` vs `tools`, placeholder syntax, `CLAUDE.md` vs `AGENTS.md`). Flag invented keys, values outside the set sibling definitions use, permission-rule syntax where a plain tool name belongs. Verify a key against the sibling files of the reviewed file's own kind in this repository (other `SKILL.md` files for a skill, `agents/` for a subagent definition, `commands/` for a command): a key absent from sibling files needs verification against the target harness schema; sibling absence alone does not prove it is invented.
- **Cross-file coherence** — a rule restated and now divergent; a deferral to a step number that shifted; two files claiming one default with opposite values; a hand-off to a mode, flag or step that no longer exists. Confirm both sides against real lines using the [review protocol](review-protocol.md).
- **Instruction determinism** — anything letting an agent behave differently on identical input: a threshold like "a handful"; two clauses that cannot both hold; a precondition nothing guarantees; a branch with no defined outcome. Ask of each rule what happens when its condition fails, and whether that is written down.
- **Failure paths** — the branch never written. A subagent that errors, returns empty, or asks a question instead of returning a verdict; a gate omitting the failure the step above it produces; a retry carrying none of the output needed to do better; an agent asked to do what its own contract forbids.
- **Routing and triggers** — a `description` saying when the file fires as well as what it does; overlapping triggers with no tiebreak; a missing hand-off to the more specific definition; a skill routed into an agent lacking the tools its procedure needs.
- **Capability assumptions** — silent runtime dependencies: whether a subagent receives the user instruction file, whether definitions are pinned at startup, whether a declared tool is granted, whether a path exists on another machine. Test it or mark the finding as resting on it; assert what a test shows.
- **Tool grants** — a list broader than the job; a read-only contract enforced by prose over a granted shell; hooks auto-approving more than they describe; instructions to bypass permissions, commit unasked, force-push, or edit outside the working directory.
- **Prompt-embedded shell** — destructive, unquoted, interactive or hang-prone commands; prose misreading a command's output (`git diff` hides staged and untracked alike); missing timeouts; hardcoded paths, tokens or machine-specific values.
- **Context cost** — a rule duplicated across files that will drift; prose restating the next line; a whole-file read where a targeted search answers the same question; a large block inlined that could load on demand. A rule a file states as the contract other files read (an always-loaded `CLAUDE.md` or `AGENTS.md` section that sibling files point at) is the one statement and stays where it is.
- **Positive phrasing** — a rule worded as a prohibition (`never`, `do not`, `don't`, `NOT`, `no X`) in an instruction the reviewed file gives its agent, where the action to take instead carries the same rule; the finding quotes the line and offers the positive rewrite. A prohibition stays where the positive form runs longer or vaguer: a hard safety line (destroy, leak, bypass), a negation inside a condition or output contract (`no material issues`).
- **Enforcement** — a rule the diff states in prose that a script or hook in this repository could enforce mechanically; a test on a prose file that pins a quoted clause or sentence instead of one of the invariants [code style](code-style.md) Tests allows. The finding names the script, hook, or invariant test, its tag `[enforcement]`.

## Bands

- **Harness contract** — Major when a key or value violates the target harness contract; Minor when a supported spelling varies among sibling definitions without changing behavior.
- **Cross-file coherence** — Major when a contradiction or dead reference sits on a real path; Minor when a restatement has not yet diverged.
- **Instruction determinism** — Major when two clauses cannot both hold on a real input; Minor when a threshold is uncounted.
- **Failure paths** — Critical when a path destroys work, leaks a secret, bypasses a gate or commits unreviewed; Major when a branch is missing on a real path.
- **Routing and triggers** — Major when triggers overlap with no tiebreak; Minor when a description falls short of its trigger.
- **Capability assumptions** — Major when a silent dependency sits on a real path.
- **Tool grants** — Major when a grant is broader than the job and includes a shell or a write; Minor when it is narrower than the job.
- **Prompt-embedded shell** — Critical when a command is destructive or unquoted on a real path; Major when it is hang-prone, interactive or without a timeout.
- **Context cost** — Minor.
- **Positive phrasing** — Minor.
- **Enforcement** — Major when a rule a script or hook could enforce mechanically is left to prose; Minor when a test on a prose file pins a quoted clause that breaks on a rewording.

## Comments

Comment findings follow the policy in `<package>/standards/review-protocol.md`. Under this reference they are banded as Minor in the `## Bands` table.

## Scope guardrails

- Review the diff or what was explicitly requested, and stop at its edge.
- Referenced but unchanged files are context; a contradiction between a changed file and one it references is in scope.
- Skip generated caches, vendored plugin copies, `node_modules/`, and lockfiles. Authored plugin manifests and marketplace catalogs are source when they are in the requested scope.
- These cutoffs bind a standalone review: past ~50 files or ~2,000 changed lines, take the highest-risk first and name the files left out. Under a grouped workflow they apply per assigned group instead: review every file in the assignment, and the parent workflow's coverage gate guarantees the complete original scope.

## Execution

**Step 1 — Resolve scope**

Follow the scope and comparison rules in `<package>/standards/review-protocol.md`.

**Step 2 — Map the change**

2–3 sentences: which definitions changed, what that alters at run time, risk level (low / medium / high). Name the files it only references.

**Step 3 — Verify in context**

Issue every independent read, grep and glob in one call.

- Changed files: read once, whole, and only where the diff's context does not settle a claim.
- Referenced but unchanged: grep for the clause you are checking, opening the file only where the grep's context does not settle a claim.
- Shell in the diff: run its read-only form or `--help` once rather than reasoning across turns.

Stop reading once every finding you intend to write has a line you can cite. A finding stands on a line you can cite; a contradiction stands on both sides read. Drop what falls short, silently.

**Step 4 — Numbered findings**

Number only what is actionable and has real impact.

````
**[N] Severity | File: path/to/file.ext (line X)**
What is wrong, and the input or path that makes an agent behave badly because of it.
````

Add a ```suggestion block where the finding leaves the corrected wording open; the block holds the replacement text, and the finding's own words stay in the finding.

Severity is the `## Bands` entry of the finding's category; a finding matching no band of its category's `## Bands` bullet is Minor, and a wording nit is 🟢 Trivial and omitted.

**Rules:**
- Open with a verdict line — `1 Critical, 3 Major, 2 Minor` — then the findings; past 10, add a one-line index.
- Number from 1, ordered 🔴 → 🟠 → 🟡 → 🟢. One issue per number, except that 🟢 nits in one file share a number.
- Every 🔴 and 🟠 cites both sides when it is a contradiction, and the path that triggers it.
- One finding per root cause; end on the last finding.
- No material issues → say so briefly and name one or two things done well.

## Conventions

- Frontmatter keys spelled as sibling definitions spell them (`allowed-tools` vs `tools`, placeholder syntax), checking uncertain keys against the target harness schema rather than treating sibling absence as proof.
- State a rule once; a rule restated elsewhere drifts — cross-reference it instead of duplicating.
- Thresholds are counts ("a handful" becomes a number), clauses can all hold at once, and each rule states what happens when its condition fails.
- Every failure path is written: what an agent does when it errors, returns empty, or cannot decide.
- `description` says when the file fires as well as what it does; overlapping triggers get a tiebreak or a hand-off to the more specific definition.
- Tool grants match the job; a read-only contract is enforced by the tools granted.
- Shell embedded in a prompt is quoted, non-interactive, carries a timeout, and takes paths, tokens and machine-specific values as variables or arguments.
- A targeted search replaces a whole-file read where it answers the same question.
- A rule a script or hook could enforce mechanically lives in that script; a test on a prose file pins one of the invariants `<package>/standards/code-style.md` Tests names, never a quoted clause.
- Rules state the action to take; a prohibition remains where the positive form runs longer or vaguer.
