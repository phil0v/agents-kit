---
name: project-rules
description: Install or refresh the two documents that make agent work in a repository consistent and reviewable. A root AGENTS.md block carries strict development approaches, a done definition and exact checks; one self-contained rules file carries ownership, style, design rules and framework documentation pins. Use when the user asks to add, update, or refresh project rules or agent guidance for a repository.
argument-hint: "[<project-path>] [--rules-file <path>]"
---

# Install or refresh project rules

## Package resources

Resolve this package root from the absolute filesystem path used to load this `SKILL.md`: the file is under `skills/<skill>/SKILL.md`, so the package root is two directories above its containing skill directory. Resolve symlinks first. If the loader supplies a resource URI, use its package-location metadata; stop if the package location cannot be established. Keep the package root separate from the target root.

Set `validator` to `<package>/skills/project-rules/scripts/rules.py`. Run it with system Python 3.9 or newer. It reads and checks; it never writes.

## Output

The skill writes two documents in the target repository. Draft and approve them together; write them together.

1. **Index block** in root `AGENTS.md`, from `<!-- project-rules:begin -->` to `<!-- project-rules:end -->`. `AGENTS.md` is the harness-neutral entry point, so the block holds what an agent must not miss. Budget: 12 nonblank lines and 150 words. Content: the directive to read the rules file, one binding rule per chosen approach, the `Done:` line, the `Frameworks:` line, and one link to the rules file.
2. **Rules file**, default `docs/project-rules.md`; `--rules-file` selects another repository-relative path. Sections in this order: `## Checks` always; then `## Ownership`, `## Approaches`, `## Code style`, `## Design`, `## Framework`, each omitted when empty. Target 100 lines. Past 150 lines, cut the rules that restate a Minor default of a standard; keep the pins.

The block loads on every turn and the file before every edit in the target project. Each line must change what an agent does there. Both documents are self-contained English with repository-relative paths and versioned official documentation links. Neither refers to this package, its tools, personal settings, or machine paths. The first rule of the file: rules apply to the lines a change touches; untouched code stays untouched.

Run only on an explicit request. Accept one optional project path and one optional `--rules-file`; reject other arguments and options. Work directly: no roles, no agent forms, no usage metrics. Change only the block, the rules file, and their parent directories. Edit the root `CLAUDE.md` adapter, edit `.style-gate.json`, or update or remove superseded `rule_sources` files only after explicit user approval.

## Prepare

```sh
python3 "$validator" prepare "<start>" --rules-file "<path>"
```

`<start>` is the given path or the invocation directory. Exit 0 prints JSON with `root`, `rules_file`, `git_dir`, `common_dir`, `claude_md`, `rule_sources`, and `style_gate`. Exit 1 prints findings on stderr: report them and stop. Exit 2 is a usage error: compare the command with the line above, correct it once, or stop and report. Use the returned `root`, `rules_file`, `rule_sources`, and `style_gate` in every later step.

The command verifies the Git worktree with one scrubbed Git environment and checks both destinations (symlink, reparse point, case, `.git`, Git metadata, ignore rules). It reports the `CLAUDE.md` state, lists existing `rule_sources` files, and checks `.style-gate.json` (`valid`, `invalid`, or `absent`). It reads neither `AGENTS.md` nor the rules file.

If `style_gate` is `invalid`, read `.style-gate.json` in Discover and report what is invalid; Approve covers the fix.

## Discover

Read in one batch: root `AGENTS.md`; every file in `rule_sources`; `.style-gate.json` if present; the manifests and lockfiles that exist (`composer.json` with `composer.lock`, `package.json` with its lockfile, `go.mod`, `pyproject.toml` or `requirements*.txt`, `Cargo.toml`, `pom.xml`, `build.gradle*`, `*.csproj`); `Makefile`, package scripts, and CI configuration; test configuration; representative source of each scope; and root `CLAUDE.md` as evidence of conventions. Confirm each claim about the tree with a path. Take names and versions from a lockfile with a targeted search or `jq`, never a full read.

**Scopes and ownership.** A manifest and the source tree it governs confirm a scope. File extensions and a root JS manifest alone do not. An independently packaged frontend owns its subtree. A Symfony build owns PHP, Doctrine, API Platform endpoints, and the TypeScript it imports, even with a root JS manifest. API use does not move ownership: pagination and DTO resources belong to the endpoint; paginated responses and the shared error shape belong to the consumer. When ownership is unclear, show the paths and the evidence and ask. Never apply two stacks' rules to one path.

**Approaches.** Any approach, in any combination, fits; the set must serve this project.

