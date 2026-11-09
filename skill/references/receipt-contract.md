# 票据与回执契约（receipt-contract）

## 结论先行

`ticket` 是派发前的冻结请求，`receipt` 是派发后的事实记录，**二者按 ticketId 一一对应**。`passed` 是最严格状态：缺实际模型证据自动降 `unknown`；实际模型与请求模型不一致禁止 `passed`；`unknown/failed` 不得推进阶段。没有回执的阶段不能 `advance`。

## ticket 字段（`tickets/<stage>-<n>.json`）

| 字段 | 来源/含义 |
| --- | --- |
| ticketId | 票据唯一标识，回执文件名与之对应 |
| stage | 八阶段之一；不存在或已 passed 的阶段不允许开票 |
| role | model-map 中的角色键 |
| agentName | 由 role 解析的命名子代理名 |
| requestedModelId | 由 role 解析的 modelId（须在 model-catalog 且 builtin=true） |
| family | 模型家族，用于复核避让 |
| sourceRefs | 输入来源 path + sha256 |
| criteria | 验收判据 |
| allowedTools | 允许使用的工具范围（来自 role.tools） |
| outputPath | 产物路径 |
| dependencies | 依赖的票据/任务 |
| createdAt | 创建时间 |

## receipt 字段（`receipts/<ticketId>.json`）

| 字段 | CLI 参数 | 含义 |
| --- | --- | --- |
| status | `--status passed\|failed\|unknown` | 阶段结论 |
| agentId | `--agent-id ID` | 宿主子代理标识（可得时） |
| taskId | `--task-id ID` | 宿主任务标识（可得时） |
| agentName | 继承 ticket | 实际派发的代理名；回退时记 general-purpose + fallback |
| requestedModelId | 继承 ticket | 请求模型 |
| actualModelEvidence | `--actual-model TEXT` / `--model-evidence TEXT` | 实际模型及证据文本 |
| inputHashes | 校验 sourceRefs | 输入哈希一致性 |
| rawOutput | `--raw-output PATH` | 原始输出路径 + sha256 |
| artifactHashes | `--artifact PATH ...` | 产物路径 + sha256 |
| findings | `--finding TEXT ...` | 发现项、限制、分歧、fallback 记录 |

证据字段以 `config/model-map.json` 的 `dispatch.evidenceFields` 为准：`agentId, taskId, agentName, requestedModelId, actualModelEvidence, inputHashes, rawOutput, artifactHashes`。宿主未暴露的标识写 `unknown`，不得编造。

## 校验规则

| 规则 | 触发 | 结果 |
| --- | --- | --- |
| 缺实际模型证据 | `status=passed` 且无 `--actual-model` | 自动降为 `unknown`，不得通过 |
| 模型不一致 | `--actual-model` ≠ 票据 `requestedModelId` | 禁止标记 `passed` |
| 未知/失败不推进 | 回执 `unknown` 或 `failed` | `advance` 拒绝 |
| 来源哈希一致 | `advance` 时 | ticket 的 sourceRefs sha256 必须与当前文件一致 |
| 产物存在且一致 | `advance` 时 | 产物文件存在且哈希一致 |
| 回执齐备 | `advance` 时 | 该阶段所有票据都有对应 `passed` 回执 |
| 返修上限 | `revise` 时 | 超过档位 maxRevisions 拒绝，退出码非 0 |

## 状态语义

| status | 含义 | 能否推进 |
| --- | --- | --- |
| passed | 有真实模型证据、请求与实际模型一致、产物绑定 | 能 |
| failed | 执行失败或复核判不通过 | 不能 |
| unknown | 证据缺失、模型不可用、标识不可得 | 不能 |
| not_run | 档位不适用，须写原因 | 阶段级标记，不参与推进 |

## 示例（关键字段）

```json
{
  "ticketId": "execute-1",
  "stage": "execute",
  "role": "fast_executor",
  "agentName": "xiezhi-fast",
  "requestedModelId": "deepseek-v4.1-flash",
  "family": "DeepSeek",
  "sourceRefs": [{"path": "docs/input.md", "sha256": "<hex>"}],
  "criteria": "按判据成文",
  "allowedTools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
  "outputPath": "artifacts/draft.md",
  "dependencies": []
}
```

```json
{
  "ticketId": "execute-1",
  "status": "passed",
  "agentId": "<AGENT_ID>",
  "taskId": "<TASK_ID>",
  "agentName": "xiezhi-fast",
  "requestedModelId": "deepseek-v4.1-flash",
  "actualModelEvidence": {"actualModel": "deepseek-v4.1-flash", "evidence": "宿主子代理运行记录"},
  "rawOutput": {"path": "artifacts/raw-draft.md", "sha256": "<hex>"},
  "artifactHashes": [{"path": "artifacts/draft.md", "sha256": "<hex>"}],
  "findings": ["已覆盖 6 项判据"]
}
```

## 回退记录位置

自定义子代理名未加载而回退到 `general-purpose` 时：在 receipt 的 `--finding` 写明原 agentName、回退目标、显式传入的 modelId 与原因，并在 `--model-evidence` 标注 fallback；`report` 的边界声明必须披露，不得仍按命名子代理宣称路由证据。
