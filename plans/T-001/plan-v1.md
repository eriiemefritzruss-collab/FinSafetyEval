# T-001 plan v1 — Windows 规划 / Mac 实施 / 独立验收

## 冻结与状态

| 项目 | 冻结值 |
| --- | --- |
| 状态 | PROPOSED / 待用户批准；未实施、未验收、未合并 |
| 计划版本 | plan-v1（对应 requirement-v1） |
| 需求文件 | `plans/T-001/requirement-v1.md` |
| 需求 SHA-256 | `dcc4c99136bb91e3097193db724535a99a6981f3a10107d0962bd169202c0ef1` |
| 代码参考 / main SHA | `1ff1d05079e933f79b32f625964a204da86fb340` |
| 计划分支 / PR 类型 | `plan/T-001-v1` → `main`；docs-only draft PR |
| 代码仓库 | https://github.com/eriiemefritzruss-collab/FinSafetyEval |
| 执行顺序 | 用户批准冻结计划 → Mac 实施 → 独立 Reviewer 四 Gate → 用户最终决定合并 |

需求 hash 使用 UTF-8、无 BOM、LF 文件的原始字节。提交后以 Git blob 为跨平台权威；若 checkout 转换了换行，不能把变更后的工作区 hash 当成另一需求。可从仓库根目录复核，不输出凭据：

```powershell
@'
import hashlib, pathlib, subprocess
rel = 'plans/T-001/requirement-v1.md'
blob = subprocess.check_output(['git', 'show', 'HEAD:' + rel])
expected = 'dcc4c99136bb91e3097193db724535a99a6981f3a10107d0962bd169202c0ef1'
assert hashlib.sha256(blob).hexdigest() == expected
checkout = pathlib.Path(rel).read_bytes()
assert checkout.replace(b'\r\n', b'\n') == blob  # tolerate checkout-only CRLF conversion
print('requirement-v1 SHA-256 OK')
'@ | python -B -X utf8 -
```

Mac 用 `python3 -B -X utf8 - <<'PY'` 执行同一 Python 内容。若批准后的实施分支不包含此计划提交，改用明确的计划提交 SHA 读取 blob，并保留批准记录；不依赖浮动 HEAD 解释用户批准。计划提交 SHA 和 PR URL 在 PR 元数据及交付报告中记录，避免把自身提交 SHA 写回文件形成自引用。任何范围/需求变更发布 v2 并重新请用户批准。

## 本机、基线与实测事实

2026-11-20 Windows Planner 实测：

- Host：`jinzhiheng-pc`；OS：Microsoft Windows 10 企业版，10.0.19045，64 位。
- 初始 cwd：`D:\联动Agent搭建`；目标目录事先不存在；仅 clone 至 `D:\联动Agent搭建\FinSafetyEval-pilot`，未覆盖其他项目。
- Git 2.56.1.windows.1；Codex CLI 0.170.0；Python 3.12.10。Paseo CLI 和 gh 未在 PATH 中找到；Paseo MCP 可调用，self 为 `srv_C1Cd2zI-VYyW`。
- clone 的 HEAD、origin/main 与 `git ls-remote origin refs/heads/main` 均为冻结代码 SHA。克隆初始工作区干净；未发现仓库 AGENTS.md 或已跟踪的 plans/、.github/ 协作约束文件。角色和四 Gate 按本次用户指令拟定，不声称修改了既有协作政策。
- 既有 GitHub connector 账号可读取仓库并报告 push 权限；禁用交互登录的 Git push dry-run 成功。只使用既有凭据，不登录新账号，不展示 token。Git 全局 author 未配置；本次文档提交使用命令级 `Codex Windows Planner` 自动化身份，不修改全局配置或权限。
- 没有安装依赖或配置模型密钥。虽然若干 requirements 中的第三方包未安装，当前 verify 的实际执行路径只需标准库及仓库模块，导入检查实际成功；不将其解释为所有业务依赖已齐备。
- Paseo `list_hosts` 返回目标注册列表为空；用目标 `srv_AhOCGb492Tw-` 查询 Mac 会话 `72930b24-1fd1-4bd2-b791-b950a4bfaeac` 返回 `host_unknown`。最终仍尝试发送一次结果报告；失败不影响本地文档，不注册新 host、不启动 watcher/schedule。

