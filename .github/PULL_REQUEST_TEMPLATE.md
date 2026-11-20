## 问题与交付行为

<!-- 用具体输入/触发说明变更前后行为；计划 PR 说明目标与设计，实施 PR 说明实际行为。 -->

- task_id / Issue：
- PR 类型：计划 / 实现 / 协作 bootstrap
- 目标与非目标：
- 修改范围与风险：
- 回退方式：

## 计划与候选版本

<!-- 业务实施须关联用户已批准且合入主干的 docs-only 计划；bootstrap 以此次搭建请求为范围依据，不伪造批准字段。 -->

```text
requirement_revision:
plan_pr / plan_version:
approved_plan_sha:
plan_path / plan_blob_oid:
code_reference_sha / code_base_sha:
pr_head_sha:
tested_base_sha:
tested_integration_sha: null
policy_version / policy_source_sha:
required_gates / required_checks（来自批准记录，不能由本 PR 缩减）:
require_integration_commit:
actual_host / OS / cwd:
```

## 验证与验收覆盖

<!-- 列出 AC-ID → 行为/改动 → 实际命令及退出码 → 证据。未执行写明原因，不能把预期写成结果。 -->

- AC-01：
- Code：PASS / FAIL / BLOCKED / N/A；证据：
- Integration：PASS / FAIL / BLOCKED / N/A；证据：
- Deployment：PASS / FAIL / BLOCKED / N/A；证据或 N/A 的批准范围依据：
- Acceptance：PASS / FAIL / BLOCKED / N/A；证据或 N/A 的批准范围依据：
- 必需 CI：工作流 / 对应 SHA / run 链接 / 状态；无 CI 则说明。
- 尚未验证、已知缺陷与下一责任人：

## 独立审核与用户确认

- 独立 Reviewer / 报告链接：
- 报告绑定 head / base / plan / policy：
- 审核轮次：1 / 2 / 3
- 总结论：待审核 / PASS / CODE_CHANGES_REQUIRED / PLAN_REVISION_REQUIRED / ENV_BLOCKED / NEEDS_HUMAN
- 当前状态与下一责任人：

报告保存在候选分支外；新 head、计划或政策变化后旧报告不得继续作为当前通过结论，目标主干变化须重评集成证据。角色规则属于行为约定，不能当作权限隔离或正式独立 Approve。本阶段无自动 merge；全部适用要求满足后交用户确认合并。

Advisory 门禁只核对 approval/current/review 的字段、版本与状态，不认证批准身份或 CI 真实性；未批准的任务不能填造有效 approval。
