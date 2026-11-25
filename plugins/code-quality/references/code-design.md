# Code design

`DESIGN-CODE-DESIGN-1` Scope is the lines the change touches; untouched code stays untouched.

`DESIGN-CODE-DESIGN-2` Each section's opening band line is its band; no matching clause is Minor. Style rules belong in code-style.

## Architecture

`DESIGN-ARCHITECTURE-1` Critical: interpolated text reachable from input at a sink; Major: wrong-way dependency, skipped layer, unvalidated boundary input; Minor: bucket package, one-caller abstraction.

- `DESIGN-ARCHITECTURE-2` Dependencies point one way, toward the domain
- `DESIGN-ARCHITECTURE-3` An interface is declared where it is used
- `DESIGN-ARCHITECTURE-4` A layer talks to the layer below it, not past it
- `DESIGN-ARCHITECTURE-5` Modules are cut by feature, not by technical kind
- `DESIGN-ARCHITECTURE-6` An abstraction earns its place with the second caller
- `DESIGN-ARCHITECTURE-7` Two or three short functions live in the file that calls them
- `DESIGN-ARCHITECTURE-8` Unexported by default; exported only for a real caller
- `DESIGN-ARCHITECTURE-9` A published contract changes by addition; removal or rename waits for zero callers
- `DESIGN-ARCHITECTURE-10` A field arrives with its reader
- `DESIGN-ARCHITECTURE-11` No circular dependencies: the shared part moves down
- `DESIGN-ARCHITECTURE-12` State has one owner; everything else derives it or receives it
- `DESIGN-ARCHITECTURE-13` A value two paths both need has one definition both read
- `DESIGN-ARCHITECTURE-14` Dependencies arrive through the constructor as values
- `DESIGN-ARCHITECTURE-15` Validate at the boundary; code inside trusts its own types
- `DESIGN-ARCHITECTURE-16` Nothing crosses a sink as interpolated text; the sink's own tool parameterizes or escapes SQL, shell, HTML and URLs

## Isolation

`DESIGN-ISOLATION-1` Critical: a transaction nested or opened inside a repository on a write path; Major: forced outside change, unowned opened resource, side effect below the edge; Minor: more than four collaborators.

- `DESIGN-ISOLATION-2` A module hides its internals behind a narrow contract
- `DESIGN-ISOLATION-3` A change inside a module never forces a change outside it
- `DESIGN-ISOLATION-4` Few collaborators: more than four collaborators is holding work that belongs to whoever owns the data
- `DESIGN-ISOLATION-5` A component owns what it opens: files, connections and goroutines are closed by whoever created them
- `DESIGN-ISOLATION-6` A transaction spans one use case, never inside a repository and never nested
- `DESIGN-ISOLATION-7` Side effects stay at the edges of the system
- `DESIGN-ISOLATION-8` A timeout, budget or margin is derived from the whole unit it bounds

## Errors

`DESIGN-ERRORS-1` Critical: an error swallowed on a write or respond path; Major: swallowed or prose-parsed elsewhere, a dependency's error type crossing the module; Minor: context wrapped twice.

- `DESIGN-ERRORS-2` An error is never swallowed — handle it, wrap it, or let it propagate; a bare `Close` may be discarded
- `DESIGN-ERRORS-3` Wrap with context once, at a boundary; the message names the operation and its input
- `DESIGN-ERRORS-4` An error carries a value the caller can branch on — a type or a sentinel, never prose or a dependency's type
- `DESIGN-ERRORS-5` A pattern that can miss fails naming what it expected at the line it reads

## Simplicity

`DESIGN-SIMPLICITY-1` Major: an unmeasured cache, pool, batch or index on a hot path, an unpinned image, a pass-through layer on a public surface; Minor: any other breach here.

- `DESIGN-SIMPLICITY-2` The simplest code that solves today's problem wins
- `DESIGN-SIMPLICITY-3` An option, flag or configuration entry arrives with the request that asked for it
- `DESIGN-SIMPLICITY-4` An optimization earns its place with a measurement
- `DESIGN-SIMPLICITY-5` Two functions, not one: more than two sentences to explain, or a boolean parameter splitting the body
- `DESIGN-SIMPLICITY-6` Guard clauses first: no `else` after a `return`; nesting past two levels is extracted
- `DESIGN-SIMPLICITY-7` Branching stays countable: unenumerable paths split into named predicates and functions
- `DESIGN-SIMPLICITY-8` Unused code gets deleted, not kept
- `DESIGN-SIMPLICITY-9` A language interface type with one implementation is a concrete type
- `DESIGN-SIMPLICITY-10` A generic type or function appears with the second concrete type
- `DESIGN-SIMPLICITY-11` No pass-through layer: the caller calls the target
- `DESIGN-SIMPLICITY-12` A pattern is named after the fact: a `switch` before a strategy, a struct literal before a builder, a function before a factory, a map before a registry
- `DESIGN-SIMPLICITY-13` No defensive code past the boundary: no re-validation, no impossible errors or states
- `DESIGN-SIMPLICITY-14` A language feature or the standard library before a helper, a helper before a dependency
- `DESIGN-SIMPLICITY-15` A container image is pinned by a full version tag, such as `postgres:16.4-alpine`, never a digest, floating tag or none

## Go

`DESIGN-GO-1` Major: a goroutine with no stop or owner, a receiver-closed channel, `init()` or package-level mutable state; Minor: an idiom below the `go.mod` floor. Unchecked `Close` takes the Errors band.

`DESIGN-GO-2` Read [Go conventions](golang.md) whenever the change touches `.go` files or `go.mod`.
