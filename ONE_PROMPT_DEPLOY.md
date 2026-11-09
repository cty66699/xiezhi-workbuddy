# One-prompt deployment for WorkBuddy

## Short answer

A bare repository URL is **not enough** for WorkBuddy to guess that it should install a Skill, generate subagents, register an expert, adapt model IDs and run preflight.

Use the following single prompt instead.

## Copy this into your WorkBuddy

```text
请把这个 GitHub 仓库部署到我的 WorkBuddy：
https://github.com/cty66699/xiezhi-workbuddy

请克隆仓库，进入仓库目录，运行：
python scripts/bootstrap.py --workspace . --yes

只写入 WorkBuddy/CodeBuddy 用户配置中与 xiezhi-engine 相关的目录；不要修改其他配置。
完成后运行仓库内的 preflight 和 scripts/verify_redaction.py，告诉我重启 WorkBuddy 后如何召唤“协智引擎”。
```

Chinese or English both work. The essential parts are:

1. The repository URL.
2. `python scripts/bootstrap.py --workspace . --yes`.
3. Permission to write only the Xiezhi Engine user-config directories.
4. A request to run preflight and report the result.

## What bootstrap.py installs

- `<WORKBUDDY_CONFIG_DIR>/skills/xiezhi-engine/`
- `<WORKBUDDY_CONFIG_DIR>/agents/` — 11 WorkBuddy-style named subagents
- `~/.codebuddy/agents/` — 11 CLI-style named subagents
- `<workspace>/.codebuddy/agents/` — optional project-level CLI subagents
- `<WORKBUDDY_CONFIG_DIR>/plugins/marketplaces/my-experts/plugins/xiezhi-engine/` — optional Agent-type expert entry
- `<WORKBUDDY_CONFIG_DIR>/plugins/marketplaces/my-experts/.codebuddy-plugin/marketplace.json` — local registration entry
- `<WORKBUDDY_CONFIG_DIR>/xiezhi-engine-install.json` — install manifest

It also attempts to discover the local WorkBuddy built-in model catalog and adapt `model-map.json` to the model IDs actually available on that machine. If discovery is unavailable, it falls back to the bundled snapshot and emits a warning.

## Safety properties

- No external model API calls.
- No API keys, Token Plan or `custom-local:*` models.
- No recursive deletion of user directories.
- No modification of unrelated marketplace entries.
- No generated binary avatar download; a standard-library placeholder PNG is created locally.
- Preflight is executed automatically and the bootstrap exits non-zero if it fails.

## After deployment

1. Restart WorkBuddy.
2. Open Expert Center.
3. Search for `协智引擎` or `xiezhi-engine`.
4. It appears as an **Agent-type expert**, not a Team-type expert group.
5. Send: `用协智引擎标准档处理这个任务，并生成运行报告。`

## Verification commands

```bash
python scripts/verify_redaction.py
python runtime/xiezhi.py preflight
python -m unittest discover -s tests -p "test_*.py"
```
