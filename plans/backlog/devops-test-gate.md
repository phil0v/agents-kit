# DevOps flow package with a local pre-deploy test gate

## Context

The request asks for a devops flow with local testing before a deploy: unit, smoke, and e2e layers.
The user chose a new installable package and a gate-only flow; the deploy action stays manual.
The user named `ecc` as the test source and could not say what `ecc` is; this plan does not use it.

Package detection needs the `.claude-plugin` manifest directory: `tests/test_packages.py:10` "if path.is_dir() and (path / '.claude-plugin/plugin.json').exists()".
Three places pin the package set: `Makefile:6` "PACKAGES := code-flow code-quality local-guards usage-metrics", `tests/test_packages.py:27` "{'code-quality', 'code-flow', 'local-guards',", and `tests/test_skills.py:10` "PACKAGES = ('code-flow', 'code-quality', 'local-guards', 'usage-metrics')". A new package joins all three.
The identity test compares every manifest copy and the marketplace entry; they share one name, one version, and one description.
The portability test copies each package of its `PACKAGES` tuple and checks every markdown link inside the copy.
The root Pi glob at `package.json:12` '"./plugins/*/skills/*"' already covers a new package skill directory.
Root tests check every `SKILL.md` for `name`, `description`, and `argument-hint`; `tests/test_files.py:67` "self.assertIsInstance(data.get('argument-hint'), str)" pins the hint rule.
The `code-quality` package shows the minimal manifest trio: `plugins/code-quality/plugin.json`, `plugins/code-quality/package.json`, and `plugins/code-quality/.claude-plugin/plugin.json`.
`plans/backlog/cross-review-skill.md` is the nearby precedent for adding a package skill.
Package runtime scripts run on system `python3` 3.9 or newer with the standard library only.
Existing package tests need no `__init__.py`; `plugins/code-quality/tests/` holds only `test_portability.py`.

## Summary

| File | Change |
|---|---|
| `plugins/devops-flow/scripts/run_gate.py` | New gate runner script |
| `plugins/devops-flow/tests/test_run_gate.py` | Behavior pins: layer order, stop on first failure, exit codes |
| `plugins/devops-flow/skills/deploy-gate/SKILL.md` | New pre-deploy gate skill |
| `plugins/devops-flow/tests/test_package.py` | Package pin: exposed skill set and script path |
| `plugins/devops-flow/plugin.json` | New portable package manifest |
| `plugins/devops-flow/package.json` | New Pi identity manifest |
| `plugins/devops-flow/.claude-plugin/plugin.json` | New Claude identity manifest |
| `.claude-plugin/marketplace.json` | Marketplace entry for the new package |
| `Makefile` | `devops-flow` joins `PACKAGES` |
| `tests/test_packages.py` | Pinned package set gains `devops-flow` |
| `tests/test_skills.py` | Portability package tuple gains `devops-flow` |
| `README.md` | Package row, skill row, install line |
| `docs/install.md` | Client install lines and the skills link loop |
| `AGENTS.md` | Repository map gains the package |
| `package.json` | Description names the new package kind |

## Steps

1. [ ] Add the gate runner script with behavior tests
   depends on: none
   acceptance: A1, A2, A3
   creates: plugins/devops-flow/scripts/run_gate.py, plugins/devops-flow/tests/test_run_gate.py
   - Write `plugins/devops-flow/scripts/run_gate.py` with the standard library only; it runs on system python3 3.9 or newer.
   - The script reads `.devops-flow.json` at the target root that `--root <dir>` names; the default target root is the working directory.
   - Config keys `unit`, `smoke`, and `e2e` carry one shell command string each.
   - The script refuses unknown keys and a config with no layers; exit is 2 with the reason named.
   - An empty or missing command counts as no layer.
   - A missing `.devops-flow.json` at the target root exits 2 and names the file.
   - Invalid JSON, a non-object config, or a non-string command exits 2 and names the reason.
   - The script runs configured layers in the fixed order unit, smoke, e2e.
   - Each layer runs as one shell command at the target root.
   - A nonzero layer exit stops the run; later layers do not run.
   - On a stop, exit is 1 and the report names the failed layer, its command, and its exit status.
   - On a green run, exit is 0 and the report lists one status line per layer.
   - Write `plugins/devops-flow/tests/test_run_gate.py` with portable fake commands; cover order, stop, all three exit codes, and the missing config case.
   done when: the new package suite passes with the order, stop, exit-code, and config-refusal pins
   test: cd plugins/devops-flow && python3 -m unittest discover -s tests -v

