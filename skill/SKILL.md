---
name: xiezhi-engine
display_name: 协智引擎
version: 2026-11-09-local-v1
description: >-
  协智引擎（WorkBuddy 原生版）八阶段任务编排与严谨复核。当用户说“启动协智引擎”“用协智引擎处理”
  “严谨复核”“按标准档处理”“按极速档处理”，或要求按 fast/standard/strict 分级、多角色并行派发、
  独立上下文复核、可追溯运行报告与有限返修时调用。仅使用 WorkBuddy 内置积分模型与命名子代理，
  不接入任何外部模型 API。
---

# 协智引擎（xiezhi-engine）

## 结论先行

本 Skill 是**调度说明书，不是模型执行器**。它不能把当前会话底层模型改成另一个模型；模型调用只能发生在 WorkBuddy 原生执行链（命名子代理 / 专家）。本地脚本只处理状态、哈希、证据、模板和报告，**不生成模型回答**。任何阶段缺真实模型证据，一律标 `unknown`，不得记为通过。

## 何时调用

| 触发语句 | 默认档位 | 说明 |
| --- | --- | --- |
| 启动协智引擎 / 用协智引擎处理 | standard | 走完整八阶段 |
| 严谨复核 / 按严谨档处理 | strict | 全阶段、最多 2 轮返修、优先跨家族复核 |
| 按标准档处理 / 标准复核 | standard | 全阶段、最多 1 轮返修 |
| 极速处理 / 简单任务用极速档 | fast | 仅 coordinate/execute/adjudicate |
| 生成运行报告 / 看复核结论 | 按已有 run-id | 调用 status / report |

用户未指定档位时：简单问答、短文案、明确小修改建议 `fast`；报告、方案、一般文件与代码任务用 `standard`；高风险、多约束、复杂研究、重要交付由用户确认后进入 `strict`。

## 硬约束（违反即停止）

| 禁止项 | 具体含义 |
| --- | --- |
| 外部模型 API | 不调用外部模型端点，不使用 API Key、供应商 SDK、Token Plan、自定义端点 |
| custom-local 模型 | 不使用 `custom-local:*` 及任何自定义 API 模型 |
| Auto 自动档位 | 不使用 `Auto`、`fast-model`、`balanced-model`、`deep-model` 承担需核验的固定岗位 |
| 提示词自称模型 | 在提示词里写“你现在使用 X”并当作路由证据；不能证明真实模型切换 |
| 伪造回执 | 打印派发请求或合成输出当成子代理真实返回；缺证据标记 passed；把派发请求当结果 |
| 静默换模型 | 模型不可用时自动回退备用模型；必须停止路线并请求用户确认 |
| 主会话逐轮切模型 | 不在当前会话里反复手动切换模型；主会话保持固定 |

模型 ID 只能来自 `config/model-catalog.json` 且 `builtin=true`；角色绑定以 `config/model-map.json` 为准。

## 三档分级

| 档位 | 阶段 | 并行专家上限 | 复核等级 | 返修上限 |
| --- | --- | --- | --- | --- |
| fast | coordinate, execute, adjudicate | 1 | role_review | 0 |
| standard | 全部八阶段 | 2 | context_review | 1 |
| strict | 全部八阶段 | 4 | cross_family_review_if_possible | 2 |

不适用的阶段必须写 `not_run + 原因`，不能省略记录后假装完整执行。阶段状态只取 `pending / passed / failed / not_run / unknown`。

## 全流程 SOP

固定顺序：**preflight → start → ticket → 派发 → receipt → advance → reviewer → revise（按需）→ verify → report**。`status` 随时查，`selftest` 验脚本。

| 步骤 | 命令 | 动作要点 |
| --- | --- | --- |
| 1 preflight | `python runtime/xiezhi.py preflight` | 校验 modelId 在目录内且 builtin=true、无 custom-local/外部 URL/API Key、子代理定义文件存在；失败即停 |
| 2 start | `python runtime/xiezhi.py start --goal "..." --mode standard --source PATH` | 生成 run-id（UTC + 内容哈希）；相同 goal + 相同 source 哈希返回原 run-id，不重复创建 |
| 3 ticket | `python runtime/xiezhi.py ticket --run-id ID --stage STAGE --role ROLE --source PATH --criteria TEXT --output PATH` | 从 model-map 解析 agentName/modelId/family/tools，记录 sourceRefs sha256 |
| 4 派发 | 启动命名子代理 | **显式传入 modelId**；见下节调度规则 |
| 5 receipt | `python runtime/xiezhi.py receipt --run-id ID --ticket PATH --status passed\|failed\|unknown --actual-model ID --model-evidence TEXT --raw-output PATH --artifact PATH --finding TEXT` | 无实际模型证据自动降 unknown；actual-model 与票据 requestedModelId 不一致禁止 passed |
| 6 advance | `python runtime/xiezhi.py advance --run-id ID --stage STAGE` | 该阶段所有票据有 passed 回执、来源与产物哈希一致才推进 |
| 7 reviewer | `python runtime/xiezhi.py reviewer --run-id ID` | 按作者家族避让选 recommendedRole/modelId/independenceLevel |
| 8 revise | `python runtime/xiezhi.py revise --run-id ID --reason TEXT` | 复核发现需返修时；超上限拒绝 |
| 9 verify | `python runtime/xiezhi.py verify --run-id ID` | 检查回执、哈希、返修轮次、复核独立性声明 |
| 10 report | `python runtime/xiezhi.py report --run-id ID` | 生成单文件可打印 `report.html` |

八阶段与命令参数 `STAGE` 取值：`coordinate / analyze / route / execute / collect / safety / review / adjudicate`。

