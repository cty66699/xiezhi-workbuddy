# 协智引擎 WorkBuddy 原生版实施规格

## 目标

在本机部署一套仅使用 WorkBuddy 内置积分模型的协智引擎。系统包含：

1. 用户级 Skill：`~/.workbuddy/skills/xiezhi-engine/`
2. 命名子代理：`~/.workbuddy/agents/` 与 `~/.codebuddy/agents/`
3. 项目源码：当前工作区 `xiezhi-workbuddy/`
4. 本地运行账本：JSON + 单文件 HTML 报告
5. 桌面端专家入口：按 expert-manager 规范注册

## 硬约束

- 不调用外部模型 API，不使用 API Key、供应商 SDK、Token Plan 或自定义端点。
- 不使用 `custom-local:*` 模型，也不使用 `Auto`、`fast-model`、`balanced-model`、`deep-model`。
- 模型 ID 只能来自 `config/model-catalog.json`。
- 派发子代理时显式传入 `modelId`，不仅依赖 frontmatter。
- 不在当前会话逐轮切换模型；主会话保持固定，子任务启动独立上下文。
- 不伪造模型证据；没有实际模型证据时标记 `unknown`。
- 不静默替换模型；模型不可用即停止并请求用户确认。
- 本地脚本只处理状态、哈希、证据、模板和报告，不生成模型回答。

## 模型分工

详见 `config/model-map.json`。默认主链：

`kimi-k3.1-pro` 主控 → `glm-5.3` 分析 → `glm-5.3-flash` 路由 → 按需专家并行 → `glm-5.3-flash` 汇总 → `kimi-k3.1-pro` 安全 → `hy4free` 复核 → `kimi-k3.1-pro` 裁定。

专业专家：`deepseek-v4.1-flash` 快速执行、`deepseek-v4-pro` 深度核验、`kimi-k2.8-preview` 编程与视觉文档、`minimax-m3.1-flash` 数据与 Office 及多模态复核、`hy4free` 中文成文。

## 阶段

`coordinate / analyze / route / execute / collect / safety / review / adjudicate`

- fast：`coordinate, execute, adjudicate`；其余 `not_run`。
- standard / strict：全部八阶段。
- 每阶段状态：`pending / passed / failed / not_run / unknown`。
- 标准最多 1 轮返修，严谨最多 2 轮，极速默认不返修。

## 运行目录结构

```text
xiezhi-runs/<run-id>/
├─ task-spec.json
├─ run-ledger.json
├─ tickets/
├─ receipts/
├─ findings/
├─ artifacts/
├─ revisions/
└─ report.html
```

## CLI 规格：`runtime/xiezhi.py`

仅使用 Python 3 标准库，入口为 `python xiezhi.py <command>`。默认 `--root` 为当前工作目录下的 `xiezhi-runs`。

### `preflight`

- 读取 `config/model-catalog.json` 与 `config/model-map.json`。
- 校验所有 `modelId` 均在目录内且 `builtin=true`。
- 校验没有 `custom-local:`、外部 URL、API Key 字段。
- 检查子代理定义文件是否存在于已安装路径。
- 输出 `{"ok":true,...}` 或明确失败，退出码非 0。

### `start --goal TEXT --mode fast|standard|strict [--source PATH ...] [--root DIR]`

- 生成 `run-id`：UTC 时间 + goal/source 内容哈希前缀。
- 相同 goal 与相同 source 哈希时返回原 run-id，不重复创建。
- 写 `task-spec.json`：goal、mode、sources(path+sha256)、createdAt、idempotencyKey、policyVersion。
- 写 `run-ledger.json`：八阶段及状态；不适用阶段标记 `not_run` 并写原因；当前阶段为第一个 pending 阶段。

### `ticket --run-id ID --stage STAGE --role ROLE [--source PATH ...] [--criteria TEXT] [--output PATH]`

