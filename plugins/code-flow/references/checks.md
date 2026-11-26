# Checks

Discovery and execution of a repository's own checks for a workflow that runs them.

## Discovery

- Run a known command directly through the execution wrapper below when its result answers in a few lines.
- Anything whose output cannot be predicted, or that must be discovered at all, goes to the bundled `tool-runner` role, which finds the project's own commands: a `Makefile`, `package.json` or `composer.json` scripts, CI configuration, a `check`/`check.sh` script at the repository root.
- Two sources are known ahead of discovery, checked in order:
  1. the first `## Checks` section found in the repository's instruction files — its `CLAUDE.md`, then its `AGENTS.md`. Its lines read `- <repo>: <command>`, where `<repo>` is a repository's root directory name and `.` names the repository the workflow runs in. A listed command is the known command for that repository, run from its root.
  2. a repository that no line of the section lists falls through to `check`/`check.sh` at its root.
- An instruction that names `tool-runner` outright overrides this order.

## Execution

- Resolve the Python interpreter before launching Python-based checks. Honor repository version requirements and use the newest available supported interpreter; do not assume `python3` resolves to it. Preserve the selected executable in the exact command argv.
- A formatter invoked over multiple explicit paths can lose its own config discovery: `php-cs-fixer fix <path>...` without `--config` exits 16 (`For multiple paths config parameter is required`). A scoped multi-path formatter argv carries the repository's config flag explicitly (`--config=.php-cs-fixer.dist.php` when that file exists) instead of relying on auto-discovery.
- Direct and delegated executions use `<package>/scripts/run-check.py` from the resolved package. Supply the repository root as `--cwd` and the exact command argv after `--`:

  ```sh
  python3 "<package>/scripts/run-check.py" --cwd "/path/to/repository" --timeout 300 -- bash check.sh
  ```

- `./check` and a `## Checks` command use the same wrapper. Preserve arguments as individual argv entries; commands requiring shell syntax must explicitly use `bash -c` followed by the supplied shell command as one argument. The wrapper does not interpret shell syntax.
- The default timeout is 300 seconds; any caller-supplied timeout must be finite and positive. The wrapper closes stdin, sets `CI=1`, `NO_COLOR=1`, `TERM=dumb`, and `PAGER=cat`, and writes combined stdout/stderr to a unique temporary log. On Unix it kills the child process group on timeout and any surviving descendants after the leader exits, preserving the leader's actual exit status. Other systems terminate the immediate child on timeout. Poll a yielded execution session until completion; polling does not replace the wrapper's timeout.
- Any executed check argv must be non-interactive — the caller supplies its non-interactive form — and a discovered entry point that prompts is a reported blocker with the same stop semantics as missing tooling; the wrapper remains the enforcement mechanism for executed checks.
- Save the JSON result: `command` is the argv array, `cwd` is the resolved directory, `exit_status` is the actual child return code (negative for a Unix signal, null if no child result), `timed_out` identifies a timeout, and `log_path` names the complete output. Wrapper exit 0 means success, 1 means a failed or timed-out check, and 2 means usage or launch failure with an `error`. The wrapper's exit code is not the child's exit status; a timeout remains a timeout regardless of the child's final return code.
- On failure or timeout, send `tool-runner` the saved JSON result and existing log path in **log-analysis mode**. It reads that output and reports failing entries without executing the command again or discovering a replacement command. A fresh execution requires an explicit caller instruction after a fix or a justified transient failure. Missing or unreadable logs are reported as unavailable evidence, never reconstructed by rerunning.

## Selection

- The coordinator selects checks; `tool-runner` only discovers commands for a named suite and executes what it is given, validating reuse evidence before accepting it.
- Selection proceeds in order:
  1. checks explicitly required by repository instructions or the accepted plan — acceptance, security, migration, packaging, and project-mandated checks run even when broad;
     - Full suites in an accepted plan's `## Verification` section belong to the full-suite and integration tier. Full suites that repository instructions require also belong to that tier. A step's declared `test` is not part of that tier. It still runs in its per-step run, as `<package>/skills/plan-do/SKILL.md` requires. Every check run except the last readiness gate leaves out the full-suite and integration tier. Only that last readiness gate runs that tier. The excluded runs are per-step runs, the plan-do completion run, and each run of closeout Run step 3. Those runs use the narrowest checks from item 2. See `## Reuse within a run` for the once-only rule.
  2. the narrowest available checks covering the changed behavior;
  3. integration or full-suite checks only when changed paths cross component boundaries, coverage cannot be mapped confidently, shared workflow or configuration contracts change, or a required gate says so.
