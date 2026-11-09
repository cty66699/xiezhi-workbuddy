# 八阶段工作流（workflow）

## 结论先行

一次运行 = 冻结主会话模型 + 按档位展开阶段 + 每阶段 `ticket → 派发 → receipt → advance` + 独立复核 + 有限返修 + 出报告。八阶段状态始终记录；不适用写 `not_run + 原因`，不能省略后假装完整执行。所有模型调用发生在 WorkBuddy 原生子代理，本地脚本不生成回答。

## 阶段定义

| 阶段 STAGE | 主责 role | 默认 modelId | 主要产物 |
| --- | --- | --- | --- |
| coordinate | sol_controller | kimi-k3.1-pro | task-spec.json、验收标准、档位、run-id |
| analyze | requirements_analyst | glm-5.3 | 约束拆解、验收判据、依赖与备选方案 |
| route | luna_router | glm-5.3-flash | DAG、最小任务票据、串并行关系 |
| execute | 按需专家 | 按 role 映射 | 各专家结果、来源、限制、分歧 |
| collect | luna_router | glm-5.3-flash | 贡献矩阵，保留分歧不抹平 |
| safety | sol_controller | kimi-k3.1-pro | 越权/敏感资料/外部动作/风险检查 |
| review | 独立复核者 | 按 reviewer 输出 | 逐项复核结论与发现项 |
| adjudicate | sol_controller | kimi-k3.1-pro | 逐项处置、最终文件哈希绑定、交付 |

## 档位 × 阶段矩阵

| 阶段 | fast | standard | strict |
| --- | --- | --- | --- |
| coordinate | 运行 | 运行 | 运行 |
| analyze | not_run（极速档省略） | 运行 | 运行 |
| route | not_run（极速档省略） | 运行 | 运行 |
| execute | 运行 | 运行 | 运行 |
| collect | not_run（极速档省略） | 运行 | 运行 |
| safety | not_run（极速档省略） | 运行 | 运行 |
| review | not_run（极速档省略） | 运行 | 运行 |
| adjudicate | 运行 | 运行 | 运行 |

阶段状态取值：`pending / passed / failed / not_run / unknown`。`failed / unknown` 不得推进阶段；没有真实模型证据时标 `unknown` 而非 `passed`。

## 调度规则

| 项 | 规则 |
| --- | --- |
| 主会话模型 | 固定不变，`mainConversationModelSwitch = not_used` |
| 模型绑定 | 派发时**显式传入 modelId**，不仅依赖 frontmatter；ID 必须来自 model-catalog 且 builtin=true |
| 独立任务 | 同一批调度中并行启动；fast≤1、standard≤2、strict≤4，不广播 |
| 依赖任务 | 通过共享任务列表解除阻塞后串行启动 |
| 复核上下文 | 必须全新上下文，禁止恢复作者/汇总代理 agentId |
| 返修复用 | 仅同一作者代理可按 agentId 恢复上下文 |

## 命令序列（按阶段）

| 顺序 | 命令 | 通过条件 |
| --- | --- | --- |
| 0 | `python runtime/xiezhi.py preflight` | 输出 `{"ok":true,...}`；失败退出码非 0 即停 |
| 1 | `python runtime/xiezhi.py start --goal TEXT --mode fast\|standard\|strict [--source PATH ...]` | 相同 goal + 相同 source 哈希返回原 run-id（幂等） |
| 2 | `python runtime/xiezhi.py ticket --run-id ID --stage STAGE --role ROLE [opts]` | 不存在的阶段或已 passed 阶段不允许开票 |
| 3 | 派发命名子代理（显式 modelId） | 见 SKILL.md 映射表 |
| 4 | `python runtime/xiezhi.py receipt --run-id ID --ticket PATH --status passed\|failed\|unknown [opts]` | 见 receipt-contract.md |
| 5 | `python runtime/xiezhi.py advance --run-id ID --stage STAGE` | 该阶段全部票据有 passed 回执、来源与产物哈希一致 |
| 6 | `python runtime/xiezhi.py reviewer --run-id ID` | 输出 recommendedRole/recommendedModelId/independenceLevel |
| 7 | `python runtime/xiezhi.py revise --run-id ID --reason TEXT`（按需） | 未超该档 maxRevisions |
| 8 | `python runtime/xiezhi.py verify --run-id ID` | 无证据未标 passed、哈希未变、返修未超限、独立性声明不矛盾 |
| 9 | `python runtime/xiezhi.py report --run-id ID` | 生成 `report.html` |

辅助：`python runtime/xiezhi.py status --run-id ID` 查当前阶段/未解决发现/返修次数；`python runtime/xiezhi.py selftest` 跑 unittest 聚合结果。

## 返修规则

| 档位 | maxRevisions | 默认复核 |
| --- | --- | --- |
| fast | 0（默认不返修） | role_review |
| standard | 1 | context_review |
| strict | 2 | cross_family_review_if_possible |

`revise` 行为：

- 在 `revisions/round-N.json` 保留原发现、原因和新 idempotencyKey。
- 把 `execute, collect, safety, review, adjudicate` 重置为 `pending`；保留 `coordinate/analyze/route` 证据。
- 超过上限时拒绝并退出码非 0。

## 运行目录

```text
runs/<run-id>/
├─ task-spec.json      # goal、mode、sources(path+sha256)、createdAt、idempotencyKey、policyVersion
├─ run-ledger.json     # 八阶段状态与当前阶段
├─ tickets/
├─ receipts/
├─ findings/
├─ artifacts/
├─ revisions/
└─ report.html         # 单文件、可打印、无外部 CSS/JS
```

## 禁止事项（详见 SKILL.md）

外部模型 API / API Key / 供应商 SDK / Token Plan / 自定义端点；`custom-local:*`；`Auto` 与自动档位；提示词自称模型当路由证据；伪造回执或把派发请求当真实返回；静默换模型；主会话逐轮切模型；无证据标 passed。
