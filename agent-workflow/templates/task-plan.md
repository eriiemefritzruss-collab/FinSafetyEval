# 任务与计划模板

> 复制本模板到 `plans/<task_id>/plan-v<version>.md` 并填写真实值；尖括号不是已有任务或批准。审批记录放 GitHub Issue / plan PR，避免把计划提交 SHA 写回自身造成循环。

## 任务身份

- task_id：`<例如 T123>`
- 关联 Issue：`<仓库 / Issue 编号>`
- 需求原文快照：`<在本版本化文件中保留原始需求或引用另一版本化需求文件>`
- requirement_revision：`<上述冻结需求快照的内容 hash 或不可变版本>`
- plan_version：`<例如 1>`
- code_reference_sha：`<Planner 实际阅读的代码提交 SHA>`
- policy_version：`<可信协作政策的版本或提交 SHA>`
- policy_source_sha：`<可信政策来源的完整 Git commit SHA>`
- Planner / Implementer / Reviewer / merge 人：`<分别填写>`
- 依赖任务与顺序：`<依赖或明确无>`

## 目标与范围

- 用户期望的可观察行为：`<触发、输入、结果>`
- 非目标与不能改变的现有行为：`<范围边界>`
- 允许修改的文件或模块：`<清单>`
- 需要另行确认的范围：`<涉及迁移、权限、CI、政策或需求变化时填写>`
- 代码依据与当前行为：`<精确路径、符号、事实；推测另列>`
- 假设、待验证事项及决策：`<事项、责任人、结论>`

## 实施步骤

1. `<具体步骤、涉及模块、前置依赖、产物>`
2. `<具体步骤>`
3. `<验证与回退准备>`

## 验收契约

### AC-01：`<可验证的行为>`

- 输入 / 前置条件：`<明确值与状态>`
- 预期结果：`<可观察输出和副作用>`
- 反例 / 边界：`<应拒绝、失败或保持不变的情况>`
- 验证方式 / 命令：`<执行位置与命令；尚未确定要标明>`
- 所需环境 / 数据：`<Mac、Windows、浏览器、服务及隔离数据>`
- 所属门槛：`<Code / Integration / Deployment / Acceptance>`
- 必需证据：`<日志、断言、截图、产物或可复现记录>`

按需复制为 AC-02 等，不能仅以“实现了某函数”代替行为验收。

## 必需门槛与风险

- Code：`<必需检查>`
- Integration：`<必需检查或明确不要求及原因>`
- Deployment：`<必需检查或明确不要求及原因>`
- Acceptance：`<必需检查；涉及真实账号、设备或用户体验时说明验收人>`
- 兼容 / 迁移 / 数据影响：`<影响及处理>`
- 回退条件与方式：`<如何确认异常，如何恢复；数据不可逆影响单列>`
- 返工轮次 / 时间 / 成本上限：`<事先约定值；超限交用户裁决>`

## 审批与接力记录（放 Issue / plan PR）

```text
task_id: <任务 ID>
plan_version: <批准版本>
plan_pr: <docs-only PR 编号>
approved_by: <用户>
approved_plan_sha: <合入 main 后包含批准计划的精确提交 SHA>
requirement_revision: <冻结需求快照版本>
plan_path: plans/<task_id>/plan-v<version>.md
plan_blob_oid: <上述提交中该文件的 Git blob OID>
policy_version: <可信政策版本>
policy_source_sha: <可信政策来源的完整 Git commit SHA；未获批不填造>
code_base_sha: <本次实现起点；准入时绑定，未实施不填造>
required_gates: <冻结的必需门槛数组，如 [Code, Integration, Acceptance]>
required_checks: <冻结的必需检查名称数组，至少一项>
require_integration_commit: <true / false，省略时为 false>
state: <PLAN_APPROVED / IMPLEMENTING / REVIEWING / READY_FOR_HUMAN_MERGE 等>
next_owner: <下一责任人>
implementation_pr: <创建后填写>
pr_head_sha: <当前候选 SHA；创建后填写>
```

v1 推荐先批准并合入 docs-only plan PR，再启动实施。计划修订必须新建 vN+1，用户批准后更新有效版本；旧审查结论不得沿用。只有用户对某个低风险小任务明确授权时，才可用冻结、可追溯的计划快照省略独立 plan PR，不能把这个例外自动推广到其他任务。

## 试点约定

- 使用政策：`agent-workflow/policies/workflow.md`；本阶段无自动 merge。
- Bootstrap 的依据是用户此次实施请求；它仅授权协作基础搭建，不代表未来业务计划已经批准。
- 后续业务任务必须完成独立 docs-only 计划 PR 的用户批准及合入，再填写 `approved_plan_sha`，不能用草稿 SHA 预填。
- 审核最多三轮（首轮加两次复审）；连续两轮重复同一阻塞则升级给用户。基础设施重试另计并设停止条件。
- 记录执行主机、OS、cwd、语言及依赖版本、隔离输出位置。若使用模型 API，明确模型/网关、样本范围与调用规模；离线试验须明确与真实模型评测的区别。

## 结构化门禁记录

确定性门禁使用独立的 `approval`、`current`、`review` 记录，共同绑定以下字段：`task_id`、`repository`、`requirement_revision`、`plan_version`、`approved_plan_sha`、`plan_path`、`plan_blob_oid`、`code_base_sha`、`policy_version`、`policy_source_sha`。批准前不得将任何演练数据写成真实 approval。

`approval` 另含 `approved_by`、`required_gates` 数组、非空检查名称数组 `required_checks`，可选 `require_integration_commit`（布尔值，默认 false）。必须检查的名称和门槛在批准时冻结，实现者不能在 review 中减少检查。

`current` 另含 `pr_head_sha` 与 `tested_base_sha`，来源须为现场核对的候选和目标分支状态。`review` 按独立验收模板记录相同版本及执行证据。脚本只做 advisory 一致性检查，不认证批准者身份，不查询或保证 CI 真实性，也不授予 merge 权限。
