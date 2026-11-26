# Codex dispatch

Codex loads no native agent forms for this package. The plugin manifest registers only the hooks. `<package>/harnesses/codex` links to `<package>/roles/`; the workflows dispatch the role TOML files as resources from the package root, never as plugin-registered native agents. Model and effort come from each TOML's `model` and `model_reasoning_effort`. The resolver's `codex` profile has `effort_policy: explicit`.

When the selected profile requires a generic agent, use the `spawn_agent` adapter with `fork_turns: "none"` and the resolved `model` and `reasoning_effort` when those values are concrete. Choose a generic `default` or `worker` type rather than an unverified personal role.
