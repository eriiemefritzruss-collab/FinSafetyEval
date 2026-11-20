# FinSafetyEval 跨设备人工接力试点

本目录落实阶段 A：Windows Planner → 用户批准计划 → Mac Implementer → 独立 Reviewer → 用户确认合并。
当前 bootstrap 获本次搭建请求授权；不代表后续业务计划已批准。尚未启用 Hub、watcher 或自动合并。

## 三个角色入口

- Windows 在本地主机 clone 业务仓库，创建独立 Planner 会话，读取 `roles/planner.md`。
- Mac 在获批计划进入 main 后，创建独立 worktree，读取 `roles/implementer.md`。
- Reviewer 新建会话与固定 SHA 的独立 worktree，读取 `roles/reviewer.md`。
- 任务/计划与审核模板见 `templates/`；完整状态与四 Gate 政策见 `policies/workflow.md`。

客户端 Hosts 的连接列表与 daemon 的 fleet 调度注册表分开保存。
若 UI 已显示远端 Online，但 `list_hosts` 为空或 remote serverId 返回 `Unknown host`，
可在 UI 新建 workspace 选择远端主机，确认新会话实际 OS/cwd 后人工接力。
不能把 Mac 上新建的会话称为 Windows Planner。
配置 daemon 调度连接需单独核验目标，不修改或复用用户的旧会话，不为接力暴露公网端口。

## 本地验证

仓库根目录运行（Windows 将 `python3` 换为已确认的 `python`）：

```bash
python3 -m unittest discover -s agent-workflow/tests -v
python3 agent-workflow/check_handoff.py --help
```

测试使用临时 Git 仓库与合成计划/报告，不调用真实模型 API。
覆盖正常通过、代码返工、计划返工、环境阻塞、版本漂移及缺少有效证据。
fixture 中的人工批准/CI成功只用于检验规则，不能作为真实任务获批或真实 CI 已运行的证据。
仓库 CI 在 Ubuntu 和 Windows 上运行这些合同测试；不证明金融模型评测准确率或生产可部署。

## 如何检查真实交接

先由协调人 `git fetch origin`，从当前 PR/GitHub 检查读取真实 head、base 和 CI；
从受控审批记录读取当前有效计划版本、批准人及合入 main 的 SHA。
将三份 JSON 存在候选目录之外，运行：

```bash
python3 agent-workflow/check_handoff.py --repo . \
  --approval ../approval.json --review ../review.json --current ../current.json
```

三份记录均绑定 `task_id, repository, requirement_revision, plan_version,
approved_plan_sha, plan_path, plan_blob_oid, code_base_sha, policy_version`。
`requirement_revision` 使用冻结需求文件原始 UTF-8 字节的 `sha256:<64位十六进制>`。

approval 另含 `approved_by`、`required_gates`（四 Gate 的子集）、`required_checks`
（非空 CI 名称数组），可使用 `require_integration_commit: true` 要求真实合并测试提交。
current 另含 `pr_head_sha, tested_base_sha`；review 绑定 current 全部字段，再提供
`tested_integration_sha`（无合并测试为 null）、`overall`、四项 `gates`、
`required_checks: [{"name": "检查名称", "status": "success", "head_sha": "完整SHA"}]`
及 `blocking_findings` 数组。每项 gate 是 `{"status": "PASS", "reason": "可定位证据"}`；
未要求且不适用的 gate 也需记录 N/A 的理由。

该命令输出 `advisory: true`：它只核对人工可信输入与本地 Git 对象，
不会主动取回 GitHub、认证批准人、证明 CI 真伪、确认有效计划是否已被替换，或执行 merge。
不能直接作为平台强制门禁。可信输入、最新远端版本与身份仍由协调人核实；
政策/核验器必须来自可信版本，不能让候选 PR 的自定义规则决定自身通过。
即使输出 READY_FOR_HUMAN_MERGE，最终合并仍由用户确认。

## 本次交付边界

T-000-bootstrap 仅搭建流程。T-001 是独立业务试点计划，尚未经用户批准时禁止实施。
独立 review 报告放 PR 评论/独立证据目录，不提交回候选分支改变 head。
不将本地绝对路径、设备配对链接、token、`.env` 或真实评测输出提交仓库。
