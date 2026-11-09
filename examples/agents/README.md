# Agent definition examples

- `cli/`: definitions intended for `~/.codebuddy/agents/` or `<workspace>/.codebuddy/agents/`.
- `workbuddy/`: definitions intended for `<WORKBUDDY_CONFIG_DIR>/agents/`.

Each file binds one role to one WorkBuddy built-in model ID. The dispatcher must still pass `modelId` explicitly at spawn time; do not rely only on frontmatter.

Restart WorkBuddy after installation so custom subagents are loaded.
