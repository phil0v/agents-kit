# agents-kit

Installable agent-plugin packages: a delegation workflow (`code-flow`), code
review standards (`code-quality`), local Git safety hooks (`local-guards`),
and a local usage recorder (`usage-metrics`), plus two standalone skills,
`retro` and `review-fix`. The `phil0v` marketplace lists all four packages in
[`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) for Claude
Code and Codex.

## Packages

| Package | Description |
|---|---|
| [`code-flow`](plugins/code-flow) | Coordinate implementation, independent close-out review, and OpenSpec change workflows with bundled agent resources and local edit checks |
| [`code-quality`](plugins/code-quality) | Portable code standards and focused backend, frontend, Symfony, and agent-configuration review |
| [`local-guards`](plugins/local-guards) | Local Git push protection and branch backups |
| [`usage-metrics`](plugins/usage-metrics) | Local usage recorder and report for plugin and workflow events |

## Skills

Each package ships its skills in its `skills/` directory. Two standalone
skills ship in the root `skills/` directory. Every skill is a folder with a
`SKILL.md` that states its arguments (`argument-hint`), required steps, and
stop conditions.

| Skill | Package | Purpose |
|---|---|---|
| [`plan-new`](plugins/code-flow/skills/plan-new/SKILL.md) | `code-flow` | Create and independently review an implementation plan |
| [`plan-do`](plugins/code-flow/skills/plan-do/SKILL.md) | `code-flow` | Execute a reviewed plan from durable state and close out through `wrap-up` |
| [`plan-revise`](plugins/code-flow/skills/plan-revise/SKILL.md) | `code-flow` | Amend remaining work in an accepted plan |
| [`spec-new`](plugins/code-flow/skills/spec-new/SKILL.md) | `code-flow` | Create one OpenSpec change; planning artifacts only |
| [`spec-flow`](plugins/code-flow/skills/spec-flow/SKILL.md) | `code-flow` | Implement one OpenSpec change, verify it, review it, and commit it |
| [`spec-list`](plugins/code-flow/skills/spec-list/SKILL.md) | `code-flow` | List active OpenSpec changes; set change metadata |
| [`spec-batch`](plugins/code-flow/skills/spec-batch/SKILL.md) | `code-flow` | Run several named OpenSpec changes through `spec-flow` |
| [`wrap-up`](plugins/code-flow/skills/wrap-up/SKILL.md) | `code-flow` | Finish a change with one combined review round and an authorized commit |
| [`project-rules`](plugins/code-flow/skills/project-rules/SKILL.md) | `code-flow` | Install or refresh repository-local project rules |
| [`code-reviewer`](plugins/code-quality/skills/code-reviewer/SKILL.md) | `code-quality` | Severity-banded code review for backend, frontend, Symfony, agent configuration, and security |
| [`setup`](plugins/local-guards/skills/setup/SKILL.md) | `local-guards` | Check dependencies and duplicate local hooks before you enable `local-guards` |
| [`metrics-report`](plugins/usage-metrics/skills/metrics-report/SKILL.md) | `usage-metrics` | Analyze the local usage log: invocations, durations, review rounds, dispatches |
| [`retro`](skills/retro/SKILL.md) | standalone | Analyze a session for improvements to skills and workflows |
| [`review-fix`](skills/review-fix/SKILL.md) | standalone | Run a read-only review with `code-reviewer`, fix every finding, and re-review the fix diff |

## Installation

Requirements: `git`, `bash`, `jq`, `python3` (3.9+), and the CLI of your
client. Clone the repository to any directory:

```sh
git clone https://github.com/phil0v/agents-kit.git
```

Run every command below from the clone root; paths are relative to it.

Set `AGENTS_HOME` to your clone path. Plugin-installed copies load the recorder
from `$AGENTS_HOME/plugins/usage-metrics/`, and the usage log is
`$AGENTS_HOME/metrics/usage.jsonl`. Without `AGENTS_HOME`, both default to
`~/.agents`.

### Claude Code

```sh
claude plugin marketplace add phil0v/agents-kit
claude plugin install code-quality@phil0v
claude plugin install code-flow@phil0v
claude plugin install local-guards@phil0v
claude plugin install usage-metrics@phil0v
```

Confirm the guard hooks through `/hooks`.

### Codex

Add the marketplace from GitHub:

```sh
codex plugin marketplace add phil0v/agents-kit
```

With a local clone, add its path instead:
`codex plugin marketplace add .` from the clone root.

Install the hook and recorder packages. Codex asks for hook trust on the
first `code-flow` and `local-guards` use. The `code-flow` plugin adds only
edit checks; the `code-flow` and `code-quality` skills come from the
[skills directory](#skills-directory).

```sh
codex plugin add code-flow@phil0v
codex plugin add local-guards@phil0v
codex plugin add usage-metrics@phil0v
```

### Pi

Pi requires Node.js 22.19+. Install the dependency first, then the repository
as one package:

```sh
pi install npm:pi-subagents
pi install git:github.com/phil0v/agents-kit
```

With a local clone, install its root instead: `pi install .`. The root
`package.json` lists the skills of all packages and the standalone skills, the
`code-flow` roles, and the `local-guards` hooks extension. Do not also link the
same skills into the [skills directory](#skills-directory): Pi would load two
copies.

### Antigravity

`agy plugin install` takes a directory only and copies each plugin, so run the
commands again after `git pull`. Live registration through
`~/.gemini/config/plugins.json` avoids the copies.

```sh
agy plugin install ./plugins/code-quality
agy plugin install ./plugins/code-flow
agy plugin install ./plugins/local-guards
agy plugin install ./plugins/usage-metrics
```

### Skills directory

Pi, Delta, and the Codex USER scope read `~/.agents/skills/`. This repository
does not write to that directory. From the clone root, link each skill that your
client needs into it, for example a package skill and a standalone skill:

```sh
mkdir -p ~/.agents/skills
ln -sfn "$PWD/plugins/code-flow/skills/plan-do" ~/.agents/skills/plan-do
ln -sfn "$PWD/skills/retro" ~/.agents/skills/retro
```

### Other Agent Plugins clients

Each package contains a portable `plugin.json` manifest at its root. Use the
client's own installation and discovery mechanism, then verify resource access
and runtime capabilities separately.

Per-harness capability boundaries, update commands, and uninstall steps are in
[`docs/install.md`](docs/install.md).

## Repository layout

| Path | Contents |
|---|---|
| [`plugins/`](plugins) | The four installable packages |
| [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) | Marketplace manifest for Claude Code and Codex |
| [`skills/`](skills) | The standalone `retro` and `review-fix` skills |
| [`docs/install.md`](docs/install.md) | Installation runbook: capability boundaries, update, and uninstall steps |
| [`tests/`](tests) | Marketplace and package packaging tests |
| [`Makefile`](Makefile) | Verification entry points: `make test`, `make check` |
| [`AGENTS.md`](AGENTS.md) | Repository instructions for coding agents |
| [`LICENSE`](LICENSE) | MIT license |
| [`package.json`](package.json) | Pi package: the whole repository installs as one package |

## Usage

- **Skills** run inside your agent. Select one by name and pass arguments per
  its `argument-hint`, for example `/metrics-report` or
  `/plan-do plans/example.md --no-commit`. A typical change runs
  `plan-new` → `plan-do` → `wrap-up`, or `spec-new` → `spec-flow` in
  OpenSpec repositories.
- **Guards** run as hooks with no invocation: `git push` is blocked, branch
  rewrites write a `refs/backup/<branch>-<timestamp>` ref first, JSON and
  TOML config writes are syntax-checked, and formatters and linters run
  after edits.
- **Usage metrics** record one JSON line per hook run and script invocation
  to `$AGENTS_HOME/metrics/usage.jsonl`.
  Records stay on the machine and carry metadata only. Disable with
  `AGENTS_METRICS=off`. Read the report:

  ```sh
  python3 ./plugins/usage-metrics/scripts/metrics-report.py
  ```

  The `metrics-report` skill runs this report and reads its output.
- **Tests** run per affected root:

  ```sh
  make test-root
  make test-package PACKAGE=code-flow
  ```

  `make test` runs every suite. `make check` adds ruff, the manifest and link
  checks, the agent-form sync check, and `claude plugin validate` when the CLI
  is installed. `make help` lists every target. Install the test dependencies
  once with `python3 -m pip install -r requirements.txt`.

## License

[MIT](LICENSE)
