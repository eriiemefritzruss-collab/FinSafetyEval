# T-000 协作基础设施搭建计划 v1

- task_id: T-000-bootstrap
- plan_version: 1
- requirement_revision: sha256:e6cfc0b3f609b0f9f7f84eed15ffb7651e5c14e3948cf9bc385e3cefa2401b7d
- requirement_path: plans/T-000-bootstrap/requirement-v1.md
- code_reference_sha: 1ff1d05079e933f79b32f625964a204da86fb340
- policy_version: workflow-v1
- 任务授权：2026-11-20 用户明确请求实施已有协作方案。
- 状态：本次授权的 bootstrap 实施；未声称业务计划已批准或合入 main。

## 实施及范围

仓库当前没有协作模板、门禁或 CI；原 verify_setup.py 缺少样例时仍返回通过。
先增加 agent-workflow/ 下的角色、政策、模板及 Python 标准库一致性核验器；
增加 .github/ 的需求/PR模板和仅运行离线流程测试的 CI。根 .gitignore 排除
本地凭据与 Python 缓存。所有业务代码及数据保持原版本。

核验器只读取人工可信输入和 Git 对象，检查审批/审查/当前版本的一致性与
失败路由，不认证人员身份、CI真实性或当前远端状态，不执行 merge。
运行前由协调人 fetch 并从可信来源取得当前 head/base、有效审批及必需 CI。
测试仅用临时 Git 仓库、合成文件及 fixture，不能充当真实用户批准。

Windows Planner 编写独立 T-001 计划 PR；待用户批准并合入后，Mac 再创建
实施 worktree。Reviewer 在新会话、独立检出固定 SHA 验证，报告放候选分支外。

## 验收契约

- AC-01 / Code：三角色规则、需求/计划与审核模板包含需求hash、plan/blob、
  head/base/integration、政策、四Gate和人工merge边界。
- AC-02 / Code+Integration：以真实临时 Git 对象验证计划祖先/blob、版本绑定，
  完整有效报告输出 READY_FOR_HUMAN_MERGE；无批准、缺字段、过期 head/base、
  失败或缺失 CI、阻塞Gate/finding均不得通过。
- AC-03 / Integration：代码返工→Implementer，计划返工→Planner，环境阻塞→环境负责人；
  fixture正常通过、失败演练有可复跑日志。命令：
  `python3 -m unittest discover -s agent-workflow/tests -v`。
- AC-04 / Code+Integration：CI以只读权限、无模型密钥执行同一离线测试，
  在 Ubuntu / Windows runner 运行；首次实际CI结果记入 PR，未运行不得写通过。
- AC-05 / Acceptance：Windows 本地新会话确认OS与clone、产出真实计划；
  Mac独立审核bootstrap固定SHA。整个业务实施闭环须另记状态，不得提前宣称完成。
- Deployment / N/A：本任务交付仓库协作工具，不部署线上服务。

## 风险、回退与验收边界

人工角色约定不是系统沙箱；共用GitHub账号不能自行Approve；本地核验器不是
可信平台Check。自动化与规则保护后续单独决定。主干未合入前丢弃搭建分支即可回退；
合入后可对搭建提交单独revert，不涉及数据迁移。最多首轮及两次复审；同类阻塞重复
两轮交用户决定。业务计划修订和最终merge保持用户批准节点。
