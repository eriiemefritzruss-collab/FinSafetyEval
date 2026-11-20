"""A single-host durable state machine, not a distributed lock or merge service.

The caller must authenticate the event author and obtain current GitHub/Git
facts before ingesting. A self-reported role, proof URL, or GitHub account shared
by agents cannot establish human approval. This module checks the configured
actor allowlist and immutable snapshot equality; it never fetches or executes.

Claimed/dispatched jobs are deliberately NOT reclaimed on process restart.
A timeout does not prove that an external agent stopped. The adapter must first
reconcile an uncertain launch/receipt and explicitly resume after it has stopped.
"""
import contextlib
import datetime as dt
import json
from pathlib import Path
import re
import sqlite3


VERSION = 1
BINDINGS = ("requirement_revision", "plan_version", "plan_sha", "plan_blob_oid",
            "head_sha", "base_sha", "policy_source_sha")
PLAN_FIELDS = ("requirement_revision", "plan_version", "plan_sha", "plan_blob_oid", "policy_source_sha")
REVIEW_FIELDS = (*PLAN_FIELDS, "head_sha", "base_sha")
ROLES = {
    "TASK_OPENED": {"User", "Planner", "Coordinator"},
    "PLAN_PROPOSED": {"Planner"},
    "PLAN_APPROVED": {"User"},
    "IMPLEMENTATION_READY": {"Implementer"},
    "IMPLEMENTATION_BLOCKED": {"Implementer"},
    "HEAD_CHANGED": {"Implementer", "Coordinator"},
    "REVIEW_COMPLETED": {"Reviewer"},
    "ENV_RESUMED": {"User", "Coordinator"},
    "TASK_STOPPED": {"User", "Coordinator"},
}
OUTCOMES = {"PASS", "CODE_CHANGES_REQUIRED", "PLAN_REVISION_REQUIRED", "ENV_BLOCKED", "NEEDS_HUMAN"}
GATES = {"Code", "Integration", "Deployment", "Acceptance"}
ACTIVE_JOBS = ("pending", "claimed", "dispatched")
SHA = re.compile(r"[a-f0-9]{40}\Z")
REVISION = re.compile(r"sha256:[a-f0-9]{64}\Z")


class InvalidEvent(ValueError):
    pass


class EventConflict(ValueError):
    pass


class JobConflict(ValueError):
    pass


class Rejected(ValueError):
    """A valid envelope whose transition is no longer applicable."""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InvalidEvent(label + " must be a nonempty string")


def names(value, label, allowed=None):
    if not isinstance(value, list) or not value:
        raise InvalidEvent(label + " must be a nonempty list")
    for item in value:
        text(item, label)
        if allowed is not None and item not in allowed:
            raise InvalidEvent(label + " contains an unsupported name")
    if len(value) != len(set(value)):
        raise InvalidEvent(label + " contains duplicates")


