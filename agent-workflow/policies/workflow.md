# FinSafetyEval 三角色协作政策

政策标识：`workflow-v1`。执行时还须记录获取本政策的可信来源提交 SHA（`policy_source_sha`，完整 40 位 Git commit），不可只凭可变文件路径确认版本。

本政策将工作区《Paseo 跨设备 Agent 协作方案》用于 FinSafetyEval 的第一阶段人工接力试点。本次用户请求授权协作基础搭建（bootstrap）；新增规则、模板与脚本须作为可审阅候选交付，不能自行声明用户已经批准未来业务计划、权限规则或合并。

## 角色、主机与隔离

- Windows Planner 调查需求、读取源码，提交 docs-only 计划 PR；不接管业务实现。
- Mac Implementer 在独立 worktree 按已批准计划修改代码、验证并交付实现 PR。
- 独立 Reviewer 使用新会话与独立检出，固定候选 SHA，亲自复跑关键检查；不修复候选分支，不修改计划或门禁，不执行 merge。
- 用户批准业务计划及重要修订，并最终确认合并。本阶段无自动 merge。

启动角色前记录实际执行 host、OS、cwd、分支与任务 ID。Windows 客户端显示 Mac 会话不能证明任务在 Windows 执行；设备配对也不会复制仓库、工具、依赖或 GitHub 登录。

角色提示词、只读约定与本政策属于行为约定，不构成系统沙箱、GitHub 独立身份或路径级写入隔离。三个 Agent 共用 GitHub 账号时，Reviewer 报告可以支持用户判断，但不能冒充正式独立 Approve。仓库权限和分支保护是否生效必须另行实测。

## 计划准入与版本交接

后续业务任务采用以下顺序：Issue 冻结需求 → 独立 docs-only 计划 PR → 用户批准并合入目标主干 → Mac 实现 PR → 独立审核 → 用户确认 merge。只创建计划 PR 或 push 分支不代表计划已批准。

计划文件使用 `plans/<task_id>/plan-v<version>.md`，保留原始需求快照、明确非目标、可观察 AC-ID、影响文件、验证命令、所需环境与回退方式。需求快照用内容 hash 或其他不可变版本标识。

交接至少绑定：

```text
task_id / repository / requirement_revision
plan_version / approved_plan_sha / plan_path / plan_blob_oid
code_reference_sha / code_base_sha
pr_number / pr_head_sha / tested_base_sha
tested_integration_sha（未执行则 null）
policy_version / policy_source_sha（可信政策来源）
attempt / 实际执行主机与依赖环境 / 证据位置
```

`approved_plan_sha` 使用计划实际合入目标主干后的完整提交 SHA；squash 后不能继续使用旧分支 SHA。计划 blob 须来自该提交，审批记录放 Issue / plan PR 或受控记录，不能通过计划文件自写其提交 SHA。Implementer fetch 后确认批准计划仍有效且该提交是当前目标主干祖先；相关代码或依赖已漂移时先复核假设。

需求、计划、候选 head 或可信政策变化，旧报告不得继续作为当前通过结论。目标主干变化使原集成证据失效，须检查变动并重新执行受影响验证。报告提交到候选分支外，避免写报告时改变被审核 head。

Bootstrap 使用此次用户请求和工作区既有方案作为范围依据，不伪造 `approved_plan_sha`。其本地实验结果只能证明已执行的试点检查；bootstrap 合并及未来业务任务的计划准入仍由用户确认。

## 状态与返工路由

```text
DRAFT → PLAN_REVIEW → PLAN_APPROVED → IMPLEMENTING → PR_READY → REVIEWING
REVIEWING → PASS → READY_FOR_HUMAN_MERGE → MERGED
REVIEWING → CODE_CHANGES_REQUIRED → Implementer 修复并重审
REVIEWING → PLAN_REVISION_REQUIRED → Planner 提交新计划，用户批准后再实施
REVIEWING → ENV_BLOCKED → 环境恢复后补验
REVIEWING → NEEDS_HUMAN → 用户裁决后继续
```

