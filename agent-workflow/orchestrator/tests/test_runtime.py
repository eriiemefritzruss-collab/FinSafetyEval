"""Runtime boundaries: real Git objects with mock authenticated services."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock


MODULE = Path(__file__).resolve().parents[1] / "runtime.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("finsafety_runtime_under_test", MODULE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class Service:
    def __init__(self):
        self.events = []
        self.reports = []
        self.pr = {}
        self.checks = []
        self.on_post = None
        self.fail_after_publish = None

    def request(self, method, path="", body=None):
        if method == "GET" and path.startswith("issues?"):
            return [{"number": 5, "body": runtime.TASK_MARKER}]
        if path.startswith("pulls"):
            return [copy.deepcopy(self.pr)] if method == "GET" else copy.deepcopy(self.pr)
        raise AssertionError((method, path))

    def list_events(self, issue):
        return copy.deepcopy(self.events)

    def get_pr(self, number):
        return copy.deepcopy(self.pr)

    def list_checks(self, head):
        return copy.deepcopy(self.checks)

    def post_report(self, issue, markdown, report_id):
        self.reports.append((issue, markdown, report_id))
        return {"url": "https://github.com/owner/repo/issues/5#issuecomment-report", "comment_id": 100}

    def post_event(self, issue, event):
        if self.on_post:
            self.on_post(event)
        prior = next((x for x in self.events if x["event"]["event_id"] == event["event_id"]), None)
        if prior:
            return {**copy.deepcopy(prior), "created": False}
        envelope = {"event": copy.deepcopy(event), "author": "trusted-login", "comment_id": len(self.events)+1,
                    "url": "https://github.com/owner/repo/issues/5#issuecomment-event"}
        self.events.append(envelope)
        if self.fail_after_publish == event["kind"]:
            self.fail_after_publish = None
            raise runtime.AdapterError("GITHUB_NETWORK", "Uncertain remote receipt", uncertain=True)
        return {**copy.deepcopy(envelope), "created": True}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.owned = tempfile.TemporaryDirectory(prefix="finsafety-runtime-")
        self.addCleanup(self.owned.cleanup)
        self.root = Path(self.owned.name)
        self.repo = self.root / "repo space 中文"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.repo/".gitignore").write_text("agent-workflow/local/\n", encoding="utf-8")
        (self.repo/"policy.txt").write_text("policy\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "trusted policy")
        self.policy = self.git("rev-parse", "HEAD")
        self.task = "T-RUNTIME"
        self.plan_path = f"plans/{self.task}/plan-v1.md"
        (self.repo/self.plan_path).parent.mkdir(parents=True)
        (self.repo/self.plan_path).write_text("plan\n", encoding="utf-8")
        self.fixture_path = f"agent-workflow/fixtures/e2e/{self.task}.json"
        self.contract_path = f"plans/{self.task}/handoff-v1.json"
        self.contract = {"allowed_paths": [self.fixture_path], "validation_commands": [[sys.executable, "-c", "print('verified')"]]}
        (self.repo/self.contract_path).write_text(json.dumps(self.contract), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "frozen plan")
        self.plan = self.git("rev-parse", "HEAD")
        self.blob = self.git("rev-parse", "HEAD:"+self.plan_path)
        self.git("update-ref", "refs/remotes/origin/main", self.plan)
        self.rev = "sha256:" + hashlib.sha256(b"goal").hexdigest()
        self.binding = {"requirement_revision": self.rev, "plan_version": 1, "plan_sha": self.plan,
                        "plan_blob_oid": self.blob, "head_sha": None, "base_sha": None, "policy_source_sha": self.policy}
        self.config = {"repository": "owner/repo", "repo_path": str(self.repo), "state_dir": str(self.root/"state"),
                       "controller_login": "trusted-login", "entry_agent_id": "entry-agent-1234", "paseo_binary": "paseo",
                       "provider": "codex", "mode": "full-access", "worktree_root": str(self.root/"worktrees"),
                       "allowed_test_tasks": [self.task], "task_issues": [5], "discover_tasks": False,
                       "implementation_base_branch": "main", "windows_entry_script": "D:\\project\\entry.py",
                       "windows_config_path": "D:\\project\\entry.json", "job_timeout_seconds": 60}
        self.service = Service()
        self.paseo = Mock()
        self.paseo.inspect_agent.return_value = {"Status": "idle"}
        self.rt = runtime.Runtime(self.config, github=self.service, paseo=self.paseo)
        real_git = self.rt.git
        def local_git(*args, network=False, cwd=None):
            if args[0] in ("fetch", "push"):
                return ""
            return real_git(*args, cwd=cwd)
        self.rt.git = local_git
        self.counter = 0

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def event(self, kind, bind=None, payload=None, role=None):
        self.counter += 1
        roles = {"TASK_OPENED": "Planner", "PLAN_PROPOSED": "Planner", "PLAN_APPROVED": "User", "IMPLEMENTATION_READY": "Implementer"}
        return {"schema_version": 1, "event_id": kind+":"+str(self.counter), "task_id": self.task,
                "repository": "owner/repo", "kind": kind, "actor": {"role": role or roles.get(kind, "Coordinator"), "id": "trusted-login"},
                "binding": copy.deepcopy(bind or self.binding), "payload": payload or {}, "proof": {"source": "mock-authenticated-github", "url": "https://github.com/owner/repo/issues/5"}}

    def opened(self, test_mode=True):
        bind = dict(self.binding, plan_version=None, plan_sha=None, plan_blob_oid=None)
        return self.event("TASK_OPENED", bind, {"goal": "goal", "test_mode": test_mode, "entry_agent_id": self.config["entry_agent_id"],
                                               "required_checks": ["contract-ci"], "required_gates": ["Code", "Integration", "Acceptance"]})

    def setup_approved(self, test_mode=True):
        self.rt.core.ingest(self.opened(test_mode))
        proposal = self.event("PLAN_PROPOSED", payload={"plan_pr": 4, "handoff_path": self.contract_path})
        self.rt.core.ingest(proposal)
        self.rt.meta["proposals"][self.task] = proposal
        self.rt.meta["task_issues"][self.task] = 5
        self.rt.core.ingest(self.event("PLAN_APPROVED", payload={"decision": "approve", "plan_merged": not test_mode}))
        job = self.rt.core.claim(self.rt.worker_id, kinds=["implement"])
        self.rt.core.mark_dispatched(job["id"], self.rt.worker_id, "implementation-agent")
        return next(x for x in self.rt.core.export_snapshot()["jobs"] if x["id"] == job["id"])

    def candidate(self):
        path = self.repo/self.fixture_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task_id": self.task, "requirement_revision": self.rev, "marker": "FSA_CROSS_DEVICE_OK"}), encoding="utf-8")
        self.git("add", self.fixture_path)
        self.git("commit", "-m", "candidate fixture")
        head = self.git("rev-parse", "HEAD")
        self.service.pr = {"number": 9, "head": {"sha": head}, "base": {"sha": self.plan}}
        self.service.checks = [{"name": "contract-ci", "head_sha": head, "conclusion": "success", "status": "completed"}]
        return head

    def info(self, job):
        result_path = self.repo/"agent-workflow/local/worker-result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        info = {"issue": 5, "worktree": str(self.repo), "branch": "fixture-branch", "result_path": str(result_path),
                "contract": copy.deepcopy(self.contract), "phase": "waiting_agent", "started_at": runtime.now(), "job_id": job["id"], "agent_id": "worker-agent"}
        self.rt.meta["runtime_jobs"][str(job["id"])] = info
        self.rt.save()
        return info

    def prepare_review(self):
        self.setup_approved()
        head = self.candidate()
        bind = dict(self.binding, head_sha=head, base_sha=self.plan)
        self.rt.core.ingest(self.event("IMPLEMENTATION_READY", bind, {"implementation_pr": 9}))
        job = self.rt.core.claim(self.rt.worker_id, kinds=["review"])
        self.rt.core.mark_dispatched(job["id"], self.rt.worker_id, "review-agent")
        job = next(x for x in self.rt.core.export_snapshot()["jobs"] if x["id"] == job["id"])
        return job, self.info(job)

    def passed(self):
        return {"outcome": "PASS", "summary": "independent fixture validation", "tests": [{"command": "python local check", "exit_code": 0}],
                "findings": [], "blocking_findings": [], "gates": {name: {"status": "N/A" if name == "Deployment" else "PASS", "reason": "actual fixture evidence"} for name in ("Code", "Integration", "Deployment", "Acceptance")}}

    def test_unapproved_proposal_cannot_queue_implementation_and_forged_author_is_ignored(self):
        opened = self.opened()
        self.service.events = [{"event": opened, "author": "attacker", "comment_id": 1}]
        self.rt.sync()
        self.assertIsNone(self.rt.core.state(self.task))
        self.service.events[0]["author"] = "trusted-login"
        proposal = self.event("PLAN_PROPOSED", payload={"plan_pr": 4})
        self.service.events.append({"event": proposal, "author": "trusted-login", "comment_id": 2})
        self.rt.sync()
        self.assertEqual(self.rt.core.state(self.task)["state"], "PLAN_REVIEW")
        self.assertFalse(any(j["kind"] == "implement" for j in self.rt.core.export_snapshot()["jobs"]))

    def test_production_approval_uses_actual_pr_merge_not_body_claim(self):
        self.rt.core.ingest(self.opened(False))
        proposal = self.event("PLAN_PROPOSED", payload={"plan_pr": 4})
        self.rt.core.ingest(proposal)
        self.rt.meta["proposals"][self.task] = proposal
        self.service.pr = {"merged": False, "merge_commit_sha": self.plan}
        approval = self.event("PLAN_APPROVED", payload={"decision": "approve", "plan_merged": True})
        with self.assertRaises(runtime.RuntimeErrorSafe):
            self.rt._verify_approval(approval, self.rt.core.state(self.task))

    def test_implementation_event_is_published_before_job_is_completed(self):
        job = self.setup_approved()
        self.candidate()
        info = self.info(job)
        observed = []
        def observe(event):
            if event["kind"] == "IMPLEMENTATION_READY":
                status = next(j for j in self.rt.core.export_snapshot()["jobs"] if j["id"] == job["id"])["status"]
                observed.append(status)
        self.service.on_post = observe
        self.rt._finish_implementation(job, info, {"outcome": "IMPLEMENTATION_READY", "summary": "fixture"})
        self.assertTrue(observed)
        self.assertNotIn("completed", observed, "Local completion must not precede the remote stage receipt")

    def test_uncertain_stage_write_can_reconcile_after_restart_without_relaunch(self):
        job = self.setup_approved()
        self.candidate()
        info = self.info(job)
        self.service.fail_after_publish = "IMPLEMENTATION_READY"
        try:
            self.rt._finish_implementation(job, info, {"outcome": "IMPLEMENTATION_READY", "summary": "fixture"})
        except runtime.AdapterError:
            pass
        restarted = runtime.Runtime(self.config, github=self.service, paseo=self.paseo)
        restarted.collect()
        current = next(j for j in restarted.core.export_snapshot()["jobs"] if j["id"] == job["id"])
        self.assertEqual(current["status"], "completed")
        self.assertEqual(len([e for e in self.service.events if e["event"]["kind"] == "IMPLEMENTATION_READY"]), 1)
        self.assertNotEqual(restarted.meta["runtime_jobs"][str(job["id"])]["phase"], "waiting_agent")
        self.paseo.run_agent.assert_not_called()

    def test_review_event_is_published_before_local_completion(self):
        job, info = self.prepare_review()
        statuses = []
        self.service.on_post = lambda event: statuses.append(next(j for j in self.rt.core.export_snapshot()["jobs"] if j["id"] == job["id"])["status"]) if event["kind"] == "REVIEW_COMPLETED" else None
        self.rt._finish_review(job, info, self.passed())
        self.assertTrue(statuses)
        self.assertNotIn("completed", statuses)

    def test_changed_head_never_publishes_old_pass(self):
        job, info = self.prepare_review()
        self.service.pr["head"]["sha"] = self.policy
        before = len(self.service.events)
        self.rt._finish_review(job, info, self.passed())
        published = [x["event"] for x in self.service.events[before:]]
        self.assertEqual([x["kind"] for x in published], ["HEAD_CHANGED"])
        self.assertNotEqual(published[0]["binding"], job["payload"]["binding"])

    def test_changed_base_never_publishes_old_pass(self):
        job, info = self.prepare_review()
        self.service.pr["base"]["sha"] = self.policy
        self.rt._finish_review(job, info, self.passed())
        self.assertEqual(self.service.events[-1]["event"]["kind"], "HEAD_CHANGED")

    def test_pass_cannot_erase_reviewer_findings_or_failed_gate(self):
        job, info = self.prepare_review()
        result = self.passed()
        result["findings"] = [{"id": "F-block", "severity": "high", "summary": "real blocker"}]
        result["blocking_findings"] = ["F-block"]
        result["gates"]["Code"] = {"status": "FAIL", "reason": "real blocker"}
        try:
            self.rt._finish_review(job, info, result)
        except runtime.RuntimeErrorSafe:
            pass
        self.assertFalse(any(x["event"].get("kind") == "REVIEW_COMPLETED" and x["event"].get("payload", {}).get("outcome") == "PASS" for x in self.service.events))

    def test_wrong_head_or_missing_ci_cannot_publish_pass(self):
        job, info = self.prepare_review()
        self.service.checks[0]["head_sha"] = self.policy
        self.rt._finish_review(job, info, self.passed())
        self.assertFalse(any(x["event"]["kind"] == "REVIEW_COMPLETED" for x in self.service.events))
        self.assertIn(info["phase"], ("awaiting_checks", "blocked"))

    def test_allowlist_rejects_extra_committed_file_before_push(self):
        job = self.setup_approved()
        self.candidate()
        (self.repo/"outside.txt").write_text("outside", encoding="utf-8")
        self.git("add", "outside.txt")
        self.git("commit", "-m", "outside approved scope")
        with self.assertRaises(runtime.RuntimeErrorSafe):
            self.rt._finish_implementation(job, self.info(job), {"outcome": "IMPLEMENTATION_READY"})
        self.assertFalse(self.service.events)

    def test_worker_symlink_result_blocks_task_and_does_not_repeat_launch(self):
        job = self.setup_approved()
        self.candidate()
        info = self.info(job)
        outside = self.root/"outside-result.json"
        outside.write_text(json.dumps({"outcome": "IMPLEMENTATION_READY"}), encoding="utf-8")
        try:
            Path(info["result_path"]).symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        self.rt.collect()
        self.assertEqual(self.rt.core.state(self.task)["state"], "ENV_BLOCKED")
        self.rt.collect()
        self.paseo.run_agent.assert_not_called()

    def test_notification_publish_is_not_an_entry_ack(self):
        self.rt.core.ingest(self.opened())
        self.rt.core.ingest(self.event("PLAN_PROPOSED", payload={"plan_pr": 4}))
        self.rt.meta["task_issues"][self.task] = 5
        job = self.rt.core.claim(self.rt.worker_id, kinds=["report"])
        self.rt._report(job)
        self.rt.sync()
        self.assertEqual(self.service.events[-1]["event"]["kind"], "ENTRY_NOTIFICATION")
        self.assertTrue(self.rt.meta["notifications"])
        self.assertEqual(self.rt.meta["acks"], [])

    def test_uncertain_notification_receipt_does_not_leave_permanent_claim_after_restart(self):
        self.rt.core.ingest(self.opened())
        self.rt.core.ingest(self.event("PLAN_PROPOSED", payload={"plan_pr": 4}))
        self.rt.meta["task_issues"][self.task] = 5
        job = self.rt.core.claim(self.rt.worker_id, kinds=["report"])
        self.service.fail_after_publish = "ENTRY_NOTIFICATION"
        try:
            self.rt._report(job)
        except runtime.AdapterError:
            pass
        restarted = runtime.Runtime(self.config, github=self.service, paseo=self.paseo)
        restarted.collect()
        actual = next(j for j in restarted.core.export_snapshot()["jobs"] if j["id"] == job["id"])
        self.assertEqual(actual["status"], "completed", "A notification already saved by GitHub must reconcile after restart")
        self.assertEqual(len([x for x in self.service.events if x["event"]["kind"] == "ENTRY_NOTIFICATION"]), 1)
        self.assertEqual(restarted.meta["acks"], [], "Reconciled publication is still not Planner consumption")

    def test_generic_contract_rejects_shell_string_commands_before_launch(self):
        job = self.setup_approved(test_mode=False)
        malformed = {"allowed_paths": [self.fixture_path], "validation_commands": ["python check.py"]}
        original_git = self.rt.git
        self.rt.git = lambda *args, **kwargs: json.dumps(malformed) if args[0] == "show" else original_git(*args, **kwargs)
        with self.assertRaises(runtime.RuntimeErrorSafe):
            self.rt._contract(job)
        self.paseo.run_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