- Coverage uncertainty resolves by progressive expansion: from the narrowest plausible suite to broader suites, with the full suite only as the last resort.
- A documentation-only change requires link, path, and workflow-invariant checks when those exist, with no unrelated runtime suite. Tests that merely assert prose do not count as behavior coverage.
- Scoped plan, spec, and documentation artifacts map to their validators: plan files under `plans/` in a repository whose root has no `openspec` directory, other than archived plans as defined by the terminal-evidence exclusion rule in `<package>/references/closeout.md`, are validated per file from the repository root with `<package>/skills/plan-new/scripts/plan.py validate <plan>` and `<package>/skills/plan-new/scripts/plan.py anchors <plan>`; scoped files under `openspec` run the repository's spec validation — the `openspec` CLI's validate command when installed, else the spec check the repository instruction files declare; scoped documentation files keep the link, path, and workflow-invariant rule above, and when the repository declares no such check the coordinator verifies each repository-relative path reference and local link in the scoped documentation files, recording method and result as a mechanical check. These artifact validations are selected checks under this reference's execution, reuse, and failure rules; missing tooling stays a reported limitation rather than a silent skip, and no unrelated runtime suite is added to cover them.

A lockfile excluded from review per the generated-path exclusion rule in `<package>/references/closeout.md` maps to its package manager's own lockfile-consistency argv in the table below, selected and executed like any other selected check under this reference's execution and reuse rules; offline execution is per tool — a noted argv consults the registry or cache on a miss instead. A generated path the table does not cover has no consistency command, and the coordinator records the exclusion and the path hash as a reported limitation instead.

| Lockfile | Consistency argv | Notes |
| --- | --- | --- |
| `composer.lock` | `composer validate --no-check-publish` | |
| `package-lock.json` | `npm install --package-lock-only --dry-run --no-audit --no-fund` | consults the registry or cache on a miss |
| `yarn.lock` | `yarn install --immutable` | consults the registry or cache on a miss |
| `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` | consults the registry or store on a miss |
| `bun.lockb` | `bun install --dry-run` | consults the registry or cache on a miss |
| `Cargo.lock` | `cargo metadata --locked --offline` | |
| `go.sum` | `go mod verify` | |
| `poetry.lock` | `poetry check --lock` | |
| `Pipfile.lock` | `pipenv verify` | |
| `uv.lock` | `uv lock --check --offline` | |

## Reuse within a run

- A workflow run is one plan-do or spec-flow invocation, or one top-level wrap-up invocation. Each change under spec-batch is its own run. A run includes its nested close-out calls and spec-flow's nested wrap-up.
- Reuse only a recorded passing result from that run when the exact command argv, resolved repository root, hashes of relevant inputs, and required environment all match. `<package>/skills/plan-new/scripts/run_state.py` enforces the evidence schema at write time: `record`, `close`, and the `recover` done path reject a passed check entry lacking `argv`, `cwd`, `exit_status`, `log_path`, or `input_hashes` with `incomplete_check_evidence`, so a recorded passing result always carries those fields. Whether they match the current run stays coordinator judgment, carried in the handoff and validated by the receiver before reuse. Missing or uncertain evidence means rerun the narrowest applicable check.
- A worker's recorded passing result is reusable evidence on merge when the post-merge working tree reproduces the recorded input hashes per-file, plus the exact `argv`, `cwd`, and required environment; the coordinator verifies the hashes instead of rerunning and still reruns anything stale or uncertain. A result recorded from an isolated replica keeps the replica path as its `cwd`: reuse never requires that path to equal the coordinator's checkout path, because per-file input-hash equality after the merge is the identity test this rule applies. Failed or timed-out worker results are never reusable.
- This section owns the once-only rule. The coordinator runs the full-suite and integration tier once per run. The tier runs at the last readiness gate before a ready result or authorized commit. By default, that gate is the wrap-up entrypoint's. A delegated result never replaces the coordinator's tier execution. If a later edit in the same run invalidates a tier result that Selection requires, the tier runs once more. That second tier execution is at the last readiness gate after the edit. In spec-flow, that gate is the check run in step 3 item 2. If that rerun fails, the run stops and reports the evidence. If a later edit makes the rerun result invalid, the run also stops and reports the evidence. No third run of the tier occurs.
- Invalidate a result on any relevant edit, failure, timeout, or unavailable log. A failed check stays failed: saved-log diagnosis never reruns the command automatically.