已读取 README.md、requirements.txt、verify_setup.py 及其导入/调用链：`src/core/{model_client,evaluator,judge}.py`、`src/prompts/{__init__,loader,judge_prompts}.py`、`src/utils/{__init__,metrics}.py`、`src/review/{__init__,exporter}.py`，以及 loader 实际读取的四个 judge prompt 文本。要点：

| 代码位置（冻结 SHA） | 核实结论 / 计划影响 |
| --- | --- |
| README 快速开始及框架结构 | 文档中的正式输入不是 verifier 的 sample_data.jsonl，不能擅自替换为大型真实交付数据，也不能运行评测或数据生成命令作为 setup 验证。 |
| verify_setup.py:8、120–144 | src 导入位置已依脚本目录设置，但数据路径仍依 cwd；缺失返回 True；空文件/全空白返回 True；字段仅检查存在。 |
| verify_setup.py:147–193 | main 捕获检查异常，汇总任意 False 后退出 1；保留此汇总机制，无需改业务模块。 |
| src/prompts/loader.py:6 | prompt 路径已经依 `__file__.resolve()` 定位，是本任务可沿用的仓库本地路径模式；不修改。 |
| src/core/model_client.py | OpenAI SDK 在客户端初始化时才导入；真实网络请求在 chat；verify 只导入类，不初始化客户端。 |
| verify 的评分与 ASR 测试 | MultiJudgeSystem 用 __new__ 构造，仅解析本地字符串；ASR 用内存 mock 数据。其他四项本次已实际通过。 |
| Git tree 的 data/input | 不存在 sample_data.jsonl。实施需显式补入小型无害样例，否则修好“缺失即失败”后新 clone 永远无法全通过。 |

当前实测使用绝对脚本路径和网络/派生进程审计守卫，`-B -X utf8` 禁止 pycache 并保证日志编码：

| 实际 cwd | 退出码 | 数据格式结果 | 网络/派生进程尝试 |
| --- | --- | --- | --- |
| `D:\联动Agent搭建\FinSafetyEval-pilot` | 0 | 警告缺失，但汇总通过 | 0 |
| `D:\联动Agent搭建` | 0 | 警告缺失，但汇总通过 | 0 |
| `D:\联动Agent搭建\FinSafetyEval-pilot\src` | 0 | 警告缺失，但汇总通过 | 0 |

另以 unittest.mock 的内存 open/exists 反例调用原 test_data_format（未写入样例）：empty、blank_only、null_fields 返回 True；缺字段返回 False；非法 JSON/UTF-8 分别抛 JSONDecodeError/UnicodeDecodeError，由现有 main 捕获才失败。上述是未修复基线证据，不是 T-001 验收通过。

## 范围及实施步骤（批准之后由 Mac 执行）

当前 Planner 的全部新增路径只有两份计划文档。未来实现单独分支（建议 `impl/T-001-v1`）、单独 PR，必须基于明确用户批准的 plan-v1 和代码 SHA；不得在计划分支混入实现。

未来实施 allowlist 精确为：

1. `verify_setup.py`：只改变样例定位、内容验证与相应日志；保留其余四项检查和 main 的失败汇总语义。
2. `data/input/sample_data.jsonl`：新增两条人工无害 smoke 记录。每条含三个非空字符串字段，case_id 不同，至少一条含中文；内容如“请解释风险提示的作用”，仅供格式检查，不包含真实个人数据或模型输出。
3. `tests/test_verify_setup.py`：新增标准库 unittest 回归测试和离线守卫，覆盖 AC-01 至 AC-07；临时文件使用测试拥有的 TemporaryDirectory，结束清理，不碰真实样例或其他项目。

不改 README、requirements、src/ 业务模块、既有 prompts、真实数据、模型配置、CI、权限、分支保护或协作政策。不新增自动运行服务。若必须增加路径、依赖或调整其它验证，不能自行扩大 allowlist。

实施顺序：