1. Detect what the tree practices. Each detected approach needs evidence: a gate, a convention in configuration, docs, or history, or a layer. A name or a dependency alone is not evidence.
2. Assess what the project needs: domain complexity, integration surface, data ownership, contract consumers, risk, scale, change cadence, legacy constraints. Propose the approaches that serve those needs, including ones the tree does not practice yet, each with the need it serves and its cost.
3. Let the user choose. An approach the user names in the request is confirmed by that instruction. Conflicting signals become a question in the draft, not a declared approach.

**Checks.** Find what the project runs and how: a `## Checks` section in `AGENTS.md` (lines `- <repo>: <command>`), `Makefile` targets, package scripts, and CI steps. Record each exact non-interactive command without TTY flags (omit `docker exec -it` and `-ti`; pass `-T` to `docker compose exec`) and without user-home host paths. Record the working directory, prerequisites (services, env files, flags), a timeout, the green output, and any baseline test noise (pre-existing failures, skips, or warnings). If dependencies or env files are absent in a fresh worktree, record an isolated worktree or replica setup recipe. If `style_gate` is `invalid`, or a containerized check needs a `.style-gate.json`, draft it with portable `{relpath}` templates and without user-home host paths. No checks found is a fact to write, not a reason to skip the section.

**Rule sources.** Take substantive rules only. Skip routing, review protocol, execution steps, and findings format.

| Scope | Sources |
|---|---|
| Every scope | `<package>/standards/code-style.md`, `<package>/standards/test-quality.md`, `<package>/standards/code-design.md` |
| Go | add `<package>/standards/golang.md`; honor the `go.mod` minimum |
| Symfony/PHP with its bundled TypeScript | `<package>/standards/symfony.md`: `## Conventions` and `## Doctrine Collections` |
| Independently packaged frontend | `<package>/standards/frontend.md`: `## Conventions` and the families the package uses |
| Agent configuration alone | `<package>/standards/agent-config.md`: `## Conventions` |
| Other languages, infrastructure, mixed stacks | `<package>/standards/code-reviewer.md`: its category rows where the stack needs them |

**Existing rules and invariants.** Check every `rule_sources` file against the tree and separate live rules to fold into the draft from stale or superseded rules. Record co-change invariants in the tree (sources and generated files, schemas and migrations, API contracts and consumers, or paired configs). Record legacy identifier spelling rules where existing code uses non-standard names or casing.

**Frameworks and libraries.** From the manifests and lockfiles, list each framework and each direct dependency with its exact version; skip transitive dependencies. The generated rules must make agents follow the official documentation and the best practices of each. For one the standards cover, take the standards' rules. For one they do not cover, take the best practices from its official documentation at the detected version: its best-practices page when one exists, else the guides for the parts the project uses. Write each as a rule and cite the page it comes from. Record each framework's read-only pre-edit diagnostic commands (such as route lists, service wiring dumps, schema checks, or type checks). Pin in `## Framework` the official documentation pages for the parts the project uses, by detected version, and name the decision each pin governs. Fetch each pin before you show the draft. Replace a dead versioned page with the dependency's official page that resolves. Mark a pin unverified when no fetch capability exists. Never link a generic latest version. Honor an explicitly required project review integration; stop when it is unavailable.

## Draft

**Index block.** Example shape; the content comes from Discover:

```markdown
<!-- project-rules:begin -->
Read [the project rules](docs/project-rules.md) before you edit, and again after a context reset.
Approaches:
- Vertical slices — a feature's code and tests live in one `src/<Feature>/` directory; no shared `Utils/`.
- Test-first — a behavior change ships a test that fails without it; a fix ships the reproducing test.
Done: `make test lint` exits 0. Handoff names the files, each check with its exit status, decisions taken, and open items. Change only what the task names; report unrelated edits.
Frameworks: read and cite the pinned official documentation before your first edit; if no pin covers a question, read the official docs at the detected version and cite them in the handoff.
<!-- project-rules:end -->
```

1. One directive line with the one link to the rules file.
2. `Approaches:`, then one line per chosen approach as `Name — binding rule`. A binding rule is one sentence that an agent can obey and a reviewer can check on a diff: what goes where, what ships with what, or what never happens.
3. `Frameworks:` one line requiring agents to read and cite the pinned official documentation before the first edit; with no pins, require the official documentation at the detected version before the first edit and citations in the handoff.
4. `Done:`, then three parts:
   - the primary check command, or `verification is manual` when the project has no checks;
   - the handoff contract: files, each check with its exit status, decisions taken without asking, open items;
   - the scope rule: change only what the task names; report unrelated edits, do not make them.

**Rules file.** Open with one H1 and the scope rule.

