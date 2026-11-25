Read `<package>/standards/review-protocol.md` before this reference. Resolve `<package>` from the dispatch context.

## Routing

Backends in Python, Go, PHP (non-Symfony) and mixed languages; general security audits (OWASP Top 10, secrets, auth); infrastructure as code (Terraform, Docker, Kubernetes, CI/CD) — whenever no more specific skill applies. Hand off: React/Next/Vue/Nuxt → `frontend`; Symfony → `symfony`; a diff that is nothing but AI agent configuration (`SKILL.md`, subagent, command and prompt markdown, `CLAUDE.md`/`AGENTS.md`, harness settings such as `settings.json`) → `agent-config`, though mixed with code it stays here.

## Review categories

| Category | Tag | Critical when | Major when | Minor when | Standard |
|---|---|---|---|---|---|
| Correctness | `[correctness]` | a reachable production path returns a wrong result, loses or corrupts data, or crashes the process | a wrong result or an unhandled crash on an input the code accepts — an edge, a boundary, a concurrent caller | a wrong result only on an input the boundary already rejects | this row |
| Security | `[security]` | injection, auth bypass, secret exposure or deserialization of untrusted input reachable from outside | the same reachable only by an authenticated or internal caller | a hardening gap with no reachable exploit | this row; OWASP Top 10, hardcoded or logged secrets, crypto and key management, API authentication/authorization |
| Performance | `[performance]` | unbounded growth of memory, connections or disk under normal load | N+1 or O(n²) on a path the change or its tests show as hot, blocking I/O in an async context | the same cost on a path neither the change nor its tests show as hot | this row |
| Reliability | `[reliability]` | a race or lost write that corrupts shared state | a call across a network boundary that the caller re-issues without idempotency, a webhook handler that is not idempotent, an outbound call with no timeout | a production config value outside its documented range with no failing path shown | this row |
| Infrastructure & config | `[infrastructure-&-config]` | a container running as root with a secret mounted, a CI step printing or committing a secret | a missing resource limit, health probe or restart policy, an image not pinned as code-design's Simplicity section requires, Terraform state untracked | a hardening gap | this row |
| Tests | `[tests]` | — | a behavior the diff adds or changes with no test through the public surface, an edge or error return the change handles with no case, a guard with no case that fails without it, a test that cannot fail on the behavior it names, a bug fix with no test that fails without it, or a flaky construct — timing, ordering, shared mutable state | an untested path the change only widened, a test that still passes with the change reverted | this row and `<package>/standards/test-quality.md`; the finding names the assertion to add |
| Design | `[architecture], [isolation], [errors], [simplicity], [go]` | the band line opening the code-design section the tag names | that band line | that band line | `<package>/standards/code-design.md`, read when it is not already preloaded |
| Acceptance | `[acceptance]` | — | an unmet plan criterion, as `<package>/roles/reviewer.toml` traces it | — | `<package>/roles/reviewer.toml` |

A comment, naming, test-shape or import finding is code-style's and the style-reviewer's, never raised here; a test's substance is `<package>/standards/test-quality.md`'s and is raised here.

## Scope guardrails

- Review only the diff or the files explicitly requested. Referenced but unchanged files are context, and a contradiction between a changed file and one it references is in scope.
- Skip generated and vendored trees: lockfiles, `node_modules/`, `vendor/`, compiled and minified assets, `.pb.go`, OpenAPI/protobuf output, migration snapshots.
- Prompt files (`SKILL.md`, subagent, command and agent-instruction files such as `CLAUDE.md` and `AGENTS.md`) are source: in a mixed diff they are reviewed here against `<package>/standards/agent-config.md`'s `## Review categories`, banded by its `## Bands` section, both read for them, and a finding on one is tagged with its matching agent-config category.
- Past ~50 files or ~2,000 changed lines, take the highest-risk first and say which were left out.

## Execution

**Step 1 — Resolve scope**

Follow the scope and comparison rules in `<package>/standards/review-protocol.md`.

**Step 2 — Architectural summary**

2–3 sentences: what changed, which layers are affected, risk level (low / medium / high).

**Step 3 — Verify in context**

Issue every independent read, grep and glob in one call — serialized single-file reads are the slowest part of this review, and nothing here needs one file's contents to choose the next. Read around each suspicious hunk, not just the diff, and confirm the issue is reachable and not already mitigated by the framework, a library or the surrounding code. Stop once every finding you intend to write has a line you can cite; a finding you cannot point at a line for is not a finding. Drop what does not survive, silently.

**Step 4 — Numbered findings**

Number only what is actionable and has real impact.

````
**[N] Severity | File: path/to/file.ext (line X)**
What is wrong and why it matters.

```suggestion
// corrected code here
```
````

Severity is the band column the finding's category row gives — for the Design row, the band line opening the code-design section it tags; a finding matching no cell of its category row is 🟡 Minor (the Design row's case is code-design's own rule), and a nit is 🟢 Trivial and omitted.

**Rules:**
- Format findings per the `## Findings format` section of `<package>/standards/review-protocol.md`.
