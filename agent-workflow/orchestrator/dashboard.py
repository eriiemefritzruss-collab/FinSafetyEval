"""Render durable controller facts; optionally serve one HTML on localhost only."""
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading

TEMPLATE = Path(__file__).with_name("dashboard.template.html")
PLAN_KEYS = ("requirement_revision", "plan_version", "plan_sha", "plan_blob_oid", "policy_source_sha")
VERSION_KEYS = (*PLAN_KEYS, "head_sha", "base_sha")
ACTIVE = {"pending", "claimed", "dispatched"}


def _list(value):
    return value if isinstance(value, list) else []


def _obj(value):
    return value if isinstance(value, dict) else {}


def _event(envelope):
    return _obj(envelope).get("event", {}) if isinstance(_obj(envelope).get("event"), dict) else {}


def _same(left, right, keys=VERSION_KEYS):
    left, right = _obj(left), _obj(right)
    return all(key in left and key in right and left[key] == right[key] for key in keys)


def _issue_url(repository, number):
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return None
    if type(number) is not int or number < 1:
        return None
    return "https://github.com/" + repository + "/issues/" + str(number)


def _job_status(job):
    labels = {"pending": "待派发", "claimed": "已领取，启动待确认", "dispatched": "已派发，等待结果",
              "completed": "执行回执已保存", "uncertain": "执行结果不确定", "failed": "执行失败",
              "superseded": "旧版本已失效"}
    return labels.get(_obj(job).get("status"), "未开始")


def _latest_job(jobs, kind, binding, keys=PLAN_KEYS):
    matches = [j for j in jobs if j.get("kind") == kind and _same(_obj(j.get("payload")).get("binding"), binding, keys)]
    return max(matches, key=lambda j: j.get("id", 0), default=None)


def _delivery(task, metadata):
    task_id, binding = task["task_id"], task["binding"]
    entry = task.get("entry_agent_id") or metadata.get("entry_agent_id")
    notifications = []
    for envelope in _list(metadata.get("notifications")):
        event = _event(envelope)
        payload = _obj(event.get("payload"))
        if (event.get("task_id") == task_id and payload.get("entry_agent_id") == entry
                and _same(event.get("binding"), binding)
                and ("state" not in payload or payload["state"] == task.get("state"))):
            notifications.append(envelope)
    notice = notifications[-1] if notifications else None
    ack = None
    if notice:
        notification_id = _event(notice).get("event_id")
        for envelope in _list(metadata.get("acks")):
            event = _event(envelope)
            payload = _obj(event.get("payload"))
            increasing = (type(envelope.get("comment_id")) is not int or type(notice.get("comment_id")) is not int
                          or envelope["comment_id"] > notice["comment_id"])
            if (event.get("task_id") == task_id and payload.get("notification_event_id") == notification_id
                    and payload.get("entry_agent_id") == entry and _same(event.get("binding"), binding) and increasing):
                ack = envelope
    return notice, ack


