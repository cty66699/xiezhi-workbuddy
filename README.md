# Xiezhi Engine for WorkBuddy

A WorkBuddy-native, evidence-driven multi-model orchestration system.

Xiezhi Engine uses **only WorkBuddy built-in credit-based models**. It does not call external model APIs, does not require API keys, and does not expose any vendor endpoints. Model routing is performed through named subagents with an explicit `modelId`, while a local runtime maintains task tickets, receipts, SHA-256 hashes, independent review, bounded repair, and a printable HTML report.

> This is a reference implementation. Model IDs and availability can vary by WorkBuddy client version, account, and subscription. Always verify the model list in your own client before use.

## Highlights

- Eight-stage workflow: coordinate, analyze, route, execute, collect, safety, review, adjudicate.
- Three modes: `fast`, `standard`, `strict`.
- Named subagents bound to WorkBuddy built-in model IDs.
- Explicit `modelId` on every dispatch; no in-conversation manual model switching.
- Source and artifact SHA-256 integrity checks.
- Independent-context review with author-family avoidance.
- Bounded repair and no silent model fallback.
- Single-file, printable HTML run report.
- No external model API, API key, Token Plan, or `custom-local:*` model.

## Default model roles

| Role | Model ID | Assignment |
| --- | --- | --- |
| Sol controller | `kimi-k3.1-pro` | Global coordination, safety, review recommendation, final adjudication |
| Requirements analyst | `glm-5.3` | Multi-constraint analysis and acceptance criteria |
| Luna router | `glm-5.3-flash` | DAG routing, handoff, structured collection |
| Fast executor | `deepseek-v4.1-flash` | Short, well-defined tasks |
| Deep checker | `deepseek-v4-pro` | Long-context reasoning and evidence checking |
| Coding / visual document executor | `kimi-k2.8-preview` | Code generation, long reading, image-to-document |
| Data / Office executor | `minimax-m3.1-flash` | Spreadsheets, data analysis, presentations, multimodal review |
| Chinese writer / default reviewer | `hy4free` | Chinese documents, meeting notes, reasoning review |

These IDs are a snapshot. See `config/model-catalog.json` and `config/model-map.json`.

## Quick start

```bash
python runtime/xiezhi.py preflight
python runtime/xiezhi.py start --goal "Example task" --mode standard --source README.md
```

Then follow `skill/SKILL.md` to create tickets, dispatch named subagents with explicit `modelId`, record receipts, advance stages, and generate a report:

```bash
python runtime/xiezhi.py report --run-id <RUN_ID>
```

The report is written to `runs/<RUN_ID>/report.html`.

## Installation

See `docs/INSTALL.md`. In short:

1. Install `skill/xiezhi-engine` as a user-level WorkBuddy Skill.
2. Install the example agent definitions under `examples/agents/`.
3. Restart WorkBuddy so custom subagents are loaded.
4. Run `preflight` and resolve any unavailable model IDs.

An optional Agent-type expert package example is provided under `examples/expert-package/`.

## Repository contents

| Path | Purpose |
| --- | --- |
| `config/` | Built-in model catalog and role-to-model mapping |
| `runtime/` | Local ledger, installer, smoke helper, benchmark |
| `skill/` | WorkBuddy Skill and reference contracts |
| `tests/` | Standard-library integration tests |
| `examples/agents/` | CLI and WorkBuddy agent definition examples |
| `examples/expert-package/` | Optional desktop expert entry example |
| `docs/` | Installation, usage, limitations, benchmark, lessons |
| `scripts/verify_redaction.py` | Re-check that no sensitive patterns remain |
| `SANITIZATION.md` | Release sanitization report |

## Privacy and safety

This repository contains no personal paths, account identifiers, API keys, session IDs, real run artifacts, or WorkBuddy internal configuration. Runtime data under `runs/` is intentionally excluded from the release and ignored by Git.

## License

MIT. See `LICENSE`.