1. 核对批准记录中需求 hash、plan-v1、计划提交和代码 SHA。只读检查本地工作区及 main 漂移；若不再匹配，记录差异并提新版/由用户决定，不能悄悄换基线。
2. 将样例路径定为 `Path(__file__).resolve().parent / "data/input/sample_data.jsonl"`，不调用 chdir，不读取 cwd 备用文件。开始验证就记录绝对路径。
3. 以该 Path 的 open 方法按 UTF-8 读取；统计物理行号及合格非空记录数。逐行校验 JSON 对象和三个字段的非空字符串类型；错误日志使用“第N行”。文件为空或仅空白时明确失败。捕获 OSError、UnicodeError、JSONDecodeError 并返回 False，保留合理上下文；不吞掉异常当作成功、不自动修复数据。输出实际合格记录数。
4. 新增样例及 unittest；PermissionError 用仅针对样例 Path.open 的 mock 注入，不修改 OS 权限或 ACL。检查 test_data_format 本身 False 及 CLI 汇总/退出码，避免只靠其他检查失败让负例误通过。
5. 依下述命令及矩阵离线复跑；把候选 SHA、证据附于实现 PR。不得声称自己完成独立 Reviewer 验收。

## 可复跑验证与反例

先记录本机 `hostname`、OS、Python 版本、`git rev-parse HEAD`、需求 hash、`git status --porcelain=v1 --untracked-files=all`，确认不暴露环境变量或凭据。以下 Python 命令 Mac 换为 python3；不安装新依赖，不运行 README 的真实模型评测/生成命令。

```text
python -B -X utf8 -m unittest discover -s tests -p test_verify_setup.py -v
git diff --check <批准的代码基线SHA>...HEAD
git diff --name-status <批准的代码基线SHA>...HEAD
```

单元测试至少包含：每个必需字段分别缺失、null、数字、空字符串及纯空白字符串；JSON 的 null/数值/列表/字符串顶层；非法 UTF-8；非法 JSON；零字节；仅空白；目录；缺失；模拟 PermissionError；正例含空白行及额外字段。PermissionError 要检查失败原因日志而不只检查 False。若新测试在冻结基线运行，应暴露现有缺陷；不能因为基线没有 tests 文件而声称红测成功。

下面是独立 Reviewer 可直接复制的 CLI 验证程序（保存行为仅发生于批准后的验收阶段；当前 Planner 不创建第三份文件、不运行此临时副本矩阵）。先切到候选仓库根目录，将 Python 内容用 stdin 交给解释器：Windows 用 `@' ... '@ | python -B -X utf8 -`，Mac 用 `python3 -B -X utf8 - <<'PY' ... PY`。完整 stdout/stderr 需作为 PR 证据保存或附于评论；本程序不会写候选仓库。