def _view(snapshot, metadata):
    snapshot, metadata = _obj(snapshot), _obj(metadata)
    jobs, events = _list(snapshot.get("jobs")), _list(snapshot.get("events"))
    repo = metadata.get("repository", "")
    task_views, deliveries = [], {}
    runtime_jobs = _obj(metadata.get("runtime_jobs"))
    for raw in _list(snapshot.get("tasks")):
        task = _obj(raw)
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not isinstance(task.get("binding"), dict):
            continue
        binding = task["binding"]
        related_jobs = [j for j in jobs if j.get("task_id") == task_id]
        related_events = [e for e in events if e.get("task_id") == task_id and e.get("accepted") in (True, 1)]
        issue = _obj(metadata.get("task_issues")).get(task_id)
        url = _issue_url(task.get("repository") or repo, issue)
        implement = _latest_job(related_jobs, "implement", binding)
        review_job = _latest_job(related_jobs, "review", binding, VERSION_KEYS)
        review = _obj(task.get("last_review"))
        current_review = review if _same(review.get("binding"), binding) else {}
        notice, ack = _delivery(task, metadata)
        deliveries[task_id] = {"notification_event_id": _event(notice).get("event_id"), "ack_event_id": _event(ack).get("event_id"),
                               "notification_url": _obj(notice).get("url"), "ack_url": _obj(ack).get("url"), "entry_read_by_human": "not established"}
        plan_exists = binding.get("plan_sha") is not None and binding.get("plan_blob_oid") is not None
        candidate_exists = bool(binding.get("head_sha"))
        impl_info = _obj(runtime_jobs.get(str(_obj(implement).get("id")), runtime_jobs.get(_obj(implement).get("id"))))
        reviewer_info = _obj(runtime_jobs.get(str(_obj(review_job).get("id")), runtime_jobs.get(_obj(review_job).get("id"))))
        def stage(key, title, host, status, summary, detail, source, next_action, evidence_url=None):
            return dict(id=key, title=title, host=host, status=status, summary=summary, detail=detail,
                        source=source, nextAction=next_action, url=evidence_url, versions=source_versions)
        source_versions = "\n".join(f"{key}: {binding.get(key) if binding.get(key) is not None else '未产生'}" for key in VERSION_KEYS)
        review_status = (current_review.get("outcome") or "报告缺失：需重新核对") if task.get("state") == "READY_FOR_HUMAN_MERGE" else current_review.get("outcome") or _job_status(review_job)
        stages = [
            stage("entry", "Windows 固定入口", "Windows · Planner 对话", "需求事件已登记", task.get("goal", ""), "当前任务来自持久事件记录；入口对话ID：" + str(task.get("entry_agent_id") or metadata.get("entry_agent_id") or "未记录"), "TASK_OPENED / controller SQLite", "在同一入口提出或补充本任务需求", url),
            stage("plan", "GitHub 计划", "Windows → GitHub", "计划已收录" if plan_exists else "等待计划", "计划 v" + str(binding.get("plan_version")) if plan_exists else "尚无冻结计划", source_versions, "PLAN_PROPOSED / shared task Issue", "冻结需求、计划SHA与blob，等待用户批准", url),
            stage("approval", "用户明确批准", "用户 → 受信批准事件", "批准已记录" if task.get("approved") else "待用户批准", "当前计划准入有效" if task.get("approved") else "未获本版本有效批准", "test_mode=" + str(bool(task.get("test_mode"))) + "；批准事件=" + str(task.get("approval_event_id") or "无") + "。隔离试验授权不替代其他业务任务的批准。", "PLAN_APPROVED / 当前版本绑定", "仅在批准和版本核对有效时实施", url),
            stage("implementation", "Mac 实施", "Mac · 独立 worktree", "候选已提交" if candidate_exists else _job_status(implement), "head=" + str(binding.get("head_sha")) if candidate_exists else "尚无已接纳候选提交", "worktree=" + str(impl_info.get("worktree") or "未记录") + "；agent=" + str(impl_info.get("agent_id") or _obj(implement).get("external_id") or "未启动") + "；job=" + str(_obj(implement).get("status") or "无"), "IMPLEMENTATION_READY / 派发账本", "按已批准计划交付实现与可复跑证据", url),
            stage("review", "独立 Review", "Mac · 新会话 / 固定 SHA", review_status, "当前绑定报告：" + str(current_review.get("outcome") or "尚无"), "reviewer agent=" + str(reviewer_info.get("agent_id") or _obj(review_job).get("external_id") or "未启动") + "；旧head的报告不能沿用。", "REVIEW_COMPLETED / 当前head-base-policy", "复跑必要验收，失败按原因返工", current_review.get("report_url") or url),
            stage("notification", "GitHub 结果通知", "Mac controller → GitHub", "通知已回写" if notice else "等待回写通知", "notification=" + str(_event(notice).get("event_id") or "无"), "只采用当前任务、入口、版本和阶段相匹配的共享通知。", "GitHub notification envelope", "Windows notifier 读取共享结果", _obj(notice).get("url") or url),
            stage("ack", "回到原对话 ACK", "Windows notifier → 固定对话", "回送 ACK 已确认" if ack else "未确认 ACK", "ack=" + str(_event(ack).get("event_id") or "无"), "ACK必须关联当前notification_event_id、task_id、entry_agent_id与版本，且评论顺序有效。ACK表示回送确认，不表示用户已阅读。", "GitHub ACK envelope / notifier receipt", "无匹配ACK时核对notifier与本机Paseo回执", _obj(ack).get("url") or url),
            stage("merge", "用户最终合并", "用户", "待用户确认", "没有自动 merge", "READY_FOR_HUMAN_MERGE只表示候选达到已记录条件；原对话送达与最终用户决定分别留痕。", "人工决策边界", "查看最新版本、CI和报告后由用户决定", url),
        ]
        task_views.append(dict(id=task_id, title=task.get("goal") or task_id, state=task.get("state", "未知"), nextOwner=task.get("next_owner", "待核对"),
                               summary=("隔离试验任务 · " if task.get("test_mode") else "业务任务 · ") + str(task.get("blocked_reason") or "状态来自已接纳事件和持久派发账本"),
                               head=binding.get("head_sha"), prUrl=url, stages=stages, test_mode=task.get("test_mode") is True))
    task_views.sort(key=lambda t: (not t["test_mode"], t["id"]))
    event_views = [dict(task=e.get("task_id"), at=e.get("created_at"), kind=e.get("kind"), title=str(e.get("kind")) + (" · 已接纳" if e.get("accepted") in (True, 1) else " · 已拒绝"),
                        detail=e.get("reason") or "已通过核心身份白名单与阶段版本核对；完整外部身份/事实由transport验证。",
                        url=_issue_url(repo, _obj(metadata.get("task_issues")).get(e.get("task_id")))) for e in events]
    job_views = []
    for job in jobs:
        info = _obj(runtime_jobs.get(str(job.get("id")), runtime_jobs.get(job.get("id"))))
        job_views.append(dict(id=job.get("id"), task=job.get("task_id"), kind=job.get("kind"), status=job.get("status"), label=_job_status(job),
                              worker=job.get("worker_id"), agent=info.get("agent_id") or job.get("external_id"), attempts=job.get("attempts"),
                              binding=_obj(job.get("payload")).get("binding"), error=job.get("error"), worktree=info.get("worktree")))
    runtime_rows = [dict(label="共享仓库", value=repo or "未配置"), dict(label="固定入口", value=metadata.get("entry_agent_id") or "未配置"),
                    dict(label="controller PID（进程自报）", value=metadata.get("controller_pid") or "未记录"),
                    dict(label="最后一次轮询", value=metadata.get("last_poll") or "未记录"),
                    dict(label="轮询错误", value=metadata.get("error") or "当前快照未记录错误")]
    return dict(asOf=snapshot.get("generated_at") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), mode="持久事件快照 · 无自动批准或合并",
                conclusion="一个 Windows 对话作为入口；GitHub 保存交接；Mac 实施与独立 Review；通知及原对话 ACK 分别核对。",
                tasks=task_views, events=event_views, jobs=job_views, runtimeRows=runtime_rows, deliveries=deliveries, checks=_list(metadata.get("checks")),
                connections=[dict(from_="Windows Planner", to="GitHub 任务事件", status="以计划事件为准", detail="入口直接提交计划；不用将daemon配对状态冒充任务完成。", evidence="TASK_OPENED / PLAN_PROPOSED"),
                             dict(from_="GitHub", to="Mac 唯一controller", status="轮询有错误" if metadata.get("error") else "轮询状态见时间", detail="Mac领取实施/审核jobs，SQLite记录claim与外部回执。", evidence=str(metadata.get("last_poll") or "尚无轮询时间")),
                             dict(from_="GitHub 结果通知", to="Windows 原对话", status="ACK独立核验", detail="通知回写成功、notifier回送成功、用户已读是三个不同事实。", evidence="notification / ENTRY_ACK")],
                findings=[dict(severity="保留范围", title="T-001 仍保留待审", detail="业务计划PR #2没有因本次协作试验获得实施或合并授权。", nextAction="只有用户明确批准该业务计划后才能启动相应实施。", source="用户范围指令")],
                capabilities=[dict(title="固定入口", status="任务ID绑定", detail="新需求继续在Windows入口提出；每项任务保留独立版本。"),
                              dict(title="后台独立执行", status="派发记录可见", detail="Implementer和Reviewer使用各自会话及worktree，不能互相代签。"),
                              dict(title="原对话回传", status="需匹配ACK", detail="READY_FOR_HUMAN_MERGE不会自动点亮ACK阶段。")],
                roadmap=[], sources=[dict(label="共享仓库", url="https://github.com/"+repo)] if repo else [])