- `CODE_CHANGES_REQUIRED`：计划合理，代码遗漏、行为错误或验证不足；修改原实现 PR，交新 head 复审。
- `PLAN_REVISION_REQUIRED`：计划假设或路径不成立；保留旧版，新增 vN+1 及原因，用户批准后更新绑定，不能降低验收标准迁就代码。
- `ENV_BLOCKED`：缺工具、服务、账号、设备或测试数据；列出已验证部分、缺口与解除条件，不能将未测写为 PASS。
- `NEEDS_HUMAN`：需求、范围、风险取舍或证据冲突无法判断；只继续与该决策无依赖的工作。
- `PASS`：报告所绑定版本的适用要求均有证据且无未解决阻塞，交用户确认；不等于已经合并、部署或上线。

审核上限为三轮（首轮 + 两次复审）；连续两轮重复同一阻塞则升级给用户。基础设施重试与业务返工分开记录并设置停止条件，不无限轮询或重复启动任务。首版仅有一个协调入口，以任务及版本识别重复交接；不将 Issue 标签、Git 文件或两台设备各自的本机锁描述为分布式原子领取。

## 四项门槛与证据

每项标为 `PASS / FAIL / BLOCKED / N/A`；N/A 必须有已批准范围依据并说明原因。

- **Code**：diff、边界行为、静态检查、相关单元验证与 AC-ID 的实现对应。
- **Integration**：真实依赖、组件交互和候选代码与目标主干结合后的行为。只有单元测试或语法检查不足以证明这层。
- **Deployment**：任务要求的构建、配置、迁移及部署证据。仅库代码或离线协作试验可在计划中明确不要求部署。
- **Acceptance**：实际业务/用户操作和设备证据；跨设备闭环须有两端实际执行记录。离线 fixture 或模拟状态不能代替远程执行、真实模型评测或用户设备验收。

Reviewer 区分亲自运行、静态阅读、CI 记录与实施方声明；声明本身不能支撑 PASS。Finding 含 ID、严重性、AC-ID、位置、输入、预期/实际、复现证据、责任人与关闭条件。多类问题并存时全部保留，以当前阻断原因决定下一责任人。

合并交接须复核计划批准有效、当前 head/base/policy 匹配、必要 CI 成功、没有未解决阻塞、必需证据齐全及用户最终确认。无 CI 或分支保护时如实披露，不把本地绿色检查写成平台强制门禁。Critical/High 缺陷不能由 Agent 自行豁免。所有新提交重新核对相关结论。

## FinSafetyEval 数据与执行范围

业务试点在计划中指明是否调用模型 API、模型或网关、样本范围、调用规模和输出目录。真实样本、处罚案例知识库、模型配置和历史评测输出按声明范围处理；离线 fixture 使用隔离目录，不覆盖已有评测数据。不得将离线流程验证表述为模型安全评测通过。

CI、门禁脚本、角色/计划规则、CODEOWNERS、权限、密钥和分支保护变动需单列范围供用户审阅。测试候选代码的进程不应携带可发布通过状态或可 merge 的高权限凭据。第一阶段不启用 Hub、常驻 watcher 或自动合并；后续自动化另行计划并验证幂等、休眠恢复和停止入口。

## Advisory 门禁输入契约

独立记录 `approval`、`current`、`review`，共同字段为 `task_id`、`repository`、`requirement_revision`、`plan_version`、`approved_plan_sha`、`plan_path`、`plan_blob_oid`、`code_base_sha`、`policy_version`、`policy_source_sha`。

- `approval` 另需 `approved_by`、`required_gates` 数组、非空检查名称数组 `required_checks`；可选布尔 `require_integration_commit` 默认 false。必需门槛和检查在用户批准时冻结，不能由实现或审核记录减少。
- `current` 另需当前现场核对的 `pr_head_sha` 与 `tested_base_sha`。
- `review` 含共同字段、current 字段及 `tested_integration_sha`、`overall`、四项 `gates`、`required_checks`、`blocking_findings`。每项 Gate 是 `{status, reason}`；每项检查是 `{name, status, head_sha}`；未执行合并验证时 integration SHA 为 null。

本地门禁脚本只提供 advisory 一致性判断，不认证 `approved_by` 身份、不查询或保证 CI 真实性、不确认计划实际获批、不发布可信检查，也不自动 merge。演练 fixture 与真实批准记录必须明确区分，尚未获用户批准的任务不得填造有效 approval。
