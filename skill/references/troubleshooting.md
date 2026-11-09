# 故障处置（troubleshooting）

## 结论先行

原则：**证据不足就标 `unknown` 并停止，不伪造、不静默替换、不把失败记为成功**。下表按现象给处置，处置后凡涉及模型和来源变化都必须重新走 `ticket → receipt → advance`。

## 问题速查

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `preflight` 失败，退出码非 0 | modelId 不在 model-catalog / 非 builtin；出现 `custom-local:`、外部 URL、API Key 字段；子代理定义文件缺失 | 核对 `config/model-catalog.json` 与 `config/model-map.json`；补齐 `~/.workbuddy/agents`、`~/.codebuddy/agents`、`<workspace>/.codebuddy/agents` 下的定义文件；停止路线，不启动 start |
| 新子代理定义未生效、按名称派发报“未知代理” | WorkBuddy 未重启，定义未加载 | 重启 WorkBuddy 让新子代理定义加载；或按 SKILL.md 回退 `general-purpose` + 显式 modelId 并**记录 fallback** |
| 客户端模型显示名与可配置 ID 不一致 | 显示名 ≠ 实际 modelId | 启动前把显示名解析为 model-catalog 中的实际 ID，冻结运行清单；解析失败即停止并请求确认 |
| 模型不可用 / 账号未开放 | Kimi-K3.1-Pro 等模型非全量开放 | 停止该路线并请求用户确认；**不自动回退备用模型**；不得视为所有账户可用 |
| `passed` 回执被拒或自动变 unknown | 缺 `--actual-model` / `--model-evidence` | 补齐实际模型证据；无证据一律标 `unknown`，不得标记通过 |
| `passed` 回执被拒（模型不一致） | `--actual-model` ≠ 票据 `requestedModelId` | 禁止 passed；核对派发时显式传入的 modelId；不一致即停止并请求确认 |
| `advance` 拒绝 | 来源文件被改动；产物缺失或哈希不一致；该阶段仍有票据无 passed 回执 | 重新核对 sourceRefs 与产物 sha256；必要时重新 ticket 或 `revise`；`unknown/failed` 阶段不得推进 |
| `reviewer` 输出 `context_review` 而非跨家族 | 作者家族占满所有候选家族 | 按 `noEligibleFamilyAction` 降级并在报告披露；**不得伪报 cross_family_review** |
| 复核结论与作者家族矛盾 / 独立性等级报错 | 复核者参与了被审材料创作或声明等级过高 | 用 `verify` 检查；新建全新上下文复核者；按 `independence.md` 降级并披露 |
| `revise` 被拒，退出码非 0 | 超过该档 maxRevisions（fast 0 / standard 1 / strict 2） | 停止自动返修，把未解决发现交给用户裁定；不做无限重试 |
| `start` 返回旧 run-id | 相同 goal + 相同 source 哈希 | 属幂等设计，非错误；用返回的 run-id 继续，不重复创建 |
| 桌面专家团无法逐成员绑定模型 | 宿主能力未承诺 | 落到命名子代理；仍不可用则按固定模型新建独立任务并标记 `manual_multi_task_mode`；**不宣称自动多模型编排** |
| 积分值取不到 | 本地账本无法读取真实扣减 | 记 `unavailable` 和调用次数等辅助指标；**不用 Token 自行伪造精确积分账单**；精确值以客户端实时显示为准 |
| 报告打不开 / 引用外部资源 | 非预期 | `report` 生成单文件 HTML，不引入外部 CSS/JS；直接用浏览器打开或打印为 PDF |
| 依赖任务被提前启动 | 阻塞未解除 | 通过共享任务列表解除依赖后再启动；独立任务才并行 |

## 诊断命令

```bash
python runtime/xiezhi.py preflight
python runtime/xiezhi.py status --run-id <RUN_ID>
python runtime/xiezhi.py verify --run-id <RUN_ID>
python runtime/xiezhi.py selftest
```

`selftest` 调用 `tests/test_xiezhi.py` 的 unittest，覆盖预检拒绝、幂等、票据哈希、证据缺失、来源变更、复核避让、返修上限、report 与 verify 等路径。

## 不可为清单

外部模型 API / custom-local / Auto 自动档位；提示词自称模型当路由证据；伪造回执或把派发请求当真实返回；静默换模型；无证据标 passed；把 `not_run` 阶段省略后假装完整执行；用合成输出冒充子代理返回。