def write_dashboard(output_path, snapshot, metadata):
    """Atomically write a self-contained file. Never performs network requests."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    packet = {"snapshot": snapshot, "metadata": metadata, "view": _view(snapshot, metadata)}
    for edge in packet["view"]["connections"]:
        edge["from"] = edge.pop("from_")
    raw = json.dumps(packet, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__CHAIN_DATA__") != 1:
        raise ValueError("Expected exactly one embedded data marker")
    page = template.replace("__CHAIN_DATA__", raw)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".dashboard-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(page)
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
    return path


def _served_page(page, paused):
    refresh = "" if paused else '<meta http-equiv="refresh" content="10">'
    page = page.replace("</head>", refresh + "</head>", 1)
    control = ('<strong>自动刷新已暂停</strong> · <a href="/">恢复每10秒刷新</a>' if paused else
               '<strong>每10秒刷新此状态快照</strong> · <a href="/?paused=1">暂停自动刷新</a>')
    return re.sub(r"<!-- refresh-control:start -->.*?<!-- refresh-control:end -->",
                  "<!-- refresh-control:start -->" + control + "<!-- refresh-control:end -->", page, count=1, flags=re.S)


def serve_dashboard(html_path, port=0):
    """Return (server, daemon thread); serve only this HTML at 127.0.0.1."""
    path = Path(html_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/?paused=1"):
                self.send_error(404)
                return
            try:
                page = _served_page(path.read_text(encoding="utf-8"), self.path == "/?paused=1").encode("utf-8")
            except OSError:
                self.send_error(503, "Dashboard snapshot is not available")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="finsafety-local-dashboard", daemon=True)
    thread.start()
    return server, thread
