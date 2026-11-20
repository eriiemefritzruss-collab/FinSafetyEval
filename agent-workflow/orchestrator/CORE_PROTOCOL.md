# 本机协调器核心协议

`core.py` 只负责 SQLite 持久状态、事件版本绑定、任务派发账本和确定性路由。
它不访问 GitHub/Paseo、不验证 Git 对象、不执行模型或 merge。Transport adapter 必须
先验证实际作者、有效批准和当前 Git/CI 事实，再把事件交给核心。

同一 GitHub 账号由人和多个 Agent 共用时，不能仅凭账号或 JSON 自报的 `User` 角色
证明人工批准。`trusted_actors` 是来源身份 allowlist，不是人的意图认证系统。

## 接口

```python
from orchestrator import Controller

controller = Controller(
    "/absolute/task-owned/state.sqlite3",
    trusted_actors={
        "User": ["authenticated-user-or-trusted-console-id"],
        "Planner": ["authenticated-planner-id"],
        "Implementer": ["authenticated-implementer-id"],
        "Reviewer": ["authenticated-reviewer-id"],
        "Coordinator": ["authenticated-coordinator-id"],
    },
    max_review_rounds=3,
    max_attempts=3,
)
jobs = controller.ingest(event)
receipt = controller.event_result(event["event_id"])
job = controller.claim("mac-controller", kinds=["implement", "review", "report"])
# Execute only through a separately configured adapter, then persist its receipt:
controller.mark_dispatched(job["id"], "mac-controller", "actual-external-agent-or-record-id")
controller.complete(job["id"], "mac-controller", {"receipt": "actual-output-reference"})
snapshot = controller.export_snapshot()
```

`complete` 只确认外部执行结果已持久保存，不推断任务阶段。阶段推进仍需一个明确、
验证过的事件。`ingest` 返回新建的 jobs；同一 event_id 的相同内容不重复入队，不同
内容引发 `EventConflict`。未批准或旧版本等语义拒绝会持久保存 `accepted=false`
及 `reason`，返回空 jobs；schema/身份错误引发 `InvalidEvent`。

Windows Planner 可直接提交已完成计划事件。`PLAN_PROPOSED` 会原子地使原 plan
job 失效，避免 Mac 后续误派一次重复规划。Mac claim 可明确排除 `plan`；计划返工
时由 report/共享事件通知 Windows 入口，Mac 不应自行接管 Planner。

## 统一事件外壳

```json
{
  "schema_version": 1,
  "event_id": "globally-unique-and-stable-event-id",
  "task_id": "T-EXAMPLE",
  "repository": "owner/repo",
  "kind": "TASK_OPENED",
  "actor": {"id": "authenticated-planner-id", "role": "Planner"},
  "binding": {
    "requirement_revision": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "plan_version": null,
    "plan_sha": null,
    "plan_blob_oid": null,
    "head_sha": null,
    "base_sha": null,
    "policy_source_sha": "1111111111111111111111111111111111111111"
  },
  "payload": {
    "goal": "Frozen task goal",
    "test_mode": false,
    "entry_agent_id": "actual-windows-entry-agent-id",
    "required_checks": ["offline-smoke"],
    "required_gates": ["Code", "Integration", "Acceptance"]
  },
  "proof": {
    "source": "verified-transport-or-human-console",
    "url": "https://github.com/owner/repo/issues/1#issuecomment-example"
  }
}
```

`binding` 必须恰好含上述七个键，所有 SHA 使用完整小写 40 位值。初始任务不能
预填尚不存在的计划/代码指针。计划事件开始必须有正整数 plan_version、plan_sha
和 plan_blob_oid；实施、head 变化及 Review 事件还须包含 head_sha/base_sha。

## 事件与阶段

- `TASK_OPENED`：User/Planner/Coordinator；冻结 goal、test_mode、必需 Gate/检查及
  回传入口，进入 PLANNING。test_mode 必须显式声明，仍需 User 批准事件。
- `PLAN_PROPOSED`：Planner；给出冻结计划绑定，进入 PLAN_REVIEW，撤销旧批准。
  重要修订必须增加 plan_version；不覆盖旧版后沿用批准。
- `PLAN_APPROVED`：仅 User；`payload.decision` 必须是 `approve`，计划版本/hash/blob/
  policy 必须匹配当前提案。普通任务还要求 `plan_merged: true`，由 adapter 实际验证
  合入主干事实；隔离 test_mode 可按用户已授权的试验范围在分支上运行。
  若 squash 后 plan_sha 改变，必须提供 `payload.proposed_plan_sha` 绑定原提案，
  实际进入主干的计划 SHA 作为新的 plan_sha。批准后进入 IMPLEMENTING。
