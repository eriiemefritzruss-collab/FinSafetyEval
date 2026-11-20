# T-TEST-001 plan v1 — 隔离 JSON 标记的真实跨设备协作

## 版本绑定与本次授权

- Task / Issue：`T-TEST-001` / https://github.com/eriiemefritzruss-collab/FinSafetyEval/issues/5
- 需求事件：`T-TEST-001:TASK_OPENED:e2e5a6286a110c73fdf1`；原文及冻结快照：https://github.com/eriiemefritzruss-collab/FinSafetyEval/issues/5#issuecomment-7036192159
- 冻结需求 revision：`sha256:f39941454eebfc686262419bd4d15dd3d1360ce7a6e21a2938d887e052610e49`，算法为 TASK_OPENED.payload.goal 原始 UTF-8 字节 SHA-256，不包括 BOM。
- 计划版本：`1`；契约：`plans/T-TEST-001/handoff-v1.json`，与本文件同一计划提交，提交与 blob 由 PLAN_PROPOSED 实际绑定。
- 本次基线：`19bc38c06ef33c7611b0cb75bbd3df5ff8ed5cf8`，Windows 于 2026-11-20 fetch 并通过 ls-remote 核实为当时 `origin/feat/paseo-agent-handoff` 最新已推送提交。
- 政策来源 SHA：`fe7ac7b859d35fcfd027e1eeb2abd47255abf6dc`；既有协议和普通业务规则不变。
- 固定 Windows Planner agent：`20142303-7657-408c-af41-f3cfa3ba8340`。本计划使用单独 `plan/T-TEST-001-v1` worktree，未从 T-001 工作区修改。

用户在本次固定入口对话明确要求开始 T-TEST-001，允许提交、propose 及 `approve --explicit-user-approval` 后等待后台回传。本授权只适用于本任务的白名单隔离 fixture 试验，允许计划未合入时运行；不授权任何 merge、T-001 实施、其他任务或扩大实现范围。Windows 会在计划 commit/push 后通过已部署入口记录本次实际授权；写本文不等于已经发出 PLAN_APPROVED。

## 目标、唯一实现范围与精确输出

验证实际链路：本 Windows 对话提出需求 → GitHub 持久化需求、计划和状态 → Mac 唯一 controller 自动派发实施 → 独立 Reviewer 固定候选验收 → GitHub 报告 → Windows notifier 唤醒此对话 → 读取报告、真实汇报、对同一 notification_event_id 写 ENTRY_ACK。

Mac 实施唯一允许新增的实现文件：`agent-workflow/fixtures/e2e/T-TEST-001.json`。它必须是 UTF-8 JSON 对象，恰好含以下三个字符串字段，无其他字段；JSON 缩进和字段顺序不限：

```json
{
  "task_id": "T-TEST-001",
  "requirement_revision": "sha256:f39941454eebfc686262419bd4d15dd3d1360ce7a6e21a2938d887e052610e49",
  "marker": "FSA_CROSS_DEVICE_OK"
}
```

Planner 只新增本计划和 handoff；不得创建 fixture 或冒充 Implementer/Reviewer。实施提交相对获批计划提交只允许这一个 fixture 变更；不修改业务代码、真实样例、CI、依赖、权限、协作政策和其他任务，不读取 .env/密钥，不调用金融模型 API。计划 PR 与实现 PR 均面向能力分支 `feat/paseo-agent-handoff`，不得 merge。

## AC 与角色步骤

| AC | 可核对条件 |
| --- | --- |
| AC-01 | TASK_OPENED 的 task、需求 hash、entry_agent_id、test_mode 和两项 required_checks 与本计划及 handoff 完全相同；PLAN_PROPOSED 绑定实际计划 commit/blob/policy，PLAN_APPROVED 明确记录本轮用户隔离测试授权。 |
| AC-02 | Mac 独立实施 worktree 从获批 plan_sha 创建；相对该提交仅新增唯一 fixture。实际 JSON 精确等于上述对象；不接受旧任务、未带 sha256: 前缀的 hash 或其他 marker。 |
| AC-03 | 独立 Reviewer 的 agent 与实施者不同，固定同一候选 head/base，读取冻结计划和 diff 并亲跑 handoff.validation_commands。Reviewer 不修改源码或代做实施，报告包含命令、退出码、四 Gate 及实际候选绑定。 |
| AC-04 | 当前候选 head 上的 `workflow-tests (ubuntu-latest)` 与 `workflow-tests (windows-latest)` 均实际 success。不能用本机测试、其他 SHA 的绿灯或旧 Reviewer PASS 替代；head/base 变化使旧验收失效。 |
| AC-05 | Reviewer 结论及证据共享于 GitHub，协调器发布目标为本入口的 ENTRY_NOTIFICATION。queued 仅代表投递回执；通知不是新的需求/授权，不触发另一个任务或自动批准。 |
| AC-06 | Windows 收到自动通知后读取对应 GitHub 报告，向用户报告真实结论、head/base、CI 和未合并状态，再使用入口 ack 写回相同 notification_event_id。只有 GitHub ENTRY_ACK 及 notifier acknowledged 事实才能证明本入口已消费；不能把 Reviewer PASS 提前当成完整往返成功或用户已阅读。 |
| AC-07 | 全程不 merge，不改 T-001 计划和原工作区，不修改政策/CI/权限、不创建额外长期任务。失败或不确定派发不手动替 Mac 重做，不盲目重派；保存证据并回报具体阻塞。 |