```python
import hashlib, json, pathlib, platform, re, shutil, subprocess, sys, tempfile

root = pathlib.Path.cwd().resolve()
assert (root / 'verify_setup.py').is_file(), 'Run from candidate repo root'
candidate = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
original = root / 'data/input/sample_data.jsonl'
shipped = original.read_bytes()  # missing candidate sample is a failure
assert shipped.strip(), 'shipped sample is empty'
sample_hash = hashlib.sha256(shipped).hexdigest()
print(json.dumps({'host': platform.node(), 'os': platform.platform(),
                  'python': sys.version, 'candidate': candidate,
                  'sample_sha256': sample_hash}, ensure_ascii=False))

# Child-only guards: count attempts even if verify catches the exception.
guard = r'''
import json, pathlib, runpy, sys
counts = {'network': 0, 'process': 0, 'model': 0}
def deny(kind):
    counts[kind] += 1
    raise RuntimeError('T001_OFFLINE_GUARD_' + kind)
def audit(event, args):
    if (event.startswith('socket.') and event != 'socket.__new__') or event == 'urllib.Request':
        deny('network')
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.spawn', 'os.exec'}:
        deny('process')
sys.addaudithook(audit)
try:
    sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve().parent / 'src'))
    from core.model_client import ModelClient
    def blocked_model(*args, **kwargs):
        deny('model')
    for name in ('__init__', 'chat', 'simple_query'):
        setattr(ModelClient, name, blocked_model)
    runpy.run_path(sys.argv[1], run_name='__main__')
finally:
    print('T001_GUARD=' + json.dumps(counts))
    if any(counts.values()):
        raise SystemExit(97)
'''

def check(repo, cwd, expected, label, line=None):
    script = (repo / 'verify_setup.py').resolve()
    sample = (repo / 'data/input/sample_data.jsonl').resolve()
    p = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', guard, str(script)],
                       cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)
    log = p.stdout + p.stderr
    print(json.dumps({'case': label, 'candidate': candidate, 'cwd': str(cwd),
                      'sample': str(sample), 'exit': p.returncode,
                      'stdout': p.stdout, 'stderr': p.stderr}, ensure_ascii=False))
    markers = [x for x in p.stdout.splitlines() if x.startswith('T001_GUARD=')]
    assert len(markers) == 1, 'guard result missing'
    assert json.loads(markers[0].split('=', 1)[1]) == {'network': 0, 'process': 0, 'model': 0}
    assert p.returncode == expected, (label, p.returncode, expected)
    assert str(sample) in log, 'resolved canonical sample path must be logged'
    assert re.search(('通过' if expected == 0 else '失败') + r'\s+数据格式', log)
    for name in ('模块导入', '场景提示词路由', '评分解析', 'ASR计算'):
        assert re.search('通过' + r'\s+' + name, log), name
    if line is not None:
        assert '第' + str(line) + '行' in log, 'physical line diagnostic missing'

valid = b'{"case_id":"DECOY","user_prompt":"local only","risk_type":"smoke"}\n'
with tempfile.TemporaryDirectory(prefix='T001-') as owned:
    sandbox = pathlib.Path(owned).resolve()
    external = sandbox / 'unrelated space 外部'
    external.mkdir()
    decoy = external / 'data/input/sample_data.jsonl'
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b'{broken decoy\n')
    for cwd in (root, root / 'src', external):
        check(root, cwd, 0, 'actual-shipped-sample-with-invalid-cwd-decoy')

    repo = sandbox / 'repo space 验收'
    repo.mkdir()
    shutil.copy2(root / 'verify_setup.py', repo / 'verify_setup.py')
    for name in ('src', 'prompts'):
        shutil.copytree(root / name, repo / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    assert (repo / 'verify_setup.py').read_bytes() == (root / 'verify_setup.py').read_bytes()
    sample = repo / 'data/input/sample_data.jsonl'
    sample.parent.mkdir(parents=True)
    sample.write_bytes(shipped)
    nested_decoy = repo / 'src/data/input/sample_data.jsonl'
    nested_decoy.parent.mkdir(parents=True)
    nested_decoy.write_bytes(b'{broken nested decoy\n')
    for cwd in (repo, repo / 'src', external):
        check(repo, cwd, 0, 'copied-shipped-sample-with-invalid-cwd-decoy')

    # Valid decoys must not mask any failure of the canonical repository sample.
    decoy.write_bytes(valid)
    nested_decoy.write_bytes(valid)
    cases = [('missing', None, None), ('directory', 'directory', None),
             ('empty', b'', None), ('blank', b' \n\n', None),
             ('invalid-utf8', b'\xff\n', None),
             ('invalid-json-at-line-3', valid + b'\n{\n', 3)]
    for value in (None, 1, [], 'text'):
        cases.append(('non-object-' + repr(value),
                      (json.dumps(value) + '\n').encode('utf-8'), 1))
    record = {'case_id': 'T001', 'user_prompt': 'local smoke', 'risk_type': 'smoke'}
    for key in record:
        missing = dict(record)
        del missing[key]
        cases.append(('missing-' + key, (json.dumps(missing) + '\n').encode(), 1))
        for value in (None, 42, '', '  '):
            invalid = dict(record, **{key: value})
            cases.append(('invalid-' + key + '-' + repr(value),
                          (json.dumps(invalid) + '\n').encode(), 1))
    for label, payload, line in cases:
        if sample.is_dir():
            sample.rmdir()  # only the empty directory created by this test
        elif sample.exists():
            sample.unlink()  # only sandbox-owned fixture
        if payload == 'directory':
            sample.mkdir()
        elif payload is not None:
            sample.write_bytes(payload)
        for cwd in (repo, repo / 'src', external):
            check(repo, cwd, 1, label + '-with-valid-cwd-decoy', line)
    if sample.is_dir():
        sample.rmdir()
    elif sample.exists():
        sample.unlink()
    sample.write_bytes(b'\n' + valid + b' \n' +
                       b'{"case_id":"T002","user_prompt":"local",'
                       b'"risk_type":"smoke","extra":true}\n')
    for cwd in (repo, repo / 'src', external):
        check(repo, cwd, 0, 'valid-extra-fields-and-blank-lines')

assert hashlib.sha256(original.read_bytes()).hexdigest() == sample_hash
print('T001 CLI matrix passed; this alone is not user approval or all-Gate acceptance')
```

