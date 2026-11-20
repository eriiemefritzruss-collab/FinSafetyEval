# 单入口、GitHub事件、双端本地执行

Windows 的固定 Planner 对话负责提出任务、冻结计划和处理用户决定。Mac 的唯一
`runtime.py` 从 GitHub 读取任务事件，按批准的精确版本创建独立实施/审核工作区。
Windows 的 `notifier.py` 只把共享报告投递回固定对话，不领取实施任务。

这条传输路径复用已有 GitHub 凭据与各自本机 Paseo CLI，不依赖 daemon fleet 的
peer配对资料。角色使用同一 GitHub 登录时，属于职责分工，不构成独立账号权限隔离。
所有候选和最终 merge 仍由用户确认，没有自动 merge 入口。

## 状态和证据

- Issue正文标记 `<!-- finsafety-orchestration-task:v1 -->` 便于发现任务。
- 评论中的事件标记与JSON协议见 `CORE_PROTOCOL.md`。消费时核对实际GitHub作者，
  不能仅信正文自报角色；event_id重复不会重复派发，内容矛盾会拒绝。
- 计划绑定需求hash、计划commit/blob及政策来源commit，实施/审核再绑定head/base。
- 生产计划必须实际合入主干，且读取合入commit中的计划blob；单纯评论“已批准”不能替代这些事实。
- 低风险隔离测试须在本地 `allowed_test_tasks` 中列名，并显式记录本次用户测试授权。
  这不会授权其他任务，也不改变T-001的待审状态。
- 审核报告全文写到GitHub，新的head/base使旧报告失效。结果回传有独立的
  `ENTRY_NOTIFICATION → ENTRY_ACK`，queued不等于已消费，更不等于用户已阅读或批准。

## Planner入口命令

`entry.py` 可以通过绝对路径执行；配置文件存于本机运行目录，不提交凭据或设备私有路径。
先用 `health --config <entry.json>` 检查账号、仓库和固定Agent。新任务采用：

```text
entry.py open --config <entry.json> --task-id <T-xxx> --goal-file <UTF8需求快照>
entry.py propose --config <entry.json> --issue <N> --task-id <T-xxx> --plan-sha <完整commit> --plan-path plans/<T-xxx>/plan-v1.md --plan-version 1 --plan-pr <N>
entry.py approve --config <entry.json> --issue <N> --task-id <T-xxx> --explicit-user-approval
```

`approve` 仅在用户明确批准相应版本时调用。它核对实际merged plan PR与主干祖先，
不会执行merge。Planner应在该计划commit中同时保存
`plans/<T-xxx>/handoff-v1.json`：

```json
{
  "allowed_paths": ["src/example.py", "tests/test_example.py"],
  "validation_commands": [["python", "-m", "unittest", "tests.test_example"]]
}
```

这些是精确相对文件路径与参数数组，不接受通配符、Git元数据目录或shell字符串。
实现者发现计划不能满足要求时使用 `IMPLEMENTATION_BLOCKED`，回流Planner，不冒充独立Reviewer。

固定Windows对话在收到通知后读取共享报告、汇报版本及结论，再执行通知所附的
`entry.py ack`。通知不是新用户命令，不授予实施或合并批准。

## 运行服务

Mac（只允许一个实例拥有同一状态目录）：

```bash
python3 runtime.py --config /absolute/mac-runtime.json --watch --interval 10
python3 runtime.py --config /absolute/mac-runtime.json --stop
```

Windows：

```powershell
python -B notifier.py --config D:\path\notifier.json --watch --interval 10
```

Windows官方Paseo桌面包通常自带 `resources\bin\paseo.cmd`，可显式配置该路径，
无需改PATH。Mac也建议配置已实测的官方内置CLI路径。服务不新增开机启动或修改系统权限；
登出/重启后由启动命令恢复，SQLite保留已消费与未确认记录。

配置要点：

- Mac：repository、repo_path、state_dir、worktree_root、controller_login、entry_agent_id、
  provider、paseo_binary、implementation_base_branch、allowed_test_tasks、windows_entry_script、windows_config_path。
- Windows入口：repository、repo_path、controller_login、entry_login、entry_agent_id、policy_source_sha、
  required_checks、required_gates、allowed_test_tasks、task_issues（任务ID到Issue号的映射）、notifier_config_path。
- Windows通知器：repository、repo_path、controller_login、entry_login、entry_agent_id、paseo_binary、
  state_dir、task_issues（Issue号数组）。入口创建新任务时会原子扩展通知器scope。

Mac状态机和独占锁只保证单主机协调，不是分布式锁。未知launch receipt不自动重派；
未知GitHub写入回执先按event_id只读核对。休眠/断线后恢复前先核对已有Agent，超时不代表旧执行已经停止。

## 可视化与验证

协调器原子生成 `snapshot.json` 与自包含 `协作运行看板.html`，包含真实阶段、责任人、
版本、派发账本、报告、通知及ACK。只读本地HTTP服务仅绑定127.0.0.1，仅提供这一份HTML，
HTTP视图每10秒重新加载，`/?paused=1`可暂停。文件快照不自动刷新；不含CDN、fetch、iframe或localStorage。
HTTP地址只适用于运行服务的本机客户端；另一台设备使用GitHub证据或自己的导出快照，不把Mac的localhost当远端地址。

```bash
python3 -m unittest discover -s agent-workflow/orchestrator/tests -v
```

单测中的模拟服务与真实跨设备验证必须分别记录。服务测试不会调用金融模型或改写正式样本。
