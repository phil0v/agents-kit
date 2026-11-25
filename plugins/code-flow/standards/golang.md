# Go

`gofmt`, `go vet` and the linter own what this section leaves out.

- The `go` line in `go.mod` is the real minimum, and every API this section names is used where that line allows it and its older form otherwise: `for i := range n` (1.22), `min`/`max`/`clear` (1.21), `slices`/`maps` (1.21) and `iter` (1.23) over hand-written loops, `cmp.Or` (1.22) for defaults, `errors.AsType` (1.26) over `errors.As`, `sync.WaitGroup.Go` (1.25) over `Add`/`Done`, `json:",omitzero"` (1.24) over pointer fields where absent and zero need not differ, `testing/synctest` (1.25), the `tool` directive (1.24), `any` over `interface{}`
- `context.Context` is the first parameter, named `ctx`, never a struct field; below the boundary nothing creates its own `context.Background()` except a shutdown deadline
- No `init()` and no package-level mutable state: a package exposes constructors, and what they build is passed in
- Zero values are usable: a type that is unsafe until constructed stays unexported, and a mutex sits unexported directly above the fields it guards
- Receivers are one kind per type — all pointer or all value — and struct literals name their fields
- `panic` is for a programmer error in `main` or a test; a library returns an error
- An error is matched with `errors.Is` or `errors.As` — `AsType` under the first bullet's gate — against an `ErrX` sentinel or an `*XError` type
- A goroutine has a way to stop — the `ctx` it takes or a channel its owner closes — and an owner that waits for it with `WaitGroup.Go` or `errgroup` before returning; `time.Sleep` synchronises nothing outside a `testing/synctest` bubble
- A channel is closed by its sender, and a `select` that waits also waits on `ctx.Done()`
- `Close` on something written to has its error checked, save the reader exception code-design's Errors section states
- `main` calls `run(ctx, args, getenv, stdin, stdout, stderr) error` and exits non-zero on error; `run` holds `signal.NotifyContext` and returns the error of what it runs: a service shuts its `http.Server` — `ReadHeaderTimeout` set — down on `ctx.Done()` with a deadline; a CLI or worker returns when `ctx` is done or its work ends
- `NewServer(deps...) http.Handler` builds the stdlib `ServeMux`, calls `addRoutes` and wraps the global middleware; `routes.go` holds every route as one `mux.Handle("METHOD /path/{param}", ...)` line, `r.PathValue` reads the parameter and `r.Pattern` labels the route — after `next.ServeHTTP` in global middleware, anywhere per route
- A handler is a maker func `handleX(deps) http.Handler` closing over exactly what it uses, its request and response types declared inside it — no server struct with handler methods, no `http.HandlerFunc` in the signature; middleware is `func(http.Handler) http.Handler`, returned by a constructor when it needs dependencies and applied per route in `routes.go` when not global
- JSON crosses the wire through one `encode`/`decode` pair, a request validating itself with `Valid(ctx) map[string]string` before the handler touches it and the status written once the body is known to encode; an error reaches the client through one mapping to status and body, never `err.Error()`; logging goes through an injected `*slog.Logger`, never `log.Printf`
- A test of an HTTP service calls `run` or `NewServer` and speaks HTTP through `httptest`, never the handler func directly, with `t.Context()` and `t.TempDir()`, and `testing/synctest` from 1.25 standing in for the injected clock code-style's Tests section asks for where time is involved
- Tools the module builds with are `tool` directives in `go.mod`, not a `tools.go`
