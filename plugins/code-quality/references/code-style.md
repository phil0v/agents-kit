# Code style

`STYLE-CODE-STYLE-1` Scope is the lines the change touches; untouched code stays untouched.

`STYLE-CODE-STYLE-2` Every section here is 🔵 Style; a review suggests a concrete correction, the implementation workflow owns edits, bandable rules belong in code-design or the review skill's category table.

`STYLE-CODE-STYLE-3` Mechanically gated families are named in the verified gate report of the review workflow's style gate; a reviewer holding a verified gate report skips exactly those families.

## Comments

- `STYLE-COMMENTS-1` No comment by default; only non-obvious runtime behavior at the line it sits on earns one
- `STYLE-COMMENTS-2` Not earned: restating the code, paraphrasing the signature (doc comments included), a guarantee living in other code, a banner, TODO or changelog note, commented-out code, design rationale, a cross-reference
- `STYLE-COMMENTS-3` Earned: translating a stored value into the human one it carries (`// 8 MiB` on `1 << 23`); a constant, limit, default or validation justification is not
- `STYLE-COMMENTS-4` A linter directive (`//nolint`, `# noqa`) is not a comment; its cause is earned when not obvious
- `STYLE-COMMENTS-5` A docblock tag earns its place only where the type system cannot express it — array shapes, generics, thrown types — or a linter demands one
- `STYLE-COMMENTS-6` The Comments rules apply in every file type: Makefile, YAML, Dockerfile, SQL, dotfiles

## Naming

- `STYLE-NAMING-1` A name says what the value means to its caller, not how it is stored or computed
- `STYLE-NAMING-2` Booleans read as assertions, functions as verbs, collections as plurals; no abbreviation the domain does not already use

## Tests

- `STYLE-TESTS-1` Go tests are table-driven: `[]struct` cases with `t.Run(tt.name, ...)`
- `STYLE-TESTS-2` A test pins one behavior through the public surface
- `STYLE-TESTS-3` Deterministic: no sleeps, wall clock, network or order dependence — time, randomness and I/O are injected or faked (`testing/synctest`)
- `STYLE-TESTS-4` No logic in a test: a loop or branch deciding the expectation belongs in the table
- `STYLE-TESTS-5` A fake asserts the arguments it receives; one that ignores them pins nothing
- `STYLE-TESTS-6` A test on a prose file (skill, agent, command, stage, reference) pins an invariant, not a quoted clause

## Imports

`STYLE-IMPORTS-1` Mechanically gated: covered by `goimports` per the `RULE_FAMILY_COVERAGE` table.

- `STYLE-IMPORTS-2` Go imports come in four blank-line-separated blocks, in order: standard library, public third-party, the organization's own private modules, then this module's packages; no repeats
