# Installation

The GitHub repository `phil0v/agents-kit` hosts the `phil0v` marketplace with
the four packages: `code-quality`, `code-flow`, `local-guards`, and
`usage-metrics`. The `agy-agents` package carries the Antigravity agent
forms for `code-flow`; Antigravity installs it from a clone. Claude Code, Codex,
and Pi install from GitHub without a clone.
Antigravity, Empryo, Delta, and the `~/.agents/skills/` readers need a local
clone. Workflow execution also requires the harness capabilities below.

Prerequisites are `git`, `bash`, `jq`, and `python3` (3.9+). The config-file
syntax checks of the `code-flow` edit checks run on every supported Python
interpreter.
Supported clients are `codex`, `claude`, `pi` (Node.js 22.19+ for Pi), `agy`
(Antigravity CLI), `empryo`, and the Delta desktop app. From the clone root,
install the Python test dependencies once on each machine:

```sh
python3 -m pip install -r requirements.txt
```

## Install

### From GitHub

Claude Code and Codex add the marketplace from the GitHub repository, and Pi
installs the repository as one package. The install commands are in their
sections below:

```sh
claude plugin marketplace add phil0v/agents-kit
codex plugin marketplace add phil0v/agents-kit
pi install npm:pi-subagents
pi install git:github.com/phil0v/agents-kit
```

