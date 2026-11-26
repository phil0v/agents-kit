# Pi dispatch

Pi loads the generated forms from `<package>/harnesses/pi/<role>.md` through the pi-subagents extension. The frontmatter carries effort in the `thinking` key. The key and level handling were verified against installed pi-subagents 0.84.0. The evidence is its `docs/agents.md` frontmatter reference, `src/agents/agents.js` parser, and `src/runs/shared/child-session.js` `thinkingLevel` handoff.