- `IMPLEMENTATION_READY`：Implementer；完整计划绑定须匹配有效批准，给出当前
  head/base，进入 REVIEWING 并排一个独立 review job。
- `IMPLEMENTATION_BLOCKED`：仅 Implementer；只在已批准且 IMPLEMENTING 时接纳，
  计划字段须匹配，无需尚未产生的 head/base。`payload.summary` 必填；可附
  JSON `evidence`。`payload.outcome` 只允许 PLAN_REVISION_REQUIRED / ENV_BLOCKED /
  NEEDS_HUMAN：分别撤销批准并回 Planner、暂停实施待环境恢复、交用户裁决。
  事件保存为独立 `implementation_blocker`，不会冒充 Reviewer 的 `last_review`。
  计划返工排 plan+report，其他两类只排 report；原实施 jobs 失效。环境恢复继续
  implement，不增加业务审核轮数。
- `HEAD_CHANGED`：Implementer/Coordinator；相同计划的新 head/base 会使旧 Review
  失效，并为新绑定排 Review。同版本重复事件不重复派发。

`IMPLEMENTATION_READY` / `HEAD_CHANGED` 可提供正整数 `payload.implementation_pr`。
核心将它保存为 task 字段，省略或 null 时沿用已知 PR，review 及环境恢复 jobs 均携带
该字段。新计划提案会清空旧实现 PR；PR 本身改变或后补时不复用绑定旧 PR 的排队审核。
未知 PR 仍为 null，transport 必须拒绝在缺少当前 PR 事实时沿用旧通过。
- `REVIEW_COMPLETED`：Reviewer；必须精确匹配当前计划/head/base/policy，有 HTTPS
  共享报告地址 `payload.report_url` 或 `proof.url`。
- `ENV_RESUMED`：User/Coordinator；仅恢复 ENV_BLOCKED。基础设施恢复不增加
  业务 Review 轮数；超出重试限额转 NEEDS_HUMAN。旧外部执行结果不确定时必须先
  核实并提供 `external_execution_stopped: true`，不能把超时当成已经停止。
- `TASK_STOPPED`：User/Coordinator；保留历史，停止任务后拒绝后续状态推进。

Review `payload.outcome` 固定为 PASS / CODE_CHANGES_REQUIRED /
PLAN_REVISION_REQUIRED / ENV_BLOCKED / NEEDS_HUMAN。代码返工回 Implementer；
计划返工撤销旧批准并回 Planner；环境阻塞等待补验；决策分歧回用户。
最多首轮及两次业务复审，仍失败转 NEEDS_HUMAN。

PASS 还必须携带：

```json
{
  "outcome": "PASS",
  "report_url": "https://github.com/owner/repo/issues/1#issuecomment-report",
  "gates": {
    "Code": {"status": "PASS", "reason": "specific evidence"},
    "Integration": {"status": "PASS", "reason": "specific evidence"},
    "Deployment": {"status": "N/A", "reason": "scope does not deploy a service"},
    "Acceptance": {"status": "PASS", "reason": "specific evidence"}
  },
  "required_checks": [{"name": "offline-smoke", "status": "success", "head_sha": "4444444444444444444444444444444444444444"}],
  "blocking_findings": []
}
```

必需 Gate 不能被报告改为 N/A，四 Gate 均不能包含 FAIL/BLOCKED。冻结的必需
检查不能漏掉，CI success 必须属于被审查 head。通过只进入
`READY_FOR_HUMAN_MERGE` 并生成回传 report，始终 `merge_authorized: false`；
不存在 merge job 或 merge 事件。

## 本机并发与故障边界

`BEGIN IMMEDIATE` 原子记录 ingest/claim，唯一 idempotency_key 防止同一阶段的
重复 jobs；同任务同时最多一个 claimed/dispatched job。这是单主机 SQLite
协调，不是跨设备分布式锁。外部写入还需 adapter 使用稳定键和结果核对。

claim 在调用外部 Agent 前即持久保存；重启不会自动抢回 claimed/dispatched job。
如果调用超时，核心不能判断外部任务是否已启动。只有 adapter 确认请求没有被接收
时，才可用 `fail(..., retryable=True)`；已有 external_id 的任务无论如何不能自动
重派。普通错误进入 ENV_BLOCKED，明确恢复并核对旧执行后才可继续。

superseded 只表示旧版本 job 已不再有效，不表示外部进程已被终止。Transport
必须防止旧执行继续向新候选写入；独立 worktree 与精确候选绑定仍不可省略。

核心回归测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s agent-workflow/orchestrator/tests -p test_core.py -v
```

这些测试使用隔离 SQLite 与合成事件，检验状态与去重规则；不能作为跨设备实机
执行成功、真实模型评测或人工计划批准的证据。
