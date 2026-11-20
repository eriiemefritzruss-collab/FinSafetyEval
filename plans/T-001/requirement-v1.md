# T-001 requirement v1 — 仓库样例的离线、跨工作目录验证

- 状态：PROPOSED / 待用户批准；不是实施授权或验收结论。
- 需求版本：requirement-v1；配套计划：plan-v1。
- 仓库：https://github.com/eriiemefritzruss-collab/FinSafetyEval
- main 代码参考 SHA：`1ff1d05079e933f79b32f625964a204da86fb340`。
- 角色：Windows Planner 出计划；Mac Implementer 实施；独立 Reviewer 验收；用户最终批准及决定合并。
- 冻结方式：本文 UTF-8、无 BOM、LF 原始文件字节的 SHA-256 记录于 plan-v1.md。本文不包含自身 hash，避免自引用。批准必须绑定需求 hash、计划版本、计划提交和代码参考 SHA；需求变更另起版本，不覆盖 v1。

## 目标与已核实问题

让 `verify_setup.py` 以绝对路径从任意工作目录启动时，只检查它所属仓库的 `data/input/sample_data.jsonl`，并让缺少或损坏的样例明确失败。全部验证保持离线，不调用真实模型 API，不需要模型密钥。

上述 SHA 的 `verify_setup.py:120-144` 使用 cwd 相对路径，缺失时返回 True，空白文件也会返回 True；只检查三个字段是否存在。该 SHA 没有跟踪 `data/input/sample_data.jsonl`，因此不能把“从根目录运行成功”理解为样例已被验证。本需求提议实施时补入两条人工编写的无害本地 smoke 样例，不借用大型交付数据，不调用模型生成。

## 验收标准

| 编号 | 必须满足的行为 |
| --- | --- |
| AC-01 | 样例唯一来源是 `Path(__file__).resolve().parent / "data/input/sample_data.jsonl"`。根目录、仓库内子目录、仓库外目录（含空格及中文）运行同一绝对脚本，均读取同一文件；不更改进程 cwd，不回退读取 cwd 的同名文件。成功及失败日志均显示解析后的绝对样例路径。 |
| AC-02 | 仓库样例缺失、路径为目录或读取发生 OSError（包括 PermissionError）时，数据检查返回 False，CLI 汇总为“数据格式”失败且退出码为 1；明确记录路径和失败原因，不跳过、不自动下载或创建数据。 |
| AC-03 | 损坏包括非法 UTF-8、任意非空行的非法 JSON、JSON 顶层不是对象、缺少任一必需字段、任一必需字段不是去除首尾空白后非空的字符串，以及整个文件没有有效非空记录。必需字段保持为 `case_id`、`user_prompt`、`risk_type`。这些情况数据检查返回 False、CLI 退出 1；行级错误日志包含物理行号（统一写成“第N行”）和原因，文件级错误包含路径和原因。不得仅以 traceback 代替明确诊断。 |
| AC-04 | UTF-8 JSONL 中的空白行可忽略；至少有一条合格对象即可，额外字段可保留。提供两条无害、人工编写、具有不同 case_id 的 smoke 样例（含中文，用于格式检查，不宣称是金融安全基准）；正常仓库 CLI 的五项检查全部通过、退出 0，并记录实际有效条数。 |
| AC-05 | 现有导入、路由、模拟评分解析、ASR 检查和新增验证均不初始化 ModelClient、不调用 chat/simple_query、不访问真实模型 API。验证守卫拦截网络和派生进程，模型调用入口使用计数后抛错的守卫；即使异常被被测脚本捕获，任何尝试计数大于 0 也使验收失败。不得读取、打印或请求真实密钥。 |
| AC-06 | 用实际 CLI 子进程验证路径选择：仓库样例有效而 cwd 同名样例损坏时仍成功；仓库样例缺失或损坏而 cwd 同名样例有效时仍失败。正反例均验证绝对路径日志、数据检查汇总和退出码，不能只检查源码或 mock 路径。 |
| AC-07 | 改动限于 plan-v1 的实施 allowlist；不改变现有其他四项验证语义、不改业务模块、真实数据、模型配置、依赖、CI、权限和协作政策。单元及 CLI 验证可重复运行，不修改候选仓库或其他项目文件；坏样例仅出现在测试拥有的临时副本。 |
| AC-08 | Mac Implementer 与独立 Reviewer 使用同一候选提交及本文 hash。Reviewer 独立复跑四 Gate 的必要检查；Acceptance 必须包含 Mac 与 Windows 的实际跨目录行为证据。记录 host/OS、Python、提交、命令、cwd、样例 hash、退出码、关键日志和零网络/模型调用计数。Planner 不代替 Reviewer 或用户签字；用户才可最终批准及决定 merge。 |

## 边界及交付

当前 Windows 任务只创建 `plans/T-001/requirement-v1.md` 和 `plans/T-001/plan-v1.md`，在 `plan/T-001-v1` 分支提交，凭既有凭据创建 docs-only draft PR；不修改任何代码、数据或政策，不合并，不批准自身计划。

后续实施以用户对冻结计划的明确批准为前提；不得把通知 Mac、PR 已创建或测试通过视为批准。若实际候选基线不同或出现额外改动需求，停止扩大范围，提交新版需求/计划供用户决定。

四 Gate 为 Code、Integration、Deployment、Acceptance。Code 和 Integration 必需；本试点无部署，Deployment 可给出 N/A 及理由，仍需 Reviewer 确认；Acceptance 不得用文档审阅替代实际跨目录执行。具体步骤、反例、证据和回退见 plan-v1.md。
