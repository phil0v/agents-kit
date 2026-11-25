Read `<package>/standards/review-protocol.md` before this reference. Resolve `<package>` from the dispatch context.

## Routing

Symfony PHP applications, and the TypeScript that ships alongside them in the same repository. Hand off: a React/Next/Vue/Nuxt frontend of its own → `frontend`; Python, Go, Rust, Java, C# → `code-reviewer`; a diff that is nothing but AI agent configuration (`SKILL.md`, subagent, command and prompt markdown, `CLAUDE.md`/`AGENTS.md`, harness settings such as `settings.json`) → `agent-config`, though mixed with PHP it stays here.

**Verify the project first:** `composer.json` with `symfony/symfony` or `symfony/flex`, plus `config/`, `bin/console` or `symfony.lock`. If none, stop and say this reference only applies to Symfony projects.

Review as a **senior Symfony developer** and **senior TypeScript developer**.

## Architecture
- **Thin Controllers:** HTTP logic only — business logic goes to services, command handlers or domain models.
- **Constructor DI only:** no `$container->get()` in production services. Use autowiring.
- **DTOs over Entities:** never expose Doctrine entities in API responses or forms.
- **Value Objects:** wrap primitives (`Email`, `Money`, `Slug`) to encapsulate validation and behavior.
- **Domain Events:** cross-cutting side effects (email, logging, sync) go in subscribers, not in the action service.
- **CQRS:** separate reads (Queries) from writes (Commands) in complex domains.

## Database & Doctrine
- **N+1:** flag loops containing `$entity->getRelation()` — use eager loading or JOIN in the repository.
- **No flush in loops:** batch, or flush after the loop completes.
- **Migrations:** additive only — no `DROP COLUMN` without a separate cleanup migration planned.
- **Locking:** pessimistic (`setLockMode(LockMode::PESSIMISTIC_WRITE)`) for critical writes; optimistic (version field) for low-contention entities.
- **Timestamps:** `PrePersist`/`PreUpdate` callbacks or `Timestampable` — never set manually in controllers or services.
- **Soft delete:** avoid on high-traffic tables (breaks unique indexes, bloats queries).

## Doctrine Collections
- **Collection operations:** prefer `filter`, `map`, `exists`, `contains`, `first`, `last`, and `slice` over `toArray()` round-trips or manual loops when behavior is equivalent. Keep explicit loops when an index, an early exit, or other required behavior cannot be expressed clearly with the Collection API.
- **Criteria selection:** use `matching()` with `Criteria` for suitable in-memory selection on an already-loaded collection. An uninitialized ORM association may execute the criteria in SQL instead; PHP and database comparisons can differ, so do not assume the same results in both modes.
- **SQL-bound filters:** put filters that must run in SQL in repository methods using parameterized QueryBuilder or DQL, such as filters on large associations or before pagination. Do not load a collection just to post-filter it or rely on `matching()` to guarantee SQL execution.
- **Mutation:** use the Collection API (for example, `add()` or `removeElement()`). If a mutation would affect traversal, collect the changes and apply them after the loop.

An equivalent simplification is 🟡 Minor; use the existing severity scale for findings with correctness or performance impact.

## Security
- **Validate all input:** Symfony Validator constraints or Form types before any processing.
- **Access control:** `#[IsGranted]`, Voters or explicit checks on every state-changing action.
- **Serialization groups:** `#[Groups]` on DTOs — never leak internal fields (`createdAt`, password hashes, internal IDs) in API responses.
- **No raw SQL string concat:** Doctrine QueryBuilder or prepared statements only.
- **Rate limiting:** required on all publicly-exposed endpoints.

## Performance & Reliability
- **Cache** an expensive DB query or external API call (Symfony Cache component) on a path the profiling rule below shows slow.
- **Never swallow exceptions** — handle it, wrap it with context (`['id' => $id]`), or re-throw; a log line alone does not handle it.
- **Iterators / batch processing** for large datasets in commands and background jobs.
- **HTTP caching:** `Cache-Control`, `ETag`, `Last-Modified` on public GET endpoints.
- **Profile before optimizing:** flag a path assumed slow without Blackfire/Profiler evidence.

## Coding Standards
- `declare(strict_types=1)` on every file; typed properties and return types everywhere.
- No public entity properties — use getters/setters.
- **Messenger handlers:** one message type per handler; handlers idempotent; pass entity IDs not entities (serialized entities go stale).
- **Dead letter transport** configured on all async transports; retry strategy set per transport.
- DTOs immutable (`readonly` properties / constructor promotion).
- Classes > 500 lines or > 10 methods → flag SRP violation.
- **Test coverage:** unit tests for domain logic and services (mocked deps) plus integration tests for repositories and full request cycles — both required.
- **Tests:** a behavior the diff adds or changes that no test asserts through its public surface — a request, a command, a handler, a service or repository method — or an edge it handles (empty input, a limit, an exception path) that no test reaches is 🟠 Major when the change introduced the path, 🟡 Minor when it only widened one that existed, the finding naming the assertion to add and tagged `[tests]`; a test that would still pass with the change reverted is the same finding, and so is what [test quality](test-quality.md) bands — a test that cannot fail on the behavior it names, a bug fix with no test that fails without it, a flaky construct, a test left asserting superseded behavior — under its scope line's legacy exemption. Shape is [code style](code-style.md)'s Tests section and takes 🔵 Style, the band the scale gives every code-style breach, assigned to the style-reviewer when the calling workflow separates that pass.

