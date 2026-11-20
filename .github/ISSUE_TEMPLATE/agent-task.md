---
name: Agent 协作任务
about: 冻结需求、提出计划并交接 Windows Planner / Mac Implementer / 独立 Reviewer
title: "[T-xxx] "
labels: ""
assignees: ""
---

## 原始需求与范围

- task_id：
- 用户原话：
- 可观察目标：
- 非目标：
- 允许修改文件/模块：
- 依赖或前置任务：
- 真实样本/模型 API/调用规模/隔离输出目录（不涉及则说明）：

## 实际执行环境

- Planner：host / OS / cwd / provider
- Implementer：host / OS / cwd / provider
- Reviewer：独立会话 / 独立检出目录 / OS
- 语言与依赖版本、测试服务：
- 已知环境缺口与解除条件：

设备在 UI 在线不等于远程执行已通过；填写实际检查结果。

## 验收条件

- AC-01：输入、预期结果、反例、验证命令、所需环境、证据位置。
- Code：必需检查。
- Integration：必需检查。
- Deployment：必需检查；若 N/A，说明范围依据。
- Acceptance：真实操作/设备/业务场景；若 N/A，说明范围依据。
- 风险及回退方式：

## 计划与审批记录

使用 `agent-workflow/templates/task-plan.md` 编写独立 docs-only 计划 PR。后续业务任务必须由用户批准并合入目标主干后实施；Issue 创建、标签或 Agent 回复均不代表批准。

```text
state: DRAFT
requirement_revision:
code_reference_sha:
plan_version:
plan_pr:
approved_by:
approved_plan_sha:
plan_path:
plan_blob_oid:
policy_version:
policy_source_sha:
required_gates:
required_checks:
require_integration_commit: false
code_base_sha:
implementation_pr:
pr_head_sha:
tested_base_sha:
tested_integration_sha:
attempt: 1
next_owner: Planner
evidence:
```

精确 SHA 使用完整提交标识；尚未批准或未执行的字段留空并注明缺口，不以“最新 main”或分支名代替。Bootstrap 仅以本次用户请求授权协作搭建，不能填造未来业务计划批准记录。

## 审核与交接

审核最多三轮（首轮加两次复审）；连续两轮同一阻塞交用户裁决；环境重试另计。结论使用 `PASS / CODE_CHANGES_REQUIRED / PLAN_REVISION_REQUIRED / ENV_BLOCKED / NEEDS_HUMAN`，并按 `agent-workflow/templates/review-report.md` 提供版本和四 Gate 证据。报告放候选分支外。本阶段无自动 merge，最终合并由用户确认。

必需门槛 `required_gates` 与非空检查名数组 `required_checks` 在用户批准时冻结；advisory 脚本只核对字段和版本，不认证批准身份或 CI 真实性。