Install `usage-metrics` together with `local-guards` and `code-flow`: their
copies find the recorder in the same plugin cache. Without it, usage recording
needs `AGENTS_HOME`; see [Usage metrics](#usage-metrics).

### Local clone

Antigravity, Empryo, Delta, and the `~/.agents/skills/` readers read package
files from disk and install only from a local clone. Clone the repository to any
directory:

```sh
git clone https://github.com/phil0v/agents-kit.git
```

Run every command below from the clone root; paths are relative to it. Set
`AGENTS_HOME` to the clone path: the hooks and scripts read it to locate the
recorder and the usage log.

#### Harnesses that read `~/.agents/skills/`

Codex USER scope, Pi, and Delta read `~/.agents/skills/`; the package skills
need no plugin installation there. Skip these links for Pi when the repository
is installed as its Pi package: Pi would load two copies. Copying an
individual workflow directory
omits sibling skills, roles, references, and scripts, so keep the complete
clone. To expose the standalone `retro` and `review-fix` skills, the `code-flow`
workflow skills, the `code-quality` review skills, and the `usage-metrics`
report skill to these readers, link them into `~/.agents/skills/`:

```sh
mkdir -p ~/.agents/skills
for dir in skills plugins/code-flow/skills plugins/code-quality/skills \
           plugins/usage-metrics/skills; do
  for skill in "$dir"/*/; do
    ln -sfn "$PWD/${skill%/}" ~/.agents/skills/"$(basename "$skill")"
  done
done
```

Each client still requires compatible discovery, resource access, and
independent-agent capabilities for delegated workflows. Filesystem exposure
alone does not prove support for workflow execution.

### Codex

```sh
codex plugin marketplace add phil0v/agents-kit
codex plugin add code-quality@phil0v
codex plugin add code-flow@phil0v
codex plugin add local-guards@phil0v
codex plugin add usage-metrics@phil0v
```

`codex plugin marketplace list` shows whether the `phil0v` marketplace is
registered; the `add` command is safe to run again. With a local clone, add its
path instead: `codex plugin marketplace add .`. Codex reads
`.claude-plugin/marketplace.json` from either source. Codex asks for hook trust
on the first `code-flow` and `local-guards` use.
`usage-metrics` adds no hooks of its own. Install it as well: the hooks in the
Codex cache find the recorder in the sibling `usage-metrics` copy of the cache.

Codex scans `$HOME/.agents/skills/` as its USER scope and follows symlinked
skill directories. The `code-flow` workflow skills load globally from
`~/.agents/skills/` through the links above. The `code-flow` plugin
manifest registers only its hooks (`hooks/codex.json`): edit checks on
`apply_patch`. The `code-quality` plugin serves its `code-reviewer` skill
from the Codex cache; the package has no Codex hooks or agent forms. The
`code-flow` roles remain available through `~/.agents/skills/`. Workflows
dispatch them as resources from the package root (`roles/*.toml` through
the skills' `references/dispatch.md`), never as plugin-registered native
agents.

### Claude Code

```sh
claude plugin marketplace add phil0v/agents-kit
claude plugin install code-quality@phil0v
claude plugin install code-flow@phil0v
claude plugin install local-guards@phil0v
claude plugin install usage-metrics@phil0v
```

With a local clone, add its path as the marketplace instead:
`claude plugin marketplace add .`. Use one marketplace source.

Verify the guard hooks through `/hooks` in a session. Claude Code receives the
package skills through the plugins, so the package skills need no symlinks.
The standalone `retro` and `review-fix` skills belong to no plugin: link them
from the clone root:

```sh
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skills/retro" ~/.claude/skills/retro
ln -sfn "$PWD/skills/review-fix" ~/.claude/skills/review-fix
```

### Pi

Pi requires Node.js 22.19+. Install the dependency first, because Pi does not
resolve the dependencies of a Git or local source, then the repository as one
package:

```sh
pi install npm:pi-subagents
pi install git:github.com/phil0v/agents-kit
```

With a local clone, install its root instead: `pi install .`. The root
`package.json` lists the skills of all four packages and the standalone skills,
the `code-flow` roles, and the `local-guards` hooks extension. To install one
package alone, use its path: `pi install ./plugins/code-flow` or
`pi install ./plugins/local-guards`.

Do not also link the same skills into `~/.agents/skills/`: Pi scans that
directory at startup as an Agent Skills path and would load two copies. Run
`/reload` to refresh a running session. The legacy `skills`
list in `~/.pi/agent/settings.json` from the pre-package `wire.sh` flow points
into the same tree. You can remove that legacy list.

Two runtime paths switch model providers without regenerating the plugin or its
forms. Native roles switch through the pi-subagents user or project settings
layer, which overrides form frontmatter at runtime. Project settings override
user settings. The per-invocation
`/run <role>[model=<provider>/<model-id>:<level>]` override has the highest
priority. Place these keys under `subagents`:

```json
{
  "subagents": {
    "defaultThinking": "high",
    "agentOverrides": {
      "worker": {"model": "<provider>/<model-id>", "thinking": "high"}
    },
    "agentOverridesByProvider": {
      "<provider>": {
        "reviewer": {"model": "<provider>/<model-id>"}
      }
    }
  }
}
```

`agentOverridesByProvider` applies by the active parent-session provider.
Provider ids resolve against pi's provider registry, which combines the built-in
catalog and custom providers.

Workflow routing switches through the optional `provider` and `providers` keys
in `plugins/code-flow/harnesses/mapping.json` to select per-role models at
dispatch time. The `--provider <id>` flag on
`plugins/code-flow/scripts/role-profile.py` overrides the provider for one
resolution and is never saved. The committed mapping includes no provider keys.
Operators supply tables that contain subscription-specific model ids. See
`plugins/code-flow/references/dispatch.md` for selection and fallback rules.

### Antigravity

Antigravity (`agy` 1.5.14+) loads `code-quality`, `code-flow`,
`agy-agents`, `local-guards`, and `usage-metrics`
as copied installations or directly from the clone through
JSON configuration.

#### Copied plugin installation

`agy plugin install` takes a directory only. Install all four packages from the
clone:

```sh
agy plugin install ./plugins/code-quality
agy plugin install ./plugins/code-flow
agy plugin install ./plugins/agy-agents
agy plugin install ./plugins/local-guards
agy plugin install ./plugins/usage-metrics
```

`agy plugin install` copies each plugin into `~/.gemini/config/plugins/<name>/`.
After `git pull`, run those five `agy plugin install` commands
again to update the copied plugins. `local-guards` uses
`plugins/local-guards/.antigravity-plugin/plugin.json` and root
`plugins/local-guards/hooks.json`. Root `plugin.json` stays absent in
`local-guards`, so Codex keeps loading `.codex-plugin/plugin.json`. `code-flow`
ships its edit checks in root `plugins/code-flow/hooks.json` under the `code-flow`
key. The `agy-agents` package carries the six agent forms;
Antigravity discovers them through the package's `agents/` folder.
`usage-metrics` adds no agents or hooks of its own. Install it to keep the
recorder adjacent to the installed `local-guards` and `code-flow` copies. Its
`metrics-report` skill loads globally through the `~/.agents/skills/` links.

#### Live registration via `plugins.json` and `skills.json`

To load plugins directly from the clone without reinstalling after `git pull`,
register the clone's `plugins/` directory in `~/.gemini/config/plugins.json`.
Antigravity
scans each `entries` directory one level deep:

```json
{
  "entries": [
    { "path": "<clone-path>/plugins" }
  ]
}
```

Add `include_only` or `exclude` to the entry if you want only a subset of the
four plugins.

To register your own standalone skills from `~/.agents/skills`, configure
`~/.gemini/config/skills.json` and exclude the linked package skills.
Without the exclusion, Antigravity loads duplicate copies alongside the active
plugins:

```json
{
  "entries": [
    {
      "path": "~/.agents/skills",
      "exclude": [
        "code-reviewer",
        "metrics-report",
        "plan-do",
        "plan-new",
        "plan-revise",
        "project-rules",
        "spec-batch",
        "spec-flow",
        "spec-list",
        "spec-new",
        "wrap-up"
      ]
    }
  ]
}
```

If the plugins are installed or registered in `plugins.json`, do not copy those
plugin skills into `~/.gemini/skills/` or `~/.gemini/config/skills/`. Also do
not create symlinks to them there.

#### Discovery, resolution, and verification

Validate plugin manifests offline and inspect installed plugins:

```sh
agy plugin validate --strict ./plugins/code-quality
agy plugin validate --strict ./plugins/code-flow
agy plugin validate --strict ./plugins/agy-agents
agy plugin validate --strict ./plugins/local-guards
agy plugin validate --strict ./plugins/usage-metrics
agy plugin list --json
```

`agy plugin list --json` lists `code-flow` with 9 skills and `agy-agents`
with the six agents
(`worker`, `plan-reviewer`,
`spec-reviewer`, `reviewer`, `style-reviewer`,
`tool-runner`). Those definitions set `mainAgent: false`, so they do
not appear in `agy agents`.

Resolve `code-flow` role profiles for Antigravity. Add `--provider <id>` if you
use a local provider table:

```sh
python3 ./plugins/code-flow/scripts/role-profile.py --harness antigravity --role reviewer
```

Run a one-off read-only review subagent launch with `agy --print`:

```sh
agy --print "Invoke reviewer read-only on the scoped diff." --print-timeout 180s --output-format stream-json
```

In a live Antigravity 1.5.14 verification, `code-flow-reviewer` ran read-only
with `read_file`, `run_command`, `manage_task`, and `send_message`. It had no
`write_file` or `edit_file` access and ran `git --no-pager diff`.

`local-guards` and `code-flow` hooks verified live on Antigravity 1.5.14 while
both were shipped by `local-guards`:

- `PreToolUse` on `run_command` blocks `git push` and creates
  `refs/backup/<branch>-<timestamp>` backup refs before `rebase`, `merge`, or
  forced `branch` updates.
- `PreToolUse` on `write_file|edit_file` rejects invalid JSON or TOML config
  files (`config.toml`, `hooks.json`, `package.json`, `pyproject.toml`,
  `agents/*.toml`, `settings*.json`) before write.
- `PostToolUse` on `write_file|edit_file` runs `edit-checks.py --antigravity`
  for formatters and linters. It prints `{}` on `stdout`, exits `0`, and writes
  up to 12,000 characters of diagnostics to `stderr`. Antigravity 1.5.14 does
  not deliver `PostToolUse` `stderr` diagnostics to the agent. Formatters still
  run after writes, and `PreToolUse` denial reasons reach the agent.

The last two bullets are the edit checks, which now ship in
`plugins/code-flow/hooks.json`. Their `code-flow` registration is not verified
live.

### Empryo

Empryo (`empryo` 3.14.2-beta) registration installs only the six `code-flow-*`
agent definitions. Execution of read-only `code-flow` reviews is currently
blocked (see below).

#### Registration, update, and discovery

Register the six generated Empryo forms in `~/.empryo/agents/`:

```sh
python3 ./plugins/code-flow/scripts/install-empryo-agents.py
```

This command creates file symlinks in `~/.empryo/agents/` pointing to the
clone's `plugins/code-flow/harnesses/empryo/code-flow-*.md`. Existing symlinks
automatically reflect edits and `make sync` regenerations in the same
clone. Run `install-empryo-agents.py` again only if you add new roles.

Verify headless discovery without invoking a model:

```sh
empryo --headless --profile nonexistent-profile-check "probe"
```

The missing-profile output lists all six `code-flow-*` agents.

#### Stale links, collisions, and shadowing

`install-empryo-agents.py` runs a strict preflight and stops without modifying
files or registering a partial set. It refuses pre-existing non-matching files,
directories, dangling symlinks, or symlinks pointing to another target such as a
moved clone. Inspect and remove stale symlinks in `~/.empryo/agents/` manually
before running the installer again:

```sh
ls -la ~/.empryo/agents
```

Project-local `<project>/.empryo/agents/` definitions shadow `~/.empryo/agents/`
at higher priority.

#### Execution blocker

Documented support for Empryo is registration only. On Empryo 3.14.2-beta,
`code-flow-reviewer` runs in `mode: explore`. That mode denied the required
read-only `git --no-pager diff` command (`"shell is not available in explore
mode. Use report_finding to suggest changes instead."`). Do not use Empryo for
`code-flow` review execution until explore mode supports read-only shell access.

### Other Agent Plugins clients

Each package contains a portable `plugin.json` manifest at its root. Use the
client's own installation and discovery mechanism, then verify resource access
and runtime capabilities separately. A portable manifest does not register the
workflow's bundled TOML roles in an arbitrary client.

## Workflow installation boundaries

Keep the full package, including sibling skills, `roles/`,
`harnesses/`, `standards/`, `references/`, `scripts/`, skill-local helpers,
and `harnesses/mapping.json`. Antigravity dispatch additionally needs the
`agy-agents` package. Resolve the loaded
`SKILL.md` through symlinks before deriving its package root. The
[packaging tests](../tests/test_skills.py) verify relative links inside
relocated complete packages. The
[package tests](../plugins/code-flow/tests/test_package.py) cover a plugin
copy, separate global symlinks, script imports, and role resolution from
unrelated directories including paths with spaces. These tests verify packaging,
not a client's discovery or live dispatch.

| Harness / mode | Workflow source | Required execution capability and boundary |
| --- | --- | --- |
| Codex, global skills | `~/.agents/skills/` links into the full package; recommended setup above. | Generic fresh-agent dispatch uses the resolved Codex model, effort, and canonical body. A read-only reviewer handback ran in the implementation session. That observation does not verify every workflow or plugin discovery. |
| Codex, plugin only | A complete plugin copy contains skills and dependencies. | Verify this client's plugin discovery separately because the manifest registers no native roles. Generic dispatch remains required. Live discovery and execution remain unverified for this mode. |
| Claude, plugin | Plugin skills and generated Claude forms. | Verify loaded native profiles against effective Claude profiles for canonical bodies, models, effort, and tools. Live dispatch remains unverified. |
| Claude, global skills only | Client-specific skill registration is required; global exposure alone is insufficient. | If matching native roles are absent, a verified fresh-agent API must accept the complete effective Claude profile. An unavailable fallback blocks execution. This mode remains unverified. |
| Pi, root package | Skills of all packages, `code-flow` roles, `local-guards` hooks extension, and `pi-subagents`. | Verify loaded roles, mapped provider/models, `thinking` effort, and tools. Schema and launch-path inspection covers installed `pi-subagents` 0.84.0. Live role dispatch remains unverified. |
| Pi, roles package only (`pi install ./plugins/code-flow`) | The package manifest contributes roles, not skills. | Supply the `~/.agents/skills/` links or another verified skill-discovery source before invocation. Role installation alone does not establish discovery. Live execution remains unverified. |
| Delta, global skills plus resolver mapping | Complete package links in `~/.agents/skills/`; `--harness delta` resolves runtime-default profiles without forms. | Dispatch requires Delta's subthread spawn with role-matched profiles. A smoke dispatch verified profile selection, tool grants, and handback. Full workflow execution remains unverified. |
| Antigravity, plugin (`agy plugin install` or `plugins.json`) | Complete package skills, `local-guards` and `code-flow` hooks, and the six subagents of the `agy-agents` package (`--harness antigravity`). | A one-off `agy --print` launch with `code-flow-reviewer` ran read-only using `read_file`, `run_command`, `manage_task`, and `send_message`, with no `write_file` or `edit_file`. `local-guards` `PreToolUse` hooks blocked `git push` and wrote branch backup refs; edit checks validated config files before they moved to `code-flow`. `PostToolUse` `stderr` diagnostics do not reach the agent in Antigravity 1.5.14. Full delegated workflows remain unverified. |
| Empryo, agent symlinks (`~/.empryo/agents/`) | File symlinks to `plugins/code-flow/harnesses/empryo/code-flow-*.md`. | Registration and headless discovery passed for all six `code-flow-*` agents. Execution is blocked on Empryo 3.14.2-beta because `code-flow-reviewer` (`mode: explore`) denies shell execution for `git --no-pager diff`. Empryo remains unlisted in `harnesses/mapping.json`. |
| Other skill readers / portable plugins | Reader-specific discovery plus the full package closure. | A verified adapter must start fresh independent agents, select required models and supported effort, and return complete handbacks. It must also apply canonical instructions and declared tools. A missing required capability blocks execution, and unavailable probes remain unverified. |
| Individual workflow directory | Discoverable skill text may exist without its package. | This layout is unsupported because sibling workflows, roles, references, or scripts are missing. A broken skill link cannot resolve the skill. |

Use one workflow source per harness. For Codex and Delta, prefer the
`~/.agents/skills/` links described above. For Pi, prefer the root package. For
Claude and Antigravity, use
plugin workflows and expose other skills separately. For Antigravity,
exclude plugin skills from `~/.gemini/config/skills.json` and keep them out of
`~/.gemini/skills/` and `~/.gemini/config/skills/`. If both plugin and global
copies are exposed, inspect the selected skill's absolute source and loaded
role profiles. Duplicate discovery, name precedence, and override behavior
remain unverified. Matching names do not prove matching versions. Resolve every
sibling workflow inside the selected package instead of invoking an unqualified
name that might select another copy. These checks do not authorize personal
configuration edits.

All modes keep the
[dispatch preflight](../plugins/code-flow/references/dispatch.md#capability-preflight).
Required checks cover independent inspection, effective model/effort policy,
verbatim canonical instructions, declared tools, complete results, and
package/repository worker access. Missing prerequisites stop execution before
edits. Discovery alone supplies none of those capabilities.

## Verify

```sh
codex plugin list
claude plugin list
pi list
agy plugin list --json
ls -la ~/.empryo/agents
```

Run the test suites from the repository root and each package root:

```sh
python3 -m unittest discover -s tests -v
```

## Update

For marketplace installs:

```sh
codex plugin marketplace upgrade phil0v
codex plugin add code-quality@phil0v
codex plugin add code-flow@phil0v
codex plugin add local-guards@phil0v
codex plugin add usage-metrics@phil0v
claude plugin marketplace update phil0v
claude plugin update code-quality@phil0v
claude plugin update code-flow@phil0v
claude plugin update local-guards@phil0v
claude plugin update usage-metrics@phil0v
```

For a local clone:

```sh
git pull
codex plugin add code-quality@phil0v
codex plugin add code-flow@phil0v
codex plugin add local-guards@phil0v
codex plugin add usage-metrics@phil0v
claude plugin update code-quality@phil0v
claude plugin update code-flow@phil0v
claude plugin update local-guards@phil0v
claude plugin update usage-metrics@phil0v
pi update --extensions
agy plugin install ./plugins/code-quality
agy plugin install ./plugins/code-flow
agy plugin install ./plugins/agy-agents
agy plugin install ./plugins/local-guards
agy plugin install ./plugins/usage-metrics
```

`codex plugin marketplace upgrade` refreshes the snapshot of a Git marketplace;
a local marketplace installs a copy into the Codex cache.
`agy plugin install` copies each plugin into `~/.gemini/config/plugins/`. Run
those install commands again after `git pull`. `~/.gemini/config/plugins.json`,
`~/.gemini/config/skills.json`, local-path Pi packages, `~/.empryo/agents/`
symlinks for existing roles, and the `~/.agents/skills/` links reflect the
pulled tree automatically. Run
`python3 plugins/code-flow/scripts/install-empryo-agents.py` again if
you add new Empryo roles.

## Usage metrics

The `usage-metrics` package records one JSON line per `local-guards` and
`code-flow` hook execution and `code-flow` script or dispatch invocation. It
writes lines to
`$AGENTS_HOME/metrics/usage.jsonl` (default `~/.agents/metrics/usage.jsonl`).
Each line contains only metadata: `ts`, `run` (one workflow's correlation id),
`plugin`, `event`, `status`, `seconds`, and optional `role`, `model`, or
`round_kind`. Records never include repository content, paths, or secrets.
Records stay on the machine, and nothing is sent over the network.

Hooks and scripts locate the recorder next to their package, in the sibling
`usage-metrics` copy of the client plugin cache, or under `AGENTS_HOME`. If the `usage-metrics` package is absent, recording is a silent
no-op and every hook and script runs unchanged. Set `AGENTS_METRICS=off` in
the environment to disable recording completely.

The report command reads the log, and an explicit `usage.jsonl` path is
optional:

```sh
python3 ./plugins/usage-metrics/scripts/metrics-report.py
```

The command prints invocations per plugin and per surface with `p50`/`p95`
seconds. It also prints review rounds per workflow with their `round_kind`
sequence, plus the ordered `role`/`model` dispatch list per workflow. Malformed
lines are skipped and counted in the summary. The `metrics-report` skill runs
this report and reads its output.

## Uninstall

```sh
codex plugin remove local-guards@phil0v
claude plugin uninstall code-quality@phil0v
pi remove git:github.com/phil0v/agents-kit
agy plugin uninstall code-quality
```

Repeat the command for each package and client. To remove the marketplace
itself, run `claude plugin marketplace remove phil0v` or
`codex plugin marketplace remove phil0v`. If you registered plugins or
your own skills in `~/.gemini/config/plugins.json` or
`~/.gemini/config/skills.json`, remove their entries there. For Empryo, remove
the `code-flow-*.md` symlinks from `~/.empryo/agents/`. If a package leaves
`plugins/`, remove its `[plugins."<name>@phil0v"]` block from
`~/.codex/config.toml`. Also remove `~/.codex/plugins/cache/phil0v/<name>/`
and the package skill links from `~/.agents/skills/`.