实施者仅按唯一 fixture 契约写文件、验证并本地提交，由 Mac controller 推送和创建实现 PR。Reviewer 在另一个固定 head 的 worktree 复跑验证并输出实际报告。Windows 不直接创建/操作 Mac Agent，不手动替代该链路。

## 可复跑验证、反例与 CI

命令参数数组全部冻结在同提交 handoff.validation_commands，由 Mac 实施者及 Reviewer 在各自候选根目录执行，不经过 shell 字符串：

1. 标准库 JSON 检查：读取唯一 fixture，断言实际对象严格等于冻结期望对象，输出 task/hash/marker。
2. 标准库内存反例：以同一精确比较判定分别拒绝错误 task、错误 hash、错误 marker、缺字段、额外字段和非对象；不写坏 fixture、不增加实现路径。
3. `python3 -B -m unittest discover -s agent-workflow/tests -v`。
4. `python3 -B -m unittest discover -s agent-workflow/orchestrator/tests -v`。

CI 必须使用远端现有 workflow 产生的两项精确名称：`workflow-tests (ubuntu-latest)`、`workflow-tests (windows-latest)`。该 workflow 已在冻结能力分支基线中存在；本任务不改 CI。平台跳过须逐项如实记录；本地回归不是跨设备链路已完成的替代证据。

独立 Reviewer 另检查 `git diff --name-status <approved-plan-sha> HEAD` 仅为该 fixture 新增，`git diff --check <approved-plan-sha> HEAD` 无错误，工作区干净；核对实际 base/head 和当前必需 CI。验证中任何异常均不得宣称通过。

## 四 Gate 与完整往返的边界

| Gate | 必须的证据 |
| --- | --- |
| Code | 必需 PASS：唯一实现文件、严格 JSON 内容、冻结 task/hash/marker、干净候选及最小 diff；独立 Reviewer 真实检查。 |
| Integration | 必需 PASS：Reviewer 亲跑 fixture、内存反例和两套标准库测试；两项远端 workflow-tests 在同一候选 head success。 |
| Deployment | N/A：本任务只新增静态隔离 fixture，不发布服务、不部署基础设施、不新增密钥/权限/启动项；Reviewer 给出理由。 |
| Acceptance | 必需 PASS：Reviewer 对本任务 fixture 契约与版本绑定完成实测，明确不替尚未发生的入口回传/ACK 签字。完整跨设备往返另以 AC-05/06 的实际通知、汇报及 ACK 证据闭环。 |

Reviewer PASS 只使任务进入 `READY_FOR_HUMAN_MERGE`，`merge_authorized` 必须仍为 false。完整试验成功还需本固定对话的自动回传和消费 ACK；本次即使全部成功仍不 merge。

## 证据、回退与停止

共享证据全部留在 Issue #5、计划/实现 PR：需求事件与 hash、plan commit/blob/policy、批准事件和本轮授权、Mac实施/Reviewer各自agent与head/base、命令及结果、当前CI、报告URL、notification_event_id、ENTRY_ACK及未合并状态。私有设备路径、token 和配对资料不写入共享计划。

出现范围扩大、hash不符、真实CI失败、旧head/base、环境阻塞或派发结果不确定：停止推进并保留证据，由controller按协议报告；Windows不手动生成fixture或扮演Reviewer修补结果。用户可明确停止本任务；计划与实现均未合入，回退是停止对应任务/关闭本测试PR，保留审计记录，不reset/force-push/删除其他任务。普通业务仍须明确用户批准并实际合入计划PR后才能approve；此次白名单授权不可复用于其他任务。