## 派发与调度规则（强制）

| 规则 | 要求 |
| --- | --- |
| 显式 modelId | 派发命名子代理时**必须显式传入 `modelId`**，不能只依赖子代理 frontmatter 的 `model` 字段 |
| 主会话不切模型 | `mainConversationModelSwitch = not_used`；主会话保持固定模型，子任务启动独立上下文 |
| 独立任务并行 | 相互独立的子任务在**同一批调度中并行启动**（fast≤1 / standard≤2 / strict≤4），不广播给所有模型 |
| 依赖任务串行 | 有依赖的任务通过共享任务列表解除阻塞后再启动 |
| 复核必须新上下文 | 独立复核一律启动**全新子代理上下文**，禁止恢复任何作者或汇总代理的 agentId |
| 返修复用 | 仅同一作者代理的续改可按 agentId 恢复上下文 |

### 角色 → 子代理 → modelId 映射（来自 config/model-map.json）

| role | agentName | modelId | family |
| --- | --- | --- | --- |
| sol_controller | xiezhi-sol | kimi-k3.1-pro | Moonshot Kimi |
| requirements_analyst | xiezhi-analyst | glm-5.3 | Zhipu GLM |
| luna_router | xiezhi-router | glm-5.3-flash | Zhipu GLM |
| fast_executor | xiezhi-fast | deepseek-v4.1-flash | DeepSeek |
| long_context_deep_checker | xiezhi-deep-checker | deepseek-v4-pro | DeepSeek |
| coding_executor | xiezhi-coding | kimi-k2.8-preview | Moonshot Kimi |
| office_data_executor | xiezhi-office | minimax-m3.1-flash | MiniMax |
| chinese_writer | xiezhi-chinese | hy4free | Tencent Hunyuan |
| visual_document_executor | xiezhi-visual | kimi-k2.8-preview | Moonshot Kimi |
| multimodal_reviewer | xiezhi-multimodal-reviewer | minimax-m3.1-flash | MiniMax |
| business_reviewer | xiezhi-business-reviewer | hy4free | Tencent Hunyuan |

默认主链：`kimi-k3.1-pro` 主控 → `glm-5.3` 分析 → `glm-5.3-flash` 路由 → 按需专家并行 → `glm-5.3-flash` 汇总 → `kimi-k3.1-pro` 安全 → `hy4free` 复核 → `kimi-k3.1-pro` 裁定。

### 子代理未加载时的回退

如果自定义子代理名（如 `xiezhi-coding`）尚未被宿主加载，**可回退到 `general-purpose` 并显式传入对应 `modelId`**，但必须记录 fallback：

| 记录项 | 内容 |
| --- | --- |
| 原 agentName | 票据中的命名子代理名 |
| 回退目标 | `general-purpose` |
| modelId | 仍按 model-map 显式传入，不得省略 |
| 原因 | 自定义子代理定义未加载 / 未生效 |
| 落点 | 写入 receipt 的 `--finding` 与 `--model-evidence`，并在 report 边界声明中披露 |

回退不豁免任何证据要求：仍须有实际模型证据才能标记 passed。新子代理定义未生效时，先重启 WorkBuddy 再重试命名派发。

## 可复制命令示例

在项目根目录 `xiezhi-workbuddy/` 下执行（Python 3 标准库，无需安装依赖）：

```bash
# 1 预检
python runtime/xiezhi.py preflight

# 2 启动一次标准档运行
python runtime/xiezhi.py start --goal "整理季度报告并核对数据来源" --mode standard --source docs/input.md

# 3 为分析与执行阶段开票（示例 run-id 替换为实际输出）
python runtime/xiezhi.py ticket --run-id <RUN_ID> --stage analyze --role requirements_analyst --source docs/input.md --criteria "列出全部约束与验收判据" --output artifacts/analysis.md
python runtime/xiezhi.py ticket --run-id <RUN_ID> --stage execute --role fast_executor --source docs/input.md --criteria "按判据成文" --output artifacts/draft.md

# 4 派发：启动 xiezhi-analyst / xiezhi-fast，显式传入 modelId=glm-5.3 / deepseek-v4.1-flash

# 5 回执（passed 必须带 --actual-model 与 --model-evidence）
python runtime/xiezhi.py receipt --run-id <RUN_ID> --ticket tickets/analyze-1.json --status passed --agent-id <AGENT_ID> --task-id <TASK_ID> --actual-model glm-5.3 --model-evidence "宿主子代理运行记录" --raw-output artifacts/raw-analysis.md --artifact artifacts/analysis.md --finding "约束共 6 项"

# 6 推进阶段
python runtime/xiezhi.py advance --run-id <RUN_ID> --stage analyze

# 7 选择独立复核者
python runtime/xiezhi.py reviewer --run-id <RUN_ID>

# 8 复核发现后返修
python runtime/xiezhi.py revise --run-id <RUN_ID> --reason "复核发现第 3 项数据来源缺失"

# 9 校验并出报告
python runtime/xiezhi.py verify --run-id <RUN_ID>
python runtime/xiezhi.py report --run-id <RUN_ID>
python runtime/xiezhi.py status --run-id <RUN_ID>

# 脚本自检
python runtime/xiezhi.py selftest
```

## 参考文档

| 文件 | 内容 |
| --- | --- |
| `references/workflow.md` | 八阶段状态机、档位×阶段矩阵、调度与返修细则 |
| `references/receipt-contract.md` | ticket/receipt 字段契约与校验规则 |
| `references/independence.md` | 复核独立性四级、作者家族避让、候选顺序 |
| `references/troubleshooting.md` | 预检失败、证据缺失、来源变更、模型不可用等处置 |