def full_sha(value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise InvalidEvent(label + " must be a complete lowercase Git SHA")


class Controller:
    """SQLite-backed controller. Identity mappings must be explicit.

    ``trusted_actors`` maps exact role names to authenticated identity strings.
    The transport, not a JSON actor declaration, supplies those identities.
    ``ingest`` returns newly queued jobs. Rejected stale transitions are retained
    with accepted=false, return no jobs, and can be inspected with event_result.
    complete() acknowledges dispatch output only; phase changes require events.
    """

    def __init__(self, db_path, *, trusted_actors=None, max_review_rounds=3, max_attempts=3):
        if not isinstance(trusted_actors, dict) or not trusted_actors:
            raise ValueError("An explicit trusted_actors mapping is required")
        self.trusted_actors = {}
        for role, actors in trusted_actors.items():
            if role not in set().union(*ROLES.values()) or not isinstance(actors, (set, list, tuple)) or not actors:
                raise ValueError("trusted_actors must map known roles to nonempty identity lists")
            for actor in actors:
                text(actor, "trusted actor")
            self.trusted_actors[role] = set(actors)
        if type(max_review_rounds) is not int or max_review_rounds < 1 or type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("Positive round and attempt limits are required")
        if str(db_path) == ":memory:":
            raise ValueError("Use a persistent database path")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_review_rounds, self.max_attempts = max_review_rounds, max_attempts
        with self._tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, repository TEXT NOT NULL,
                    state TEXT NOT NULL, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    document TEXT NOT NULL, accepted INTEGER NOT NULL,
                    reason TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    kind TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, worker_id TEXT,
                    external_id TEXT, result TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(status, id);
                CREATE INDEX IF NOT EXISTS jobs_task ON jobs(task_id, status);
            """)

    @contextlib.contextmanager
    def _tx(self):
        db = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _validate(self, event):
        if not isinstance(event, dict) or event.get("schema_version") != VERSION or type(event.get("schema_version")) is not int:
            raise InvalidEvent("schema_version must be 1")
        for field in ("event_id", "task_id", "repository", "kind"):
            text(event.get(field), field)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", event["task_id"]):
            raise InvalidEvent("task_id must be a portable identifier")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", event["repository"]):
            raise InvalidEvent("repository must be owner/name")
        kind = event["kind"]
        if kind not in ROLES:
            raise InvalidEvent("Unknown event kind; merge is not an executable action")
        actor = event.get("actor")
        if not isinstance(actor, dict) or not isinstance(actor.get("role"), str) or actor["role"] not in ROLES[kind]:
            raise InvalidEvent("Actor role is not allowed for this event")
        text(actor.get("id"), "actor.id")
        if actor["id"] not in self.trusted_actors.get(actor["role"], set()):
            raise InvalidEvent("Authenticated actor is not configured for this role")
        proof = event.get("proof")
        if not isinstance(proof, dict):
            raise InvalidEvent("proof must identify the verified transport source")
        text(proof.get("source"), "proof.source")
        if "url" in proof and proof["url"] is not None:
            text(proof["url"], "proof.url")
        binding, payload = event.get("binding"), event.get("payload")
        if not isinstance(binding, dict) or set(binding) != set(BINDINGS):
            raise InvalidEvent("binding must contain exactly " + ", ".join(BINDINGS))
        if not isinstance(payload, dict):
            raise InvalidEvent("payload must be an object")
        revision = binding["requirement_revision"]
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise InvalidEvent("requirement_revision must be sha256:<64 lowercase hex>")
        full_sha(binding["policy_source_sha"], "policy_source_sha")
        for field in ("plan_sha", "plan_blob_oid", "head_sha", "base_sha"):
            if binding[field] is not None:
                full_sha(binding[field], field)
        if binding["plan_version"] is not None and (type(binding["plan_version"]) is not int or binding["plan_version"] < 1):
            raise InvalidEvent("plan_version must be null or a positive integer")
        if kind not in ("TASK_OPENED", "ENV_RESUMED", "TASK_STOPPED"):
            for field in ("plan_version", "plan_sha", "plan_blob_oid"):
                if binding[field] is None:
                    raise InvalidEvent(field + " is required after task opening")
        if kind in ("IMPLEMENTATION_READY", "HEAD_CHANGED", "REVIEW_COMPLETED"):
            for field in ("head_sha", "base_sha"):
                full_sha(binding[field], field)
        if kind == "TASK_OPENED":
            if any(binding[f] is not None for f in ("plan_version", "plan_sha", "plan_blob_oid", "head_sha", "base_sha")):
                raise InvalidEvent("TASK_OPENED must not invent plan/candidate pointers")
            text(payload.get("goal"), "payload.goal")
            if type(payload.get("test_mode")) is not bool:
                raise InvalidEvent("payload.test_mode must be explicit")
            text(payload.get("entry_agent_id"), "payload.entry_agent_id")
            names(payload.get("required_checks"), "required_checks")
            names(payload.get("required_gates"), "required_gates", GATES)
            if "Code" not in payload["required_gates"]:
                raise InvalidEvent("required_gates must include Code")
        if kind == "PLAN_APPROVED" and payload.get("decision") != "approve":
            raise InvalidEvent("PLAN_APPROVED needs an explicit approve decision")
        if kind == "IMPLEMENTATION_BLOCKED":
            if (not isinstance(payload.get("outcome"), str)
                    or payload["outcome"] not in ("PLAN_REVISION_REQUIRED", "ENV_BLOCKED", "NEEDS_HUMAN")):
                raise InvalidEvent("IMPLEMENTATION_BLOCKED needs a plan/environment/human outcome")
            text(payload.get("summary"), "payload.summary")
        if kind == "REVIEW_COMPLETED":
            if not isinstance(payload.get("outcome"), str) or payload["outcome"] not in OUTCOMES:
                raise InvalidEvent("Unsupported review outcome")
            report = payload.get("report_url") or proof.get("url")
            text(report, "Shared review report URL")
            if not report.startswith("https://"):
                raise InvalidEvent("Review report must be shared over HTTPS, not only a local file")
        try:
            encoded(event)
        except (TypeError, ValueError) as exc:
            raise InvalidEvent("Event must contain only finite JSON values") from exc

    def _save(self, db, task):
        db.execute("INSERT INTO tasks VALUES(?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,snapshot=excluded.snapshot,updated_at=excluded.updated_at",
                   (task["task_id"], task["repository"], task["state"], encoded(task), now()))

    def _queue(self, db, task, kind, suffix, extra=None):
        payload = {"task_id": task["task_id"], "repository": task["repository"],
                   "binding": task["binding"], "goal": task["goal"], "test_mode": task["test_mode"],
                   "entry_agent_id": task["entry_agent_id"], "return_agent_id": task["entry_agent_id"],
                   "required_checks": task["required_checks"], "required_gates": task["required_gates"],
                   "review_round": task["review_round"], "state": task["state"], **(extra or {})}
        key = ":".join((task["repository"], task["task_id"], kind, str(suffix)))
        cursor = db.execute("INSERT OR IGNORE INTO jobs(task_id,kind,idempotency_key,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                            (task["task_id"], kind, key, encoded(payload), now(), now()))
        return [self._job(db.execute("SELECT * FROM jobs WHERE id=?", (cursor.lastrowid,)).fetchone())] if cursor.rowcount else []

    def _supersede(self, db, task_id, kinds=None):
        sql = "UPDATE jobs SET status='superseded',updated_at=? WHERE task_id=? AND status IN ('pending','claimed','dispatched')"
        args = [now(), task_id]
        if kinds:
            sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            args.extend(kinds)
        db.execute(sql, args)

    @staticmethod
    def _job(row):
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    @staticmethod
    def _equal(task, binding, fields=PLAN_FIELDS):
        mismatches = [f for f in fields if task["binding"][f] != binding[f]]
        if mismatches:
            raise Rejected("Stale or unapproved snapshot: " + ", ".join(mismatches))

    def ingest(self, event):
        self._validate(event)
        raw, jobs = encoded(event), []
        with self._tx() as db:
            existing = db.execute("SELECT document FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            if existing:
                if existing["document"] != raw:
                    raise EventConflict("event_id reused with different contents")
                return []
            row = db.execute("SELECT snapshot FROM tasks WHERE task_id=?", (event["task_id"],)).fetchone()
            task = json.loads(row["snapshot"]) if row else None
            accepted, reason = True, None
            try:
                task, jobs = self._apply(db, task, event)
            except Rejected as exc:
                accepted, reason = False, str(exc)
            if accepted:
                self._save(db, task)
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)", (event["event_id"], event["task_id"], event["kind"], raw, int(accepted), reason, now()))
        return jobs

    def _apply(self, db, task, event):
        kind, binding, payload = event["kind"], dict(event["binding"]), event["payload"]
        if kind == "TASK_OPENED":
            if task:
                raise Rejected("Task already exists; use a new task identifier")
            task = {"task_id": event["task_id"], "repository": event["repository"], "state": "PLANNING",
                    "goal": payload["goal"], "binding": binding, "test_mode": payload["test_mode"],
                    "entry_agent_id": payload["entry_agent_id"], "required_checks": payload["required_checks"],
                    "required_gates": payload["required_gates"], "approved": False, "review_round": 1,
                    "infra_resumes": 0, "next_owner": "Planner", "last_review": None, "blocked_reason": None}
            return task, self._queue(db, task, "plan", event["event_id"])
        if task is None:
            raise Rejected("Unknown task")
        if task["repository"] != event["repository"]:
            raise Rejected("Repository does not match the task")
        if task["state"] == "STOPPED":
            raise Rejected("Task is stopped")
        if kind == "TASK_STOPPED":
            self._supersede(db, task["task_id"])
            task.update(state="STOPPED", next_owner="User", blocked_reason=payload.get("reason", "User stopped task"))
            return task, self._queue(db, task, "report", event["event_id"], {"reason": task["blocked_reason"]})
        if kind == "PLAN_PROPOSED":
            old = task["binding"]
            if old["plan_version"] is not None and binding["plan_version"] <= old["plan_version"]:
                if all(old[f] == binding[f] for f in PLAN_FIELDS) and task["state"] == "PLAN_REVIEW":
                    return task, []
                raise Rejected("Plan revision must increase; old approval cannot carry forward")
            self._supersede(db, task["task_id"])
            binding.update(head_sha=None, base_sha=None)
            task.update(binding=binding, state="PLAN_REVIEW", approved=False, next_owner="User", last_review=None,
                        review_round=1, infra_resumes=0, plan_pr=payload.get("plan_pr"), blocked_reason=None)
            return task, self._queue(db, task, "report", event["event_id"], {"message": "Plan proposed; explicit user approval required"})
        if kind == "PLAN_APPROVED":
            if task["state"] != "PLAN_REVIEW":
                raise Rejected("No pending plan can be approved")
            self._equal(task, binding, ("requirement_revision", "plan_version", "plan_blob_oid", "policy_source_sha"))
            if binding["plan_sha"] != task["binding"]["plan_sha"] and payload.get("proposed_plan_sha") != task["binding"]["plan_sha"]:
                raise Rejected("Approval must bind the proposed plan or its verified merge commit")
            if not task["test_mode"] and payload.get("plan_merged") is not True:
                raise Rejected("Production task requires the approved plan to be merged and verified by the transport")
            self._supersede(db, task["task_id"])
            binding.update(head_sha=None, base_sha=None)
            task.update(binding=binding, approved=True, state="IMPLEMENTING", next_owner="Implementer", approval_event_id=event["event_id"], blocked_reason=None)
            return task, self._queue(db, task, "implement", "plan-" + binding["plan_sha"])
        if kind == "IMPLEMENTATION_BLOCKED":
            if not task["approved"] or task["state"] != "IMPLEMENTING":
                raise Rejected("Implementation blockers require a valid approval and an implementing task")
            self._equal(task, binding, PLAN_FIELDS)
            self._supersede(db, task["task_id"])
            blocker = {"event_id": event["event_id"], "binding": binding,
                       "outcome": payload["outcome"], "summary": payload["summary"],
                       "evidence": payload.get("evidence")}
            task.update(implementation_blocker=blocker, blocked_reason=payload["summary"])
            if payload["outcome"] == "PLAN_REVISION_REQUIRED":
                task.update(approved=False, state="PLAN_REVISION_REQUIRED", next_owner="Planner")
                jobs = self._queue(db, task, "plan", "implementation-blocker-" + event["event_id"], {"blocker": blocker})
            elif payload["outcome"] == "ENV_BLOCKED":
                task.update(state="ENV_BLOCKED", next_owner="Environment owner", resume_kind="implement",
                            resume_state="IMPLEMENTING", resume_owner="Implementer", execution_uncertain=False)
                jobs = []
            else:
                task.update(state="NEEDS_HUMAN", next_owner="User")
                jobs = []
            return task, jobs + self._queue(db, task, "report", event["event_id"],
                                           {"blocker": blocker, "reason": payload["summary"]})
        if kind in ("IMPLEMENTATION_READY", "HEAD_CHANGED"):
            if not task["approved"]:
                raise Rejected("An explicit valid plan approval is required before implementation/review")
            self._equal(task, binding)
            if task["state"] in ("ENV_BLOCKED", "NEEDS_HUMAN", "PLAN_REVISION_REQUIRED"):
                raise Rejected("Resolve the blocked decision before candidate dispatch")
            if all(task["binding"][f] == binding[f] for f in REVIEW_FIELDS) and task["state"] in ("REVIEWING", "READY_FOR_HUMAN_MERGE"):
                return task, []
            self._supersede(db, task["task_id"])
            task.update(binding=binding, state="REVIEWING", next_owner="Reviewer", last_review=None, blocked_reason=None)
            suffix = ":".join((binding["plan_sha"], binding["head_sha"], binding["base_sha"], str(task["review_round"])))
            return task, self._queue(db, task, "review", suffix, {"implementation_pr": payload.get("implementation_pr")})
        if kind == "REVIEW_COMPLETED":
            if not task["approved"] or task["state"] != "REVIEWING":
                raise Rejected("Task is not awaiting an independent review")
            self._equal(task, binding, REVIEW_FIELDS)
            outcome = payload["outcome"]
            if outcome == "PASS":
                self._validate_pass(task, payload)
            self._supersede(db, task["task_id"], ("review", "report"))
            task["last_review"] = {"event_id": event["event_id"], "binding": binding, "outcome": outcome,
                                   "report_url": payload.get("report_url") or event["proof"].get("url")}
            task["blocked_reason"] = payload.get("summary")
            if outcome == "PASS":
                task.update(state="READY_FOR_HUMAN_MERGE", next_owner="User", blocked_reason=None)
                return task, self._queue(db, task, "report", event["event_id"], {"review": task["last_review"], "merge_authorized": False})
            if outcome == "CODE_CHANGES_REQUIRED":
                if task["review_round"] >= self.max_review_rounds:
                    task.update(state="NEEDS_HUMAN", next_owner="User", blocked_reason="Review round limit reached")
                    return task, self._queue(db, task, "report", event["event_id"], {"reason": task["blocked_reason"]})
                task.update(state="IMPLEMENTING", next_owner="Implementer", review_round=task["review_round"] + 1)
                jobs = self._queue(db, task, "implement", "repair-" + event["event_id"], {"review": task["last_review"], "findings": payload.get("findings", [])})
            elif outcome == "PLAN_REVISION_REQUIRED":
                task.update(state="PLAN_REVISION_REQUIRED", approved=False, next_owner="Planner")
                jobs = self._queue(db, task, "plan", "revise-" + event["event_id"], {"review": task["last_review"]})
            elif outcome == "ENV_BLOCKED":
                task.update(state="ENV_BLOCKED", next_owner="Environment owner", resume_kind="review", resume_state="REVIEWING", resume_owner="Reviewer", execution_uncertain=False)
                jobs = []
            else:
                task.update(state="NEEDS_HUMAN", next_owner="User")
                jobs = []
            return task, jobs + self._queue(db, task, "report", event["event_id"], {"review": task["last_review"]})
        if kind == "ENV_RESUMED":
            self._equal(task, binding, REVIEW_FIELDS)
            if task["state"] != "ENV_BLOCKED":
                raise Rejected("Task is not environment-blocked")
            if task.get("execution_uncertain") and payload.get("external_execution_stopped") is not True:
                raise Rejected("Reconcile the old external execution before retrying")
            if task["infra_resumes"] >= self.max_attempts:
                task.update(state="NEEDS_HUMAN", next_owner="User", blocked_reason="Infrastructure retry limit reached")
                return task, self._queue(db, task, "report", event["event_id"], {"reason": task["blocked_reason"]})
            self._supersede(db, task["task_id"])
            resume_kind = task.get("resume_kind", "review")
            task.update(state=task.get("resume_state", "REVIEWING" if resume_kind == "review" else "IMPLEMENTING"), next_owner=task.get("resume_owner", "Reviewer" if resume_kind == "review" else "Implementer"), infra_resumes=task["infra_resumes"] + 1, execution_uncertain=False, blocked_reason=None)
            return task, self._queue(db, task, resume_kind, "resume-" + event["event_id"])
        raise Rejected("Unsupported transition")

    @staticmethod
    def _validate_pass(task, payload):
        gates, checks = payload.get("gates"), payload.get("required_checks")
        if not isinstance(gates, dict) or set(gates) != GATES:
            raise Rejected("PASS needs all four gate records")
        for name, gate in gates.items():
            if not isinstance(gate, dict) or gate.get("status") not in ("PASS", "N/A") or not isinstance(gate.get("reason"), str) or not gate["reason"].strip():
                raise Rejected("PASS cannot contain missing, failed or blocked gate evidence")
            if name in task["required_gates"] and gate["status"] != "PASS":
                raise Rejected("Required gates cannot be waived")
        if payload.get("blocking_findings") != []:
            raise Rejected("PASS requires an explicit empty blocking_findings list")
        if not isinstance(checks, list):
            raise Rejected("PASS requires frozen CI check records")
        seen = {}
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("name"), str) or check["name"] in seen:
                raise Rejected("CI check records must have unique names")
            if check.get("status") != "success" or check.get("head_sha") != task["binding"]["head_sha"]:
                raise Rejected("CI must succeed for the reviewed candidate")
            seen[check["name"]] = check
        if not set(task["required_checks"]).issubset(seen):
            raise Rejected("PASS omitted a required CI check")

    def event_result(self, event_id):
        with self._tx() as db:
            row = db.execute("SELECT event_id,task_id,kind,accepted,reason,created_at FROM events WHERE event_id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def claim(self, worker_id, *, kinds=None):
        text(worker_id, "worker_id")
        if kinds is not None and (not isinstance(kinds, (list, tuple, set)) or not kinds or set(kinds) - {"plan", "implement", "review", "report"}):
            raise ValueError("Unsupported job kinds")
        with self._tx() as db:
            query = "SELECT j.* FROM jobs j WHERE j.status='pending' AND NOT EXISTS (SELECT 1 FROM jobs active WHERE active.task_id=j.task_id AND active.status IN ('claimed','dispatched'))"
            args = []
            if kinds:
                query += " AND j.kind IN (" + ",".join("?" for _ in kinds) + ")"
                args.extend(kinds)
            row = db.execute(query + " ORDER BY j.id LIMIT 1", args).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='claimed',worker_id=?,attempts=attempts+1,updated_at=? WHERE id=?", (worker_id, now(), row["id"]))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def _owned(self, db, job_id, worker_id):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["worker_id"] != worker_id:
            raise JobConflict("Job does not belong to this worker")
        return row

    def mark_dispatched(self, job_id, worker_id, external_id):
        text(external_id, "external_id")
        with self._tx() as db:
            row = self._owned(db, job_id, worker_id)
            if row["status"] == "dispatched" and row["external_id"] == external_id:
                return self._job(row)
            if row["status"] != "claimed" or row["external_id"] is not None:
                raise JobConflict("Job is not awaiting a launch receipt")
            db.execute("UPDATE jobs SET status='dispatched',external_id=?,updated_at=? WHERE id=?", (external_id, now(), job_id))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def complete(self, job_id, worker_id, result):
        raw = encoded(result)
        with self._tx() as db:
            row = self._owned(db, job_id, worker_id)
            if row["status"] == "completed":
                if row["result"] != raw:
                    raise JobConflict("Completion receipt differs from the saved result")
                return self._job(row)
            if row["status"] not in ("claimed", "dispatched", "superseded"):
                raise JobConflict("Job is not completable")
            status = "superseded" if row["status"] == "superseded" else "completed"
            db.execute("UPDATE jobs SET status=?,result=?,updated_at=? WHERE id=?", (status, raw, now(), job_id))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def fail(self, job_id, worker_id, error, *, retryable=False):
        text(error, "error")
        if type(retryable) is not bool:
            raise ValueError("retryable must be a boolean")
        with self._tx() as db:
            row = self._owned(db, job_id, worker_id)
            if row["status"] not in ("claimed", "dispatched"):
                raise JobConflict("Job is not running")
            safe_retry = retryable and row["external_id"] is None and row["attempts"] < self.max_attempts
            status = "pending" if safe_retry else "uncertain" if row["external_id"] else "failed"
            db.execute("UPDATE jobs SET status=?,error=?,worker_id=?,updated_at=? WHERE id=?", (status, error, None if safe_retry else worker_id, now(), job_id))
            if not safe_retry:
                task_row = db.execute("SELECT snapshot FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
                task = json.loads(task_row["snapshot"])
                prior_state, prior_owner = task["state"], task["next_owner"]
                task.update(state="ENV_BLOCKED", next_owner="Environment owner", blocked_reason=error, resume_kind=row["kind"], resume_state=prior_state, resume_owner=prior_owner, execution_uncertain=bool(row["external_id"]))
                self._save(db, task)
                if row["kind"] != "report":
                    self._queue(db, task, "report", "job-error-" + str(job_id), {"reason": error, "execution_uncertain": bool(row["external_id"])})
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def state(self, task_id):
        with self._tx() as db:
            row = db.execute("SELECT snapshot FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return json.loads(row["snapshot"]) if row else None

    def export_snapshot(self):
        with self._tx() as db:
            return {"schema_version": VERSION, "generated_at": now(), "scope": "single-host SQLite controller; no automatic merge",
                    "tasks": [json.loads(r["snapshot"]) for r in db.execute("SELECT snapshot FROM tasks ORDER BY task_id")],
                    "jobs": [self._job(r) for r in db.execute("SELECT * FROM jobs ORDER BY id")],
                    "events": [dict(r) for r in db.execute("SELECT event_id,task_id,kind,accepted,reason,created_at FROM events ORDER BY rowid")]}
