# 复核独立性（independence）

## 结论先行

最终报告必须写明实际达到的独立性等级，**不能混称**。只有拿到真实模型证据才能称 `cross_model/cross_family_review`；无合格候选家族时降级 `context_review` 并明确披露，不得为流程好看伪报跨家族。复核一律使用全新上下文，禁止恢复任何作者或汇总代理。

## 四级定义

| 等级 | 判定条件 | 需要的证据 |
| --- | --- | --- |
| role_review | 仅角色提示词不同，模型与上下文未隔离 | 无跨模型证据，最低级 |
| context_review | 新子代理/新上下文，未继承作者讨论过程 | 新 agent/task 标识 |
| cross_model_review | 有真实证据表明复核者与作者模型不同 | 实际模型证据 |
| cross_family_review | 复核者与全部相关作者模型家族不同，且未参与被审材料创作 | 实际模型证据 + 作者家族集合 |

档位对应：fast → `role_review`；standard → `context_review`；strict → `cross_family_review_if_possible`（争取跨家族，达不到就降级并披露）。

## 作者家族避让

`python runtime/xiezhi.py reviewer --run-id ID` 的逻辑：

1. 读取所有 passed 执行/汇总回执的 `family`，形成**作者家族集合**。
2. 按 `reviewPolicy.candidateRoleOrder` 选第一个 `family` 不在作者集合中的角色。
3. 输出 `recommendedRole`、`recommendedModelId`、`independenceLevel`。
4. 无合格候选 → 输出 `context_review`（`noEligibleFamilyAction`），**不得伪报 cross_family_review**。

默认复核角色：`business_reviewer` / `hy4free`（Tencent Hunyuan）。若混元已参与中文写作或被审材料创作，则不得把本次复核标为跨家族独立复核。

候选顺序（来自 config/model-map.json）：

| 顺序 | role | modelId | family |
| --- | --- | --- | --- |
| 1 | long_context_deep_checker | deepseek-v4-pro | DeepSeek |
| 2 | office_data_executor | minimax-m3.1-flash | MiniMax |
| 3 | sol_controller | kimi-k3.1-pro | Moonshot Kimi |
| 4 | requirements_analyst | glm-5.3 | Zhipu GLM |
| 5 | chinese_writer | hy4free | Tencent Hunyuan |

## 强制规则

| 规则 | 要求 |
| --- | --- |
| 复核上下文 | 必须启动**全新子代理上下文**，禁止恢复任何作者/汇总代理的 agentId |
| 返修复用边界 | 仅允许恢复同一作者代理；复核一律新建 |
| 证据缺失 | 宿主未暴露实际模型证据 → 标 `unknown`，不得升级独立性等级 |
| 模型不可用 | 停止该复核路线并请求用户确认，不静默换备用模型 |
| 报告披露 | 写明达到的等级、作者家族集合、复核者 modelId 与证据来源 |

## 与账本的对应

`verify` 会检查复核独立性声明与作者家族是否矛盾；`report` 必须包含独立性等级、作者家族、复核者模型与模型证据，缺证据时明确披露。详见 `receipt-contract.md` 与 `troubleshooting.md`。
