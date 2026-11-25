# Test quality

<!-- flow:header -->
Scope is the tests the change touches and the tests the change owes; a green run reported elsewhere does not replace a test that can fail.

These rules and the review skill's Tests category do not apply to legacy code: a layer the project's test tooling does not reach — a legacy `lib/` layer, or a project carrying no test suite at all. A missing test in such a layer is a reported limitation, never a finding; a test the change does write or change there still answers to the sections below. Name the exemption in the review's limitations rather than dropping it silently.

Each section's opening band line is its band, and a finding matching no clause of it is Minor. Shape and writing rules are [code style](../../../references/code-style.md)'s Tests section and stay 🔵 Style; a construct that breaches both is reported once, under the band here.

## Substance

Major when a test cannot fail on the behavior it names, asserts a value the tested code's own logic produced, or confirms a double's configuration instead of the unit's behavior; Minor when an assertion is weaker than the behavior the test's name claims.

- A test asserts the behavior its name claims, through the public surface the caller uses, and fails when that behavior breaks — reasoned statically: the review is read-only, and running, reverting or mutating is not its evidence
- The expected value is independent of the code under test: a literal, a table row, a hand-computed constant — never the same logic rerun, a mirror call, or the double's configured return
- A test of a guard asserts the guarded failure — the error, the rejection, the empty result — not merely that no panic occurred
- Boundaries are cases, not commentary: empty and single-element input, the limit and the limit plus one, the smallest and largest accepted value, the exact error boundary
- A double stands in for a collaborator the unit does not own; the unit's own logic is never doubled — a mock of the unit under test tests the mock
- An assertion lives where the test runs it: an assertion inside a helper the test never calls pins nothing

## Bug fixes

Major when a bug fix the change carries has no test that can fail without it.

- A fix arrives with the test that reproduces the bug: without the fix it fails, with it it passes; the reviewer checks statically that the test reaches the fixed path with the reported input or an equivalent one and asserts the corrected outcome
- A regression test that cannot have failed before the fix pins nothing; it is the same finding as a missing test

## Isolation

Major when a test's outcome depends on timing, ordering or shared mutable state — a sleep synchronizing threads, a wall-clock or randomness read directly, a fixture mutated by an earlier test, a network call the suite does not own; Minor when the same risk is fully owned by the framework's fake-time or injection facility.

- Each test stands alone: the setup it depends on is its own, and no test leaves state a later test reads
- Time, randomness and I/O arrive injected or stand in through the language's fake-time facility; a bare sleep synchronizes nothing

## Green suite

Major when the change leaves behind a test asserting superseded behavior, or a skip or expected-failure marker without a named cause; Minor when a skipped case names its cause and what restores it.

- The suite the change leaves behind passes: a test still asserting the old behavior is a failing test committed as passing
- A skip or expected-failure names the reason and the condition that restores it; a bare skip is a hole in the suite
