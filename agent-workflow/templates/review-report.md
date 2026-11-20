# 独立验收报告模板

> 独立 Reviewer 按实际证据填写，没有默认通过项。报告放 PR 评论或候选分支外的证据目录，不提交回候选分支。

## 结论与版本绑定

- overall：`<PASS / CODE_CHANGES_REQUIRED / PLAN_REVISION_REQUIRED / ENV_BLOCKED / NEEDS_HUMAN>`
- 一句话依据：`<决定当前下一步的事实>`
- task_id / repo / PR：`<精确标识>`
- requirement_revision：`<本次冻结需求快照的内容 hash 或不可变版本>`
- plan_version / approved_plan_sha：`<版本 / 完整 SHA>`
- plan_path / plan_blob_oid：`<路径 / Git blob OID>`
- pr_head_sha：`<被审查候选的完整 SHA>`
- tested_base_sha：`<本次使用的目标主干完整 SHA>`
- tested_integration_sha：`<实际测试的合并提交 SHA；未做合并测试则明确写未执行>`
- policy_version：`<可信审查政策版本>`
- Reviewer / 运行环境 / 时间：`<独立会话、OS、关键工具版本、测试数据标识、时区>`
- 有效性：`<截至复核时上述版本是否仍匹配；不匹配则明确过期>`

## 四项门槛

分别选 `PASS / FAIL / BLOCKED / N/A`；`N/A` 必须引用批准计划的依据。

- Code：`<状态、检查及证据>`
- Integration：`<状态、检查及证据>`
- Deployment：`<状态、检查及证据>`
- Acceptance：`<状态、检查及证据>`

必需 CI：`<工作流 / run ID / 对应 SHA / 状态>`。CI 失败、未完成或任一要求的门槛未通过，不得给总体 PASS。无未解决 critical/high 缺陷是 PASS 的必要条件，不能取代行为验收。

## 验收条件覆盖

### AC-01：`<结论>`

- 对应代码：`<文件、行号、符号>`
- 证据类型：`<独立运行 / CI / 静态阅读 / 仅实施方声明>`
- 命令、输入与预期：`<可复跑信息>`
- 实际结果与证据位置：`<退出码、日志、断言或产物>`
- 尚未验证：`<缺口或明确无>`

按需复制，覆盖所有批准的 AC-ID。实施方声明不能单独支撑通过。

## Finding

### F-01：`<具体问题>`

- 严重性 / 关联 AC-ID：`<critical / high / medium / low；AC-ID>`
- 类别与路由：`<代码 / 计划 / 环境 / 人工裁决；责任人>`
- 位置：`<精确文件与行号>`
- 复现前置条件、输入与步骤：`<可执行内容>`
- 预期 / 实际：`<差异>`
- 证据：`<运行记录或静态推导；未复现时明确说明>`
- 影响与建议：`<建议修哪里以及为何，不直接修改>`
- 关闭条件：`<修复后必须看到的结果>`
- 状态：`<待处理 / 已复核关闭；关闭时绑定新 SHA>`

## 下一步与交接

- 当前责任人：`<Implementer / Planner / 环境责任人 / 用户>`
- 必须完成的动作：`<逐项对应 finding>`
- 可并行进行的动作：`<与当前阻塞无依赖的事项>`
- 待用户决定：`<具体选项与影响或明确无>`
- 重审触发：候选提交、有效计划、目标主干、可信政策或关键环境发生变化时，重评受影响门槛；旧报告只保留为历史证据。
- merge：由用户确认并执行。`PASS` 仅表示本报告绑定版本已满足要求，不表示合并、部署或上线已完成。

若需机器消费，再由可信控制器把上述字段转成严格 schema 的结果，校验身份、版本、必需 CI 和问题状态后发布 check；不能仅扫描评论中的 `PASS` 字样决定合并。

## 试点调度与身份

- 审核最多三轮（首轮加两次复审）；连续两轮重复同一阻塞时升级给用户；环境恢复重试单独记录。
- 本阶段无自动 merge。审核报告只对所列精确版本有效，不能授权计划、批准 PR 或修改分支规则。
- Bootstrap 以用户此次协作搭建请求为范围依据，尚未批准的候选政策不能冒充主干可信政策。后续业务任务须核实独立计划 PR 已获用户批准并合入。
- 报告的 Windows / Mac、provider、cwd 与命令结果须来自实际运行证据；远程设备在 UI 可见不等于远程命令执行成功。

## 结构化 review 记录

门禁所用 review 记录必须与独立 `approval`、`current` 记录的共同字段一致：`task_id`、`repository`、`requirement_revision`、`plan_version`、`approved_plan_sha`、`plan_path`、`plan_blob_oid`、`code_base_sha`、`policy_version`。另含 `pr_head_sha`、`tested_base_sha`、`tested_integration_sha`、`overall`、`gates`、`required_checks`、`blocking_findings`。

- `gates` 必须包含 `Code`、`Integration`、`Deployment`、`Acceptance` 四项，每项为 `{ "status": "PASS|FAIL|BLOCKED|N/A", "reason": "实际证据或明确依据" }`。
- `required_checks` 为 `{ "name": "已冻结的检查名", "status": "实际状态", "head_sha": "本次候选 SHA" }` 数组，覆盖 approval 冻结的全部必需检查；不得由审核记录自行缩减。
- `blocking_findings` 为未解决阻塞数组；没有则用空数组。`tested_integration_sha` 未执行时为 null，不能伪造合并测试提交。
- `approval.required_gates` 与 `approval.required_checks` 在用户批准时冻结，`approval.require_integration_commit` 省略时为 false；必需 Integration 检查仍须有真实结果。
- 脚本只做 advisory 字段、版本与状态一致性检查，不认证批准者或 CI 来源，不发布可信 check，不自动 merge。
