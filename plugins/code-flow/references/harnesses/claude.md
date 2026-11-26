# Claude Code dispatch

Claude Code loads the generated forms from `<package>/harnesses/claude/<role>.md` as plugin agents. The `.claude-plugin/plugin.json` manifest registers one form per canonical role. The forms retain the `effort` key in the frontmatter; `<package>/tools/sync-agents.py` emits it from the mapping's explicit effort.