- 从 model-map 解析 `agentName/modelId/family/tools`。
- 写 `tickets/<stage>-<n>.json`：ticketId、stage、role、agentName、requestedModelId、family、sourceRefs(含 sha256)、criteria、allowedTools、outputPath、dependencies、createdAt。
- 不存在的阶段或已 passed 阶段不允许再开票。

### `receipt --run-id ID --ticket PATH --status passed|failed|unknown [--agent-id ID] [--task-id ID] [--actual-model TEXT] [--model-evidence TEXT] [--raw-output PATH] [--artifact PATH ...] [--finding TEXT ...]`

- 写 `receipts/<ticketId>.json`。
- 保存原始输出与产物路径及 SHA256。
- 若 `status=passed` 且没有 `actual-model`，自动降为 `unknown`，不得通过。
- 若 `actual-model` 与票据 `requestedModelId` 不一致，禁止标记 passed。
- 记录 agentId、taskId、modelEvidence、findings。
- `unknown/failed` 不得推进阶段。

### `advance --run-id ID --stage STAGE`

- 该阶段所有票据都必须有对应回执且全部 `passed`。
- 校验 ticket 中的 sourceRefs 与当前文件哈希一致。
- 检查产物存在且哈希一致。
- 通过后把阶段改为 `passed`，推进 currentStage；否则保持 `failed/unknown` 并返回错误。

### `reviewer --run-id ID`

- 读取所有 passed 执行/汇总回执中的 family，形成作者家族集合。
- 按 `reviewPolicy.candidateRoleOrder` 选第一个 family 不在作者集合中的角色。
- 输出 recommendedRole、recommendedModelId、independenceLevel。
- 无合格候选则输出 `context_review`，不得伪报跨家族。

### `revise --run-id ID --reason TEXT`

- 检查模式允许的最大返修轮次。
- 在 `revisions/round-N.json` 保留原发现、原因和新 idempotencyKey。
- 把 `execute, collect, safety, review, adjudicate` 阶段重置为 pending；保留 coordinate/analyze/route 证据。
- 超过上限时拒绝并退出码非 0。

### `verify --run-id ID`

- 检查所有 passed 阶段都有通过回执。
- 检查 unknown/failed 未标成 passed。
- 检查来源与产物哈希未变。
- 检查返修轮次未超限。
- 检查复核独立性声明与作者家族是否矛盾。
- 输出结构化结果。

### `report --run-id ID`

- 生成单文件、可打印 HTML：`report.html`。
- 包含目标、模式、模型分工、阶段状态、来源、回执、模型证据、发现项、返修链、未运行项、边界声明。
- 不引入外部 CSS/JS。

### `status --run-id ID`

- 输出当前阶段、阶段状态、未解决发现和返修次数。

### `selftest`

- 调用 `tests/test_xiezhi.py` 的 unittest，返回聚合结果。

## 文档要求

- `docs/INSTALL.md`：安装路径、重启说明、预检命令。
- `docs/USAGE.md`：极速/标准/严谨三档使用方法与真实派发示例。
- `docs/LIMITATIONS.md`：桌面专家团模型绑定、宿主证据、积分精确值等边界。
- `README.md`：系统总览、目录、快速开始、验收状态。
- `skill/SKILL.md`：触发条件、八阶段 SOP、命名子代理派发、证据与禁止事项。
- `skill/references/`：workflow、receipt-contract、independence、troubleshooting。

## 测试要求

`tests/test_xiezhi.py` 使用 `unittest` 和临时目录，覆盖：

1. preflight 通过且拒绝 custom-local/external API。
2. start 幂等。
3. ticket 绑定正确模型与来源哈希。
4. 缺实际模型证据时 receipt 不得 passed。
5. 来源变化后 advance 拒绝。
6. reviewer 排除作者模型家族。
7. revise 超过返修上限被阻止。
8. report 生成 HTML 且包含关键字段。
9. verify 检查通过/失败路径。
