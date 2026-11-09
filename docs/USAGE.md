# Usage

## Trigger phrases

- "启动协智引擎" / "用协智引擎处理"
- "按标准档处理"
- "按严谨档复核"
- "用极速档完成"

## Modes

| Mode | Stages | Parallel experts | Review | Repair |
| --- | --- | --- | --- | --- |
| `fast` | coordinate, execute, adjudicate | 1 | role review | 0 |
| `standard` | all eight stages | 2 | independent context | 1 |
| `strict` | all eight stages | 4 | cross-family if possible | 2 |

## Standard flow

1. `preflight`
2. `start`
3. `ticket`
4. Dispatch a named subagent with an explicit `modelId`
5. `receipt`
6. `advance`
7. `reviewer`
8. `revise` only when needed
9. `verify`
10. `report`

## Report

`python runtime/xiezhi.py report --run-id <RUN_ID>` writes a self-contained HTML file to `runs/<RUN_ID>/report.html`. It contains model roles, stage status, sources, receipts, findings, repair history, and limitations.

## Important

Do not manually switch the main conversation model between stages. Start a new named subagent context for each model-bound task.
