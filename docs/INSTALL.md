# Installation

## Requirements

- WorkBuddy or CodeBuddy Code with built-in credit-based models
- Python 3.10+
- No external model API key

## 1. Install the Skill

Copy `skill/` to:

```text
<WORKBUDDY_CONFIG_DIR>/skills/xiezhi-engine/
```

The Skill directory must contain `SKILL.md`, `references/`, `config/`, and `scripts/xiezhi.py`.

## 2. Install named subagents

Copy the CLI examples from `examples/agents/cli/` to:

```text
~/.codebuddy/agents/
```

Copy the WorkBuddy examples from `examples/agents/workbuddy/` to:

```text
<WORKBUDDY_CONFIG_DIR>/agents/
```

For a single project only, you may instead use:

```text
<workspace>/.codebuddy/agents/
```

## 3. Restart and preflight

Restart WorkBuddy so custom subagents are loaded, then run:

```bash
python runtime/xiezhi.py preflight
```

Preflight verifies that every role uses a built-in model ID from `config/model-catalog.json` and rejects `custom-local:*`, API-key fields, external URLs, Auto, and automatic tiers.

## 4. Optional desktop expert

See `examples/expert-package/`. It is an Agent-type expert entry, not a Team-type expert group, because WorkBuddy expert packages do not provide reliable per-member model binding.

Generate your own avatar before using the example in a real expert marketplace.