- `## Checks`: the recipe from Discover **Checks**: prerequisites and any isolated recipe first, then each command verbatim in a code fence with its working directory, timeout, and baseline noise. When `AGENTS.md` already has a `## Checks` section, name it as the command source and add only what it lacks. A project with no checks says so here.
- `## Ownership`: a path-to-scope table, then the ownership rules the table does not show.
- `## Approaches`: per chosen approach, the need it serves, the paths that confirm it (`declared` when the user chose it without evidence), then the obligations beyond the block's one line, each scoped by path where a scope applies.
- `## Code style`: the project's own conventions that the tree shows and a newcomer would not guess, legacy identifier spelling rules, and the rules a standard bands Critical or Major. Omit a Minor standard rule and a rule the tree already satisfies everywhere. A rule the tree contradicts is a question in the draft.
- `## Design`: stack-specific design rules and co-change invariants (artifacts that must change together), scoped by path.
- `## Framework`: per framework and each direct dependency the project imports or configures: name, detected version, how the project configures it (routes, service wiring, transports, build pipeline) as the tree shows it, read-only pre-edit diagnostic commands, the best-practice rules with the page each comes from, and the documentation pins with the decision each governs.

State each rule once. The block carries the binding one-liner; the file carries evidence and detail; neither repeats the other. Every rule traces to tree evidence, a standards section, or a documentation page; the file cites paths for approaches and documentation pages for framework rules. Omit a rule that duplicates another, names a path the project does not have, or explains a rationale longer than one sentence. No preamble, no tutorial, no example longer than one line.

Fold the live non-duplicate rules found in Discover into the rules-file draft and mark superseded files for update or removal. Compare the drafts with existing `AGENTS.md`; move restated details into the rules file first and trim the index to the block.

## Approve

Show both drafts in full, the scopes, each proposed approach with the need it serves, its cost, its evidence or `declared`, and the user's choice. The approach set is one batched choice: offer the proposed combination and the alternatives. Show also the checks found, the pins verified and unverified, every `rule_sources` file with the live rules folded from it, any proposed `.style-gate.json` draft, and each conflict with existing content. When an existing target file will change, show its current content next to the draft. A document whose current content equals its draft is preserved and reported as preserved. Require explicit approval to replace an existing block, trim restated index content, change existing rules-file content, write or fix `.style-gate.json`, update or remove superseded `rule_sources` files, or add the adapter. A declined `rule_sources` update or removal is skipped and reported; it does not stop the run. Unresolved ownership, conflicts, or other missing approvals stop the run; nothing is written.

Adapter: when `claude_md` is `present`, offer to append one standalone `@AGENTS.md` line to root `CLAUDE.md`. `imports` needs nothing. `absent` is not offered and not reported. `unusual` is reported and not edited.

## Apply

Re-run `prepare` from the same start. `root`, `git_dir`, and `common_dir` must match the first run. Re-read `AGENTS.md`, the rules file, every `rule_sources` file, and `.style-gate.json`; each must match the state and content the draft and live-rule folding were built on, or stop. Create only the parent directories of the rules file. Write the rules file, then `AGENTS.md`: replace only the block, or append the block with one blank line before and after it. After explicit approval, write any `.style-gate.json` change, update or remove superseded `rule_sources` files, and apply the adapter last.

## Validate

Put the complete drafts in a directory outside the repository as `AGENTS.md` and the rules file's basename. If `.style-gate.json` will be written or fixed, place its draft in `--draft-dir` as `.style-gate.json` before pre-apply `validate`. Run the first command; apply only after it exits 0. After the write, run the second.

```sh
python3 "$validator" validate "<root>" --rules-file "<path>" --draft-dir "<dir>"
python3 "$validator" validate "<root>" --rules-file "<path>"
```

Exit 0 requires `project-rules: layout valid` on stdout and an empty stderr. Exit 1 lists findings on stderr with a `project-rules:` prefix. Exit 2 is a usage error. A failed or unavailable validation is a blocker, never success. The validator checks markers, fences, comments, links, headings, the block budgets (lines and words), the `Done:` and `Frameworks:` lines, the section order, and the `## Checks` heading. In `## Checks` code fences, it rejects the commands that Discover **Checks** forbids. For `.style-gate.json` (from `--draft-dir` if present, else the repository root), it checks JSON structure, `tools` `write` and `check` string lists, and absence of user-home host paths. It does not check that the content is true; review the drafts for project fit separately.

## Finish

Report the root, the rules file, each changed or preserved file with its line count, the scopes, the chosen approaches with evidence or `declared`, the frameworks and libraries with their detected versions, the checks found, the pins verified and unverified, both validation commands with their exit status, and the adapter decision. State that layout validation does not show that the harness loads or follows the rules. When discovery is unverified, tell the user to instruct the harness to load root `AGENTS.md`.