程序基于同一候选 verify/src/prompts 的真实副本改变磁盘样例，不 mock 路径或数据内容；由 root/src/外部目录实际启动 CLI。临时目录只由 TemporaryDirectory 管理，不更改原仓库。除矩阵外，Reviewer 必须核对成功日志的实际有效条数，并复跑 PermissionError 单测。程序应可重复运行且不依赖上次遗留状态；一次完整矩阵通过后，仅在变更、失败或未解决的问题需要时重跑。

基线复跑：在冻结 SHA 的干净 clone 中，把上面 guard 的 ModelClient 入口守卫一并使用，以 root、root/src、仓库父目录为 cwd 调用 verify，记录“缺失样例、退出 0”。基线没有样例，因此不能直接运行候选正例矩阵并把 FileNotFoundError 当作完整基线证据。上述实测表已证明基线缺陷；实现后必须改为缺失即退出 1。

## 四 Gate 与交接证据

所有状态现在均为待验证；以下为拟定判据，不代表已获批准。

| Gate | 必要性 / 负责人 | 通过标准与证据 | 当前状态 |
| --- | --- | --- | --- |
| Code | 必需 / 独立 Reviewer | 绑定批准的需求 hash、计划版本、计划提交、代码基线及候选 SHA；审阅 allowlist、最小 diff、路径解析、失败处理和离线调用链；unittest 覆盖 AC-01–07，diff --check 通过。 | PENDING |
| Integration | 必需 / Mac 执行，Reviewer 独立复跑 | 在干净候选 checkout 完成全部五项真实 CLI 检查；正例 0、负例 1、其他四项均通过；网络/模型/派生进程尝试为 0。记录 Python/OS、完整命令及输出、sample hash；不得只靠 mock 测试。 | PENDING |
| Deployment | 无服务、发布或部署；由 Reviewer 确认 | 仅 verifier、本地 smoke fixture、测试变更，没有运行时服务/基础设施/密钥/权限变动，给出 N/A 理由；N/A 不替代其他 Gate。 | N/A PROPOSED，待确认 |
| Acceptance | 必需 / 独立 Reviewer；用户最终决定 | 同一候选 SHA 在 Mac 与 Windows 实际完成 AC-01–08 跨目录、损坏/缺失和 cwd 诱饵矩阵，含空格中文路径；检查日志路径、物理行号、条数及退出码，附零调用计数。Reviewer 给明确 PASS/FAIL 及缺陷，用户最后批准及决定 merge。 | PENDING |

证据用实现 PR 描述/评论承载，不在本次 docs-only 分支新增日志文件：任务 T-001、需求 hash、plan-v1、批准记录链接、计划提交/PR、代码基线、候选 SHA、host/OS/Python、命令、cwd、canonical sample 路径和 hash、退出码、关键日志、守卫计数、AC 对照、四 Gate 结论、Reviewer 身份及时间。候选提交变化后旧证据失效，须对新提交重新完成必要检查。任何 Gate 失败，不能进入用户最终合并确认。

## 风险、回退及停止条件

- 主要兼容性变化是此前缺失或空样例的“假绿”会变红；新增两条明确的本地 smoke fixture 为干净 clone 提供正例。更严格字段类型仅用于 verifier 样例，不变更业务输入 schema 或正式数据。
- 分支/路径已存在、工作区不干净、批准信息不匹配、main 漂移、需要扩大 allowlist、守卫捕获任何网络/模型请求：先停止相应写入或实施，保留证据，交用户决定。无远端写权限时保留本地提交，准确报告受阻，不登录新账号、不尝试改权限。
- 本次计划若被否决：保留隔离 clone/本地提交用于追溯；用户可关闭 draft PR。Planner 不删除其他项目、不强推、不重置已有分支、不合并。
- 实现尚未合并：停止或关闭实现 PR，保留 main 原状；不做 destructive reset。若用户未来已合并而需撤回，另建恢复 PR，用 `git revert <明确的实现提交SHA>` 撤回 verify、fixture、tests 的实施变更，复核 diff/离线验证后交 Reviewer 和用户；此为回退方案，不是当前执行授权。
- 当前任务到 docs-only 计划交付及一次 Mac 结果通知为止；不承接其他工作、不创建长期 watcher、schedule、额外子代理，不启动 Mac 实施或 Reviewer 任务。
