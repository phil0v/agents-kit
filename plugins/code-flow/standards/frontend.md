Read `<package>/standards/review-protocol.md` before this reference. Resolve `<package>` from the dispatch context.

## Routing

React, Next.js, Vue, Nuxt and other frontend/UI code; `package.json`, build config (`vite.config.js`, `next.config.js`, `nuxt.config.ts`) and frontend CI; CSS, design systems and component libraries. Hand off: Symfony PHP → `symfony`; pure backend (Python, Go, Rust, Java) → `code-reviewer`; a diff that is nothing but AI agent configuration (`SKILL.md`, subagent, command and prompt markdown, `CLAUDE.md`/`AGENTS.md`, harness settings such as `settings.json`) → `agent-config`, though mixed with frontend code it stays here.

**Verify the project first:** `package.json` with `react`, `next`, `vue` or `nuxt`, plus `pages/`, `app/` or `components/` holding `.tsx`, `.jsx` or `.vue`. If none, stop and say this reference only applies to frontend projects.

## Architecture & Components

- **Single Responsibility:** components > 200 lines should be split.
- **React:** extract stateful logic into custom hooks (`useFetch`, `useAuth`).
- **Next.js App Router:** `"use client"` only for interactive elements; Server Components handle data fetching by default.
- **Vue/Nuxt — Composition API:** prefer `<script setup>` over Options API. Extract reusable logic into `composables/useX()`.
- **Vue reactivity:** `ref()` for primitives, `reactive()` for objects; never destructure a `reactive()` object — it loses reactivity. Prefer `ref` when in doubt.
- **Nuxt data fetching:** `useAsyncData` / `useFetch` for SSR-compatible fetching. Avoid bare `fetch()` in `setup()`.
- **Nuxt routing:** `<NuxtLink>` instead of `<a>`. `definePageMeta()` for route guards and layout assignment.
- **Nuxt client/server split:** `<ClientOnly>` for browser-only code; `.client.vue` / `.server.vue` suffix for component-level splitting.
- **State management (Vue):** Pinia over Vuex. State mutated only inside store actions — no direct property writes from components.

## Styling & UX

- **CSS architecture:** prefer CSS Modules or scoped styles (Vue `<style scoped>`). Avoid ad-hoc inline styles, and flag a property set by both an inline style and a class.
- **Design tokens:** CSS variables (`--color-primary`, `--spacing-md`) for themes, typography and spacing — flag hardcoded hex/rgba and magic pixel values.
- **Responsive layouts:** flexbox/grid with media queries. Flag overflow on small screens.
- **`!important`** is a specificity problem, not a fix. Global overrides of a component library's classes need a scope.

## Performance

- **Re-renders (React):** flag unnecessary state hoisting. Suggest `React.memo`, `useMemo`, `useCallback` only when profiling or clear parent re-renders warrant it.
- **Re-renders (Vue):** avoid heavy computed chains; prefer `computed()` over watchers for derived state.
- **Images:** Next.js `<Image>` or `<NuxtImg>` with `srcset`. All images have `alt`.
- **Bundle size:** no whole-library imports when only a utility is needed.
- **Dynamic imports:** `next/dynamic` or `React.lazy` / `defineAsyncComponent` for modals, heavy charts, off-screen content. Nuxt: `<LazyComponent>` for below-the-fold.

## Security & Accessibility

- **XSS:** `dangerouslySetInnerHTML` (React) and `v-html` (Vue) require DOMPurify or equivalent — flag without exception.
- **Secrets:** no API keys or credentials hardcoded or exposed in client bundles. Only `NEXT_PUBLIC_` / `VITE_` prefixed vars reach the browser.
- **Form input:** every input validated before submission, with inline error messages — and never as the only check, since a client-side rule is not a defence.
- **Semantic markup:** correct `<header>`, `<nav>`, `<main>`, `<footer>`, `<article>` — not div soup.
- **Heading hierarchy:** exactly one `<h1>` per page; subheadings follow logical order.
- **Interactive elements:** buttons are `<button>` with `type`; links use `<a>` with `href`. Both keyboard-accessible.
- **Meta / SEO:** pages have title, meta description and structured data where applicable. Nuxt: `useHead()` / `useSeoMeta()`.

## TypeScript

- **No `any`:** prefer `unknown` with narrowing, or a proper type/interface.
- **React props:** explicit interfaces on all component props — no implicit `{}` or untyped spreads.
- **Vue props:** `defineProps<{ foo: string }>()` with generics; `withDefaults()` for optional props. `defineEmits<{ change: [value: string] }>()`.
- **API responses:** typed via Zod or an explicit interface — not cast with `as`.
- **Discriminated unions** for async state: `{ status: 'loading' } | { status: 'success'; data: T } | { status: 'error'; error: string }`.
- **Strict mode:** `tsconfig.json` must have `"strict": true`. Flag if missing.

## Testing

