# T-002 单对话跨设备协作能力补齐

用户在链路审查后明确要求“补齐这种能力，然后进行测试，看是否能满足需求”。
本次请求授权实施审查中已列明的协调、状态共享和可视化缺口，并进行隔离试验。
它不批准T-001业务计划，也不授权任何PR合并。

## 实现范围

基于搭建候选 `fe7ac7b859d35fcfd027e1eeb2abd47255abf6dc`，新增
`agent-workflow/orchestrator/` 下的标准库Python状态机、GitHub/Paseo适配器、
Windows入口与通知器、Mac唯一协调器、可视化及测试，扩展现有低权限离线CI。

采用GitHub作为跨设备事件传输，复用已有身份和两端本机CLI。Windows固定Planner
自然对话驱动入口工具；Mac协调器仅运行已获批任务，Implementation/Reviewer各有
独立会话和工作区；共享报告回传原Windows对话并取得消费ACK。最终merge由用户确认。

## 验收契约

- AC-01：严格事件及精确版本绑定、实际GitHub作者核验、持久化与去重；未批准不实施。
- AC-02：真实Windows原对话发起专用 `T-TEST-001`，GitHub保存计划和本次测试授权事件。
- AC-03：Mac自动领取，隔离分支仅创建一份无害JSON fixture，提交独立实现PR。
- AC-04：独立Reviewer亲跑检查，当前head的必需Ubuntu/Windows CI通过；报告全文共享到GitHub。
- AC-05：Windows本地通知器自动投递原对话，原对话读取报告后生成同版本 `ENTRY_ACK`；queued本身不算通过。
- AC-06：重复/重启、旧head失效、代码返工、计划返工、环境阻塞及回执未知有可复跑测试；不伪称所有故障都做过实机注入。
- AC-07：Paseo可视化展示实际任务阶段和ACK，静态文件/只读刷新服务分清；关键交互与刷新实际验证。

Code/Integration/Acceptance为必需；Deployment指两端本地worker实际启动，不涉及线上服务。
只读本地可视化服务绑定loopback，不暴露整个仓库。

## 隔离测试与回退

`T-TEST-001` 在本机白名单内；计划分支可按本次测试授权执行，不借此绕过普通任务的
计划合入要求。唯一实施路径为 `agent-workflow/fixtures/e2e/T-TEST-001.json`；
不修改金融业务代码、正式数据、T-001或PR #2，不执行merge。

停止本机worker并关闭测试PR即可停止接力；共享事件和SQLite留作证据。
未知执行不靠租约或超时重派，不停止其他用户进程。后续业务任务仍须明确批准冻结计划。
