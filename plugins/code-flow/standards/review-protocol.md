# Shared review protocol

This protocol owns scope selection and workflow overrides when a standalone reference differs.

Resolve `<package>` from the caller's absolute package root; the references are internal resources, not separately installed skills. A routing hand-off names the corresponding bundled review reference in the dispatch table.

## Scope and comparison

**Step 1 — Get the diff**

A scope the caller named — paths, a commit, a range, the uncommitted work — is the scope. A target branch passed as the argument sets `base`. Otherwise resolve the default branch (not `@{u}`, which on a pushed feature branch is its own remote copy and diffs to nothing):
```bash
for ref in "$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)" origin/main origin/master main master; do
  git rev-parse --verify -q "$ref" >/dev/null 2>&1 && base=$ref && break
done
git diff "$base"...HEAD -- . ':(exclude)*.lock' ':(exclude,glob)**/*-lock.json' ':(exclude,glob)**/pnpm-lock.yaml'
```
Empty diff but `git status --porcelain` shows local changes → review the working tree with `git diff HEAD` and read the untracked files directly; neither command shows them. A named branch that is not a ref → report that rather than auto-detecting. No base and a clean tree → say so and stop.

Honor the caller's exact scope and return contract. For workflow review, the bundled reviewer and style-reviewer TOML instructions own their return formats, read coverage, and acceptance evidence; they replace standalone reference output formats. The style-reviewer owns comments, naming, test shape, imports, and behavior-preserving simplification; the reviewer owns correctness, security, reliability, test quality, and acceptance. Use the named reference's substantive rules and severity bands for those responsibilities. A style finding belongs with the style-reviewer rather than a duplicate correctness finding.

Read target repository instructions and constraints first. Review only assigned changes and the surrounding evidence needed to verify a finding. Inspect staged, unstaged, and untracked state before inferring scope from a branch. A single commit means its introduced diff, including the root-commit form when it has no parent; named ranges keep their supplied endpoints. On an unborn branch inspect staged additions and scoped untracked files without assuming HEAD exists. Read scoped untracked files separately. For a named committed change with existing local edits, inspect the original diff plus the caller's separately recorded workflow edits; unrelated changes stay context.

When no scope is supplied, use identifiable active-task edits, otherwise uncommitted work. A clean tree needs a resolved comparison and an announced baseline before review. Report an unresolved scope, missing required reference, unreadable evidence, or incomplete result as blocked or incomplete; it is not a clean verdict.

Inspectors are read-only: leave edits, formatters, check execution, staging, commits, and pushes to the workflow's authorized owners. Check existing tests as evidence without running them. Cite actionable findings at verified repository-relative lines, preserve requirements and scenario meanings, and state evidence limits. Read across groups for context while anchoring findings and the `read:` list in assigned files. The caller retains coverage, acceptance, retry, and commit gates.

A workflow that partitions a larger change assigns each inspector's review scope by group; the group's file and line limits apply to that assignment, and the coordinating workflow remains responsible for complete coverage of the original scope. A standalone review instead applies its reference's own size cutoffs and names what it leaves out.

## Comments

Comment noise is a reportable finding, not a style preference. The standard is `<package>/standards/code-style.md`'s **Comments** section, all of it — its not-earned list and its earned cases alike, so a comment it allows (`// 8 MiB` on `1 << 23`) is allowed here. Type hints that duplicate an existing annotation (`@param`/`@return` duplicating a native type hint, JSDoc types duplicating a TypeScript annotation) are not earned either. The fix is deletion, or a rename or extraction making it redundant; say which.

Each reference assigns the band for a comment finding through its own severity table.

## Findings format

Review references number findings as follows; each reference's own severity bands and finding template stay authoritative, and workflow reviewers replace this format with their TOML return contract.

- Open with a verdict line — `1 Critical, 3 Major, 2 Minor` — then the findings; past 10, add a one-line index.
- Number from 1, ordered by severity band, highest first; one issue per number, except that findings in one file share a number when they sit in the reference's lowest reported band — Minor, since Trivial is omitted — or are comment findings banded by its severity table.
- Every Critical and Major carries a code snippet or a concrete pointer; paths and line numbers are accurate relative to the diff.
- One finding per root cause: no duplicate findings for one root cause, no preamble, no restatement of the task, no closing summary.
- No material issues → say so briefly and name one or two things done well.