- **Test behavior, not implementation:** assert what the user sees — not internal state.
- **React:** `@testing-library/react`. Prefer `getByRole` / `getByLabelText` over `getByTestId`. `findBy*` / `waitFor` for async.
- **Vue:** `@testing-library/vue` or Vitest + `@vue/test-utils`. Prefer `getByRole` over `wrapper.find('.class')`.
- **Snapshots:** only for intentionally stable UI primitives — not logic components.
- **Coverage goal:** critical user flows (auth, form submission, data loading states) over 100% line coverage.
- **Tests:** a behavior the diff adds or changes that no test asserts through what the user sees, or an edge it handles — an empty list, a loading or error state, a disabled control — that no test reaches, is 🟠 Major when the change introduced the path, 🟡 Minor when it only widened one that existed; name the assertion to add, the tag being `[tests]`. A test that would still pass with the change reverted is the same finding, and so is what [test quality](test-quality.md) bands — a test that cannot fail on the behavior it names, a bug fix with no test that fails without it, a flaky construct, a test left asserting superseded behavior — under its scope line's legacy exemption. Shape is [code style](code-style.md)'s Tests section and takes 🔵 Style, the band the scale gives every code-style breach, assigned to the style-reviewer when the calling workflow separates that pass.

## Comments

Comment findings follow the policy in `<package>/standards/review-protocol.md`. Here they take 🔵 Style.

## Scope guardrails

- Review only the diff or the files explicitly requested; do not audit the tree. Referenced but unchanged files are context, and a contradiction between a changed file and one it references is in scope.
- Skip generated and vendored trees: lockfiles, `node_modules/`, `dist/`, `.next/`, `.nuxt/`, compiled and minified assets.
- Past ~50 files or ~2,000 changed lines, take the highest-risk first and say which were left out. Never silently truncate.

## Execution

**Step 1 — Resolve scope**

Follow the scope and comparison rules in `<package>/standards/review-protocol.md`.

**Step 2 — Architectural summary**

2–3 sentences: what changed, which layers are affected (components / composables / hooks / pages / styles / config), risk level (low / medium / high).

**Step 3 — Verify in context**

Issue every independent read, grep and glob in one call — serialized single-file reads are the slowest part of this review, and nothing here needs one file's contents to choose the next. Read around each suspicious hunk, not just the diff, and confirm the issue is reachable and not already mitigated by the framework, a library or the surrounding code. Stop once every finding you intend to write has a line you can cite; a finding you cannot point at a line for is not a finding. Drop what does not survive, silently.

**Step 4 — Numbered findings**

Number only what is actionable and has real impact.

````
**[N] Severity | File: path/to/file.tsx (line X)**
What is wrong and why it matters.

```suggestion
// corrected code here
```
````

🔴 **Critical** — XSS, exposed secret, broken behavior, accessibility blocker; fix before merge. 🟠 **Major** — re-render bug, missing type safety, bad practice, layout regression risk; fix before merge unless explicitly accepted. 🔵 **Style** — a breach of `<package>/standards/code-style.md`: comments, naming, tests, imports; fixed in the same run, never a reason for another pass. 🟡 **Minor** — style, micro-optimization, missed abstraction; address if easy. 🟢 **Trivial** — formatting nit; omit if noisy.

**Rules:**
- Open with a verdict line — `1 Critical, 3 Major, 1 Style, 2 Minor` — then the findings; past 10, add a one-line index.
- Number from 1, ordered 🔴 → 🟠 → 🔵 → 🟡 → 🟢. One issue per number, except that comment findings in one file share a number, as do 🟢 nits in one file.
- Every 🔴 and 🟠 carries a code snippet or a concrete pointer; paths and line numbers accurate relative to the diff.
- No duplicate findings for one root cause, no preamble, no restatement of the task, no closing summary.
- No material issues → say so briefly and name one or two things done well.

## Conventions

- Components > 200 lines should be split; React: extract stateful logic into custom hooks.
- Next.js App Router: `"use client"` only for interactive elements; Server Components fetch data by default.
- Vue/Nuxt: Composition API with `<script setup>`; never destructure a `reactive()` object.
- State management (Vue): Pinia, state mutated only inside store actions.
- CSS Modules or scoped styles; design tokens (CSS variables) over hardcoded hex/pixel values.
- `dangerouslySetInnerHTML` / `v-html` require DOMPurify or equivalent, no exceptions.
- No `any` — `unknown` with narrowing or a proper type; explicit prop interfaces, no untyped spreads.
- `tsconfig.json` must have `"strict": true`.
- Test behavior, not implementation — `@testing-library`, prefer `getByRole` over `getByTestId`/`wrapper.find`.
- Every behavior the change adds or alters gets a test through what the user sees, its states included; a test that passes with the change reverted proves nothing.
- No whole-library imports when only a utility is needed; dynamic import for modals, heavy charts, off-screen content.