2. [ ] Add the deploy-gate skill and pin the package surface
   depends on: 1
   acceptance: A4, A5
   creates: plugins/devops-flow/skills/deploy-gate/SKILL.md, plugins/devops-flow/tests/test_package.py
   - Frontmatter `argument-hint` is `[<project-root>]`.
   - The skill fires only on an explicit request to run the local pre-deploy gate.
   - The skill resolves its package root two directories above its own skill directory.
   - The skill runs `plugins/devops-flow/scripts/run_gate.py` with `--root` set to the project root argument, or to the working directory.
   - A missing `.devops-flow.json` at the project root stops the skill; the report names the file and the config schema.
   - The skill creates no files.
   - A failed layer stops the flow; the report names the layer, the command, and the exit status.
   - The skill runs no deploy step; a green report states that the deploy stays manual.
   - Write `plugins/devops-flow/tests/test_package.py` in the style of `plugins/code-flow/tests/test_package.py`.
   - The pin lists the exposed skill set and asserts that the runner script exists.
   done when: the package suite passes with the packaging pin and the skill file in place
   test: cd plugins/devops-flow && python3 -m unittest discover -s tests -v

3. [ ] Register the package in the repository packaging surfaces
   depends on: 1, 2
   acceptance: A6
   creates: plugins/devops-flow/plugin.json, plugins/devops-flow/package.json, plugins/devops-flow/.claude-plugin/plugin.json
   - All manifests share version `0.1.0` and one description.
   - The Pi manifest name is @phil0v/devops-flow in the established scoped form; the other two manifest names are `devops-flow`.
   - The `.claude-plugin` copy adds the `author` object, as the `code-quality` package does.
   - Add a `.claude-plugin/marketplace.json` entry; the source points at the new package directory.
   - The entry carries the same version and description and license MIT.
   - Add `devops-flow` to the `PACKAGES` list at `Makefile:6` "PACKAGES := code-flow code-quality local-guards usage-metrics".
   - Add `devops-flow` to the pinned set at `tests/test_packages.py:27` "{'code-quality', 'code-flow', 'local-guards'," and to the tuple at `tests/test_skills.py:10` "PACKAGES = ('code-flow', 'code-quality', 'local-guards', 'usage-metrics')".
   - The root `package.json` needs no edit; its skills glob already covers the new skill.
   done when: the root suite passes with the package registered in manifests, marketplace, and both pins
   test: python3 -m unittest discover -s tests -v

4. [ ] Name the package in documentation and the repository map
   depends on: 3
   acceptance: A7
   - Add a package row and a `deploy-gate` skill row to `README.md`, plus the plugin install line.
   - Add package install lines to the client sections and the skills link loop in `docs/install.md`.
   - Raise the package counts in both documents from four to five.
   - Cover the intro text and the install, update, and uninstall command lists.
   - Add `devops-flow` to the `plugins/` line of the repository map in `AGENTS.md`.
   - Extend the root `package.json` description with the new package kind.
   done when: the docs link check passes and the documents name the package consistently
   test: python3 -m unittest tests.test_docs -v

## Acceptance

- A1. The gate script runs configured layers in the fixed order unit, smoke, e2e, and a nonzero layer exit stops the run before later layers.
- A2. A missing, malformed, or empty config, or unknown keys, exits 2 and names the reason.
- A3. A green run exits 0 and prints one status line per layer.
- A4. The `deploy-gate` skill runs the gate script at the requested project root and reports the result; it runs no deploy step.
- A5. A missing `.devops-flow.json` at the project root stops the skill with a report that names the file and the config schema.
- A6. The root suite passes with `devops-flow` in the manifests, the marketplace entry, `PACKAGES`, and both packaging pins, under one shared identity.
- A7. `README.md`, `docs/install.md`, and `AGENTS.md` name the package, and the docs link check passes.

## Decisions

- The flow lives in a new installable package; the user chose the package route.
- The flow gates only; the deploy action stays manual.
- The target project `.devops-flow.json` file is the only test-command source; layers are optional, one layer minimum.
- The layer order is fixed: unit, then smoke, then e2e.
- This version has no external test source; the unresolved `ecc` idea stays an open question.
- One process runs the layers in sequence; no agent dispatch, so no roles and no harness forms.
- The runner script uses the standard library on system `python3` 3.9 or newer.
- Usage metrics recording stays out of this version.

## Out of scope

- The deploy and release execution itself, and any CI system integration.
- The `ecc` test-source integration.
- Parallel layers, per-layer timeouts, and per-layer working directories.
- Changes to `code-flow`, `code-quality`, `local-guards`, `usage-metrics`, and `agy-agents` beyond the shared packaging pins.

## Open questions

- [ ] The request named `ecc` as the test source; its meaning is unresolved. Settle it before any external test-source plan.

## Verification

- make check