## API Platform (if used)
- Use DTOs as `ApiResource` — never raw Doctrine entities.
- Disable unused default operations explicitly; don't rely on `security` to hide them.
- Different validation groups for `create` vs `update`.

## Frontend / API Contract
- All collection endpoints **must paginate** — no unbounded result sets.
- Consistent error response shape: `{ "error": string, "violations": [...] }`.
- CORS: `nelmio/cors-bundle` with explicit origins — no wildcards in production.
- SPA fallback: catch-all route renders the frontend entry point for non-`/api/` paths.

## Comments

Comment findings follow the policy in `<package>/standards/review-protocol.md`. Here they take 🔵 Style.

## Scope guardrails

- Review only the diff or the files explicitly requested; do not audit the tree. Referenced but unchanged files are context, and a contradiction between a changed file and one it references is in scope.
- Skip generated and vendored trees: lockfiles, `vendor/`, `node_modules/`, `var/`, compiled assets, Doctrine proxies.
- These cutoffs bind a standalone review: past ~50 files or ~2,000 changed lines, take the highest-risk first and say which were left out. Never silently truncate. Under a grouped workflow they apply per assigned group instead: review every file in the assignment, and the parent workflow's coverage gate guarantees the complete original scope.

## Execution

**Step 1 — Resolve scope**

Follow the scope and comparison rules in `<package>/standards/review-protocol.md`.

**Step 2 — Architectural summary**

2–3 sentences: what changed, which layers are affected (controller / service / entity / migration / frontend / config), risk level (low / medium / high).

**Step 3 — Verify in context**

Issue every independent read, grep and glob in one call — serialized single-file reads are the slowest part of this review, and nothing here needs one file's contents to choose the next. Read around each suspicious hunk, not just the diff, and confirm the issue is reachable and not already mitigated by the framework, a bundle or the surrounding code. Before reporting a missing `#[IsGranted]`, check `config/packages/security.yaml`: an `access_control` rule already covering the path makes its absence a deliberate "any authenticated user", not a finding — report only when the endpoint needs narrower access than that rule grants, or touches another user's data with no ownership check. An empty search result is not evidence of absence: before concluding a REMOVED behavior is gone or that no caller exists, rerun the pattern through a second mechanism — a terminal `grep -rn` over `src` and `tests` — before relying on the negative. Stop once every finding you intend to write has a line you can cite; a finding you cannot point at a line for is not a finding. Drop what does not survive, silently.

**Step 4 — Numbered findings**

Number only what is actionable and has real impact.

````
**[N] Severity | File: path/to/file.php (line X)**
What is wrong and why it matters.

```suggestion
// corrected code here
```
````

🔴 **Critical** — security vulnerability, data loss risk, broken behavior; fix before merge. 🟠 **Major** — real bug risk, potential future bug, bad practice; fix before merge unless explicitly accepted. 🔵 **Style** — a breach of `<package>/standards/code-style.md`: comments, naming, tests, imports; fixed in the same run, never a reason for another pass. 🟡 **Minor** — improvement, style, small optimization; address if easy. 🟢 **Trivial** — formatting nit; omit if noisy.

**Rules:**
- Open with a verdict line — `1 Critical, 3 Major, 1 Style, 2 Minor` — then the findings; past 10, add a one-line index.
- Number from 1, ordered 🔴 → 🟠 → 🔵 → 🟡 → 🟢; TypeScript findings continue the PHP numbering, never restart. One issue per number, except that comment findings in one file share a number, as do 🟢 nits in one file.
- Every 🔴 and 🟠 carries a code snippet or a concrete pointer; paths and line numbers accurate relative to the diff.
- No duplicate findings for one root cause, no preamble, no restatement of the task, no closing summary.
- No material issues → say so briefly and name one or two things done well.

## Conventions

- Thin controllers — HTTP logic only; business logic goes to services, command handlers or domain models.
- Constructor DI only — no `$container->get()` in production services.
- DTOs over entities — never expose Doctrine entities in API responses or forms; wrap primitives in value objects.
- `declare(strict_types=1)` on every file; typed properties and return types everywhere.
- No public entity properties — getters/setters only; DTOs immutable (`readonly` properties / constructor promotion).
- Messenger handlers: one message type per handler, idempotent, pass entity IDs not entities.
- No raw SQL string concat — Doctrine QueryBuilder or prepared statements only.
- Validate all input via Validator constraints or Form types; `#[IsGranted]` or Voters on every state-changing action.
- Classes > 500 lines or > 10 methods → flag SRP violation.
- Every behavior the change adds or alters gets a test through its public surface — a request, a command, a handler, a service or repository method — its edges included; a test that passes with the change reverted proves nothing.
- All collection endpoints paginate; consistent error shape `{ "error": string, "violations": [...] }`.
