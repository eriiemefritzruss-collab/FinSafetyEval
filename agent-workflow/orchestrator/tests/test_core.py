import concurrent.futures
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "core.py"
SPEC = importlib.util.spec_from_file_location("orchestrator_core", MODULE)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)
Controller, InvalidEvent = core.Controller, core.InvalidEvent


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="finsafety-coordinator-")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "state.sqlite3"
        self.actors = {role: [role.lower()] for role in ("User", "Planner", "Implementer", "Reviewer", "Coordinator")}
        self.controller = Controller(self.path, trusted_actors=self.actors)
        self.seq = 0
        self.initial = dict(requirement_revision="sha256:" + "a" * 64, plan_version=None, plan_sha=None,
                            plan_blob_oid=None, head_sha=None, base_sha=None, policy_source_sha="1" * 40)

    def event(self, kind, payload=None, binding=None, role=None, event_id=None):
        self.seq += 1
        role = role or next(iter(sorted(core.ROLES[kind])))
        current = self.controller.state("T-TEST")
        return dict(schema_version=1, event_id=event_id or "event-" + str(self.seq), task_id="T-TEST",
                    repository="owner/repo", kind=kind, actor={"id": role.lower(), "role": role},
                    binding=deepcopy(binding if binding is not None else current["binding"] if current else self.initial),
                    payload=payload or {}, proof={"source": "authenticated-test-adapter", "url": "https://example.invalid/events/" + str(self.seq)})

    def ingest(self, kind, payload=None, binding=None, role=None):
        event = self.event(kind, payload, binding, role)
        jobs = self.controller.ingest(event)
        return event, jobs

    def open(self, test_mode=True):
        return self.ingest("TASK_OPENED", dict(goal="isolated marker task", test_mode=test_mode,
                    entry_agent_id="windows-entry", required_checks=["offline-smoke"], required_gates=["Code", "Integration", "Acceptance"]))

    def propose(self, version=1):
        binding = deepcopy(self.controller.state("T-TEST")["binding"])
        binding.update(plan_version=version, plan_sha=str(version + 1) * 40, plan_blob_oid="3" * 40)
        return self.ingest("PLAN_PROPOSED", {"plan_pr": 4}, binding)

    def approve(self, **extra):
        return self.ingest("PLAN_APPROVED", {"decision": "approve", **extra})

    def ready(self, head="4", base="5"):
        binding = deepcopy(self.controller.state("T-TEST")["binding"])
        binding.update(head_sha=head * 40, base_sha=base * 40)
        return self.ingest("IMPLEMENTATION_READY", {"implementation_pr": 5}, binding)

    def prepare(self):
        self.open()
        self.propose()
        self.approve()
        return self.ready()

    def review(self, outcome="PASS", **extra):
        candidate = self.controller.state("T-TEST")["binding"]["head_sha"]
        payload = dict(outcome=outcome, report_url="https://example.invalid/review/1", gates={name: {"status": "PASS" if name != "Deployment" else "N/A", "reason": "isolated evidence"} for name in core.GATES},
                       required_checks=[{"name": "offline-smoke", "status": "success", "head_sha": candidate}], blocking_findings=[])
        payload.update(extra)
        return self.ingest("REVIEW_COMPLETED", payload)

    def accepted(self, event):
        return bool(self.controller.event_result(event["event_id"])["accepted"])

    def test_normal_chain_waits_for_user_and_never_creates_merge_job(self):
        _, opened = self.open()
        self.assertEqual(opened[0]["kind"], "plan")
        self.propose()
        self.assertEqual(self.controller.state("T-TEST")["state"], "PLAN_REVIEW")
        self.approve()
        self.ready()
        event, jobs = self.review()
        self.assertTrue(self.accepted(event))
        self.assertEqual(self.controller.state("T-TEST")["state"], "READY_FOR_HUMAN_MERGE")
        self.assertEqual(jobs[0]["kind"], "report")
        self.assertFalse(jobs[0]["payload"]["merge_authorized"])
        self.assertEqual(jobs[0]["payload"]["return_agent_id"], "windows-entry")
        self.assertNotIn("merge", {j["kind"] for j in self.controller.export_snapshot()["jobs"]})

    def test_plan_proposal_consumes_original_plan_job(self):
        _, jobs = self.open()
        self.propose()
        saved = {j["id"]: j for j in self.controller.export_snapshot()["jobs"]}
        self.assertEqual(saved[jobs[0]["id"]]["status"], "superseded")
        self.assertIsNone(self.controller.claim("mac", kinds=["plan"]))

    def test_same_event_is_idempotent_after_restart(self):
        event, jobs = self.open()
        restarted = Controller(self.path, trusted_actors=self.actors)
        self.assertEqual(restarted.ingest(event), [])
        self.assertEqual(len(restarted.export_snapshot()["jobs"]), 1)
        self.assertEqual(restarted.export_snapshot()["jobs"][0]["id"], jobs[0]["id"])

    def test_event_identifier_cannot_be_reused_with_other_contents(self):
        event, _ = self.open()
        event["payload"]["goal"] = "another goal"
        with self.assertRaises(core.EventConflict):
            self.controller.ingest(event)

    def test_duplicate_semantic_candidate_cannot_create_second_review(self):
        self.prepare()
        first = len([j for j in self.controller.export_snapshot()["jobs"] if j["kind"] == "review"])
        event, jobs = self.ready()
        self.assertTrue(self.accepted(event))
        self.assertEqual(jobs, [])
        self.assertEqual(first, len([j for j in self.controller.export_snapshot()["jobs"] if j["kind"] == "review"]))

    def test_concurrent_claim_has_one_winner(self):
        self.open()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(self.controller.claim, ("worker-a", "worker-b")))
        self.assertEqual(sum(j is not None for j in claimed), 1)

    def test_restart_does_not_reclaim_running_job(self):
        self.open()
        job = self.controller.claim("worker")
        self.controller.mark_dispatched(job["id"], "worker", "real-agent-id")
        restarted = Controller(self.path, trusted_actors=self.actors)
        self.assertIsNone(restarted.claim("worker-after-restart"))

    def test_unapproved_candidate_is_rejected_and_persisted(self):
        self.open()
        self.propose()
        event, jobs = self.ready()
        self.assertFalse(self.accepted(event))
        self.assertIn("approval", self.controller.event_result(event["event_id"])["reason"])
        self.assertEqual(jobs, [])
        self.assertEqual(self.controller.state("T-TEST")["state"], "PLAN_REVIEW")

    def test_only_explicit_user_can_approve(self):
        self.open()
        self.propose()
        event = self.event("PLAN_APPROVED", {"decision": "approve"}, role="Planner")
        with self.assertRaises(InvalidEvent):
            self.controller.ingest(event)
        event = self.event("PLAN_APPROVED", {"decision": "sounds good"}, role="User")
        with self.assertRaises(InvalidEvent):
            self.controller.ingest(event)

    def test_production_task_requires_verified_merged_plan(self):
        self.open(test_mode=False)
        self.propose()
        event, jobs = self.approve()
        self.assertFalse(self.accepted(event))
        self.assertEqual(jobs, [])
        event, jobs = self.approve(plan_merged=True)
        self.assertTrue(self.accepted(event))
        self.assertEqual(jobs[0]["kind"], "implement")

    def test_verified_squash_merge_binds_actual_plan_commit(self):
        self.open(test_mode=False)
        self.propose()
        binding = deepcopy(self.controller.state("T-TEST")["binding"])
        proposed = binding["plan_sha"]
        binding["plan_sha"] = "9" * 40
        event, jobs = self.ingest("PLAN_APPROVED", {"decision": "approve", "plan_merged": True, "proposed_plan_sha": proposed}, binding)
        self.assertTrue(self.accepted(event))
        self.assertEqual(jobs[0]["payload"]["binding"]["plan_sha"], "9" * 40)

    def test_old_head_review_cannot_pass(self):
        self.prepare()
        old = self.event("REVIEW_COMPLETED", {"outcome": "PASS", "report_url": "https://example.invalid/r"})
        changed = deepcopy(old["binding"])
        changed["head_sha"] = "6" * 40
        self.ingest("HEAD_CHANGED", {}, changed)
        self.assertEqual(self.controller.ingest(old), [])
        self.assertFalse(self.accepted(old))
        self.assertEqual(self.controller.state("T-TEST")["binding"]["head_sha"], "6" * 40)
        self.assertEqual(self.controller.state("T-TEST")["state"], "REVIEWING")

    def test_base_change_invalidates_completed_pass(self):
        self.prepare()
        self.review()
        binding = deepcopy(self.controller.state("T-TEST")["binding"])
        binding["base_sha"] = "7" * 40
        event, jobs = self.ingest("HEAD_CHANGED", {}, binding)
        self.assertTrue(self.accepted(event))
        self.assertEqual(self.controller.state("T-TEST")["state"], "REVIEWING")
        self.assertIsNone(self.controller.state("T-TEST")["last_review"])
        self.assertEqual(jobs[0]["kind"], "review")

    def test_code_change_routes_to_implementer_and_new_review_round(self):
        self.prepare()
        _, jobs = self.review("CODE_CHANGES_REQUIRED", findings=["F-1"])
        state = self.controller.state("T-TEST")
        self.assertEqual((state["state"], state["next_owner"], state["review_round"]), ("IMPLEMENTING", "Implementer", 2))
        self.assertEqual(jobs[0]["kind"], "implement")
        self.ready("6")
        self.review()
        self.assertEqual(self.controller.state("T-TEST")["state"], "READY_FOR_HUMAN_MERGE")

    def test_plan_failure_revokes_approval_and_new_plan_needs_user(self):
        self.prepare()
        _, jobs = self.review("PLAN_REVISION_REQUIRED")
        self.assertFalse(self.controller.state("T-TEST")["approved"])
        self.assertEqual(jobs[0]["kind"], "plan")
        self.propose(2)
        self.assertEqual(self.controller.state("T-TEST")["state"], "PLAN_REVIEW")
        event, _ = self.ready("6")
        self.assertFalse(self.accepted(event))
        self.approve()
        self.assertEqual(self.controller.state("T-TEST")["state"], "IMPLEMENTING")

    def test_environment_resume_keeps_business_review_round(self):
        self.prepare()
        self.review("ENV_BLOCKED")
        state = self.controller.state("T-TEST")
        self.assertEqual(state["state"], "ENV_BLOCKED")
        event, jobs = self.ingest("ENV_RESUMED")
        self.assertTrue(self.accepted(event))
        self.assertEqual(jobs[0]["kind"], "review")
        self.assertEqual(self.controller.state("T-TEST")["review_round"], 1)

    def test_review_round_limit_pauses_for_user(self):
        self.prepare()
        for index, head in enumerate(("6", "7", "8")):
            self.review("CODE_CHANGES_REQUIRED")
            if index < 2:
                self.ready(head)
        state = self.controller.state("T-TEST")
        self.assertEqual(state["state"], "NEEDS_HUMAN")
        self.assertIn("limit", state["blocked_reason"])

    def test_pass_requires_all_gate_and_ci_evidence(self):
        mutations = ({"gates": {}}, {"required_checks": []}, {"blocking_findings": ["F-1"]},
                     {"required_checks": [{"name": "offline-smoke", "status": "success", "head_sha": "9" * 40}]})
        self.prepare()
        for mutation in mutations:
            event, jobs = self.review(**mutation)
            self.assertFalse(self.accepted(event))
            self.assertEqual(jobs, [])
            self.assertEqual(self.controller.state("T-TEST")["state"], "REVIEWING")

    def test_gate_fail_or_required_na_cannot_pass(self):
        self.prepare()
        gates = {n: {"status": "PASS", "reason": "proof"} for n in core.GATES}
        for status in ("FAIL", "BLOCKED", "N/A"):
            gates["Code"]["status"] = status
            event, _ = self.review(gates=gates)
            self.assertFalse(self.accepted(event))

    def test_actor_identity_allowlist_cannot_be_omitted_or_forged(self):
        with self.assertRaises(ValueError):
            Controller(self.path)
        event = self.event("TASK_OPENED", {})
        event["actor"]["id"] = "untrusted"
        with self.assertRaises(InvalidEvent):
            self.controller.ingest(event)

    def test_schema_rejects_wrong_types_without_crash(self):
        self.open()
        self.propose()
        self.approve()
        self.ready()
        for change in (lambda e: e["actor"].update(role=[]), lambda e: e["payload"].update(outcome=[]),
                       lambda e: e["binding"].update(plan_version=True), lambda e: e.update(schema_version=True)):
            event = self.event("REVIEW_COMPLETED", {"outcome": "PASS"})
            change(event)
            with self.assertRaises(InvalidEvent):
                self.controller.ingest(event)

    def test_local_only_review_cannot_complete(self):
        self.prepare()
        event = self.event("REVIEW_COMPLETED", {"outcome": "PASS", "report_url": "file:///tmp/review.md"})
        with self.assertRaises(InvalidEvent):
            self.controller.ingest(event)

    def test_failed_dispatch_can_retry_only_before_external_receipt(self):
        self.open()
        job = self.controller.claim("worker")
        failed = self.controller.fail(job["id"], "worker", "transport not started", retryable=True)
        self.assertEqual(failed["status"], "pending")
        again = self.controller.claim("worker-2")
        self.assertEqual(again["id"], job["id"])
        self.controller.mark_dispatched(again["id"], "worker-2", "external-agent")
        failed = self.controller.fail(again["id"], "worker-2", "external timeout", retryable=True)
        self.assertEqual(failed["status"], "uncertain")
        event, jobs = self.ingest("ENV_RESUMED")
        self.assertFalse(self.accepted(event))
        self.assertEqual(jobs, [])
        event, jobs = self.ingest("ENV_RESUMED", {"external_execution_stopped": True})
        self.assertTrue(self.accepted(event))
        self.assertEqual(jobs[0]["kind"], "plan")

    def test_completion_receipt_is_idempotent_and_owned(self):
        self.open()
        job = self.controller.claim("worker")
        with self.assertRaises(core.JobConflict):
            self.controller.complete(job["id"], "other", {})
        self.controller.complete(job["id"], "worker", {"receipt": "saved"})
        self.controller.complete(job["id"], "worker", {"receipt": "saved"})
        with self.assertRaises(core.JobConflict):
            self.controller.complete(job["id"], "worker", {"receipt": "changed"})

    def test_task_can_stop_while_waiting_for_plan(self):
        self.open()
        event, _ = self.ingest("TASK_STOPPED", {"reason": "stop"})
        self.assertTrue(self.accepted(event))
        self.assertEqual(self.controller.state("T-TEST")["state"], "STOPPED")
        event, _ = self.propose()
        self.assertFalse(self.accepted(event))

    def test_unknown_task_and_wrong_repository_do_not_create_jobs(self):
        binding = deepcopy(self.initial)
        binding.update(plan_version=1, plan_sha="2"*40, plan_blob_oid="3"*40)
        event, jobs = self.ingest("PLAN_PROPOSED", binding=binding)
        self.assertFalse(self.accepted(event))
        self.assertEqual(jobs, [])
        self.open()
        event = self.event("PLAN_PROPOSED", binding=binding)
        event["repository"] = "another/repo"
        self.controller.ingest(event)
        self.assertFalse(self.accepted(event))

    def test_json_snapshot_is_exportable(self):
        self.prepare()
        self.review()
        value = self.controller.export_snapshot()
        self.assertEqual(json.loads(json.dumps(value))["tasks"][0]["state"], "READY_FOR_HUMAN_MERGE")

    def test_implementation_blocked_plan_revision_revokes_approval_and_routes_planner(self):
        self.open()
        self.propose()
        _, implementation_jobs = self.approve()
        event, jobs = self.ingest("IMPLEMENTATION_BLOCKED", {"outcome": "PLAN_REVISION_REQUIRED", "summary": "Assumed API is absent", "evidence": ["src/example.py:42"]})
        state = self.controller.state("T-TEST")
        self.assertTrue(self.accepted(event))
        self.assertEqual((state["state"], state["next_owner"], state["approved"]), ("PLAN_REVISION_REQUIRED", "Planner", False))
        self.assertEqual([j["kind"] for j in jobs], ["plan", "report"])
        self.assertIsNone(state["last_review"])
        self.assertEqual(state["implementation_blocker"]["evidence"], ["src/example.py:42"])
        saved = {j["id"]: j for j in self.controller.export_snapshot()["jobs"]}
        self.assertEqual(saved[implementation_jobs[0]["id"]]["status"], "superseded")
        self.assertNotIn("review", jobs[0]["payload"])

    def test_implementation_blocked_environment_resumes_implementation_without_review_round(self):
        self.open()
        self.propose()
        self.approve()
        event, jobs = self.ingest("IMPLEMENTATION_BLOCKED", {"outcome": "ENV_BLOCKED", "summary": "Test service unavailable"})
        state = self.controller.state("T-TEST")
        self.assertTrue(self.accepted(event))
        self.assertEqual((state["state"], state["next_owner"], state["resume_kind"]), ("ENV_BLOCKED", "Environment owner", "implement"))
        self.assertFalse(state["execution_uncertain"])
        self.assertEqual([j["kind"] for j in jobs], ["report"])
        resumed, jobs = self.ingest("ENV_RESUMED")
        self.assertTrue(self.accepted(resumed))
        self.assertEqual(jobs[0]["kind"], "implement")
        self.assertEqual(self.controller.state("T-TEST")["review_round"], 1)

    def test_implementation_blocked_human_routes_only_report_to_user(self):
        self.open()
        self.propose()
        self.approve()
        event, jobs = self.ingest("IMPLEMENTATION_BLOCKED", {"outcome": "NEEDS_HUMAN", "summary": "Requirement conflict"})
        self.assertTrue(self.accepted(event))
        state = self.controller.state("T-TEST")
        self.assertEqual((state["state"], state["next_owner"]), ("NEEDS_HUMAN", "User"))
        self.assertEqual([j["kind"] for j in jobs], ["report"])
        self.assertIsNone(state["last_review"])

    def test_implementation_blocked_rejects_unapproved_wrong_phase_and_stale_plan(self):
        self.open()
        self.propose()
        payload = {"outcome": "PLAN_REVISION_REQUIRED", "summary": "Plan blocker"}
        event, jobs = self.ingest("IMPLEMENTATION_BLOCKED", payload)
        self.assertFalse(self.accepted(event))
        self.assertEqual(jobs, [])
        self.approve()
        stale = deepcopy(self.controller.state("T-TEST")["binding"])
        stale["plan_sha"] = "9" * 40
        event, jobs = self.ingest("IMPLEMENTATION_BLOCKED", payload, stale)
        self.assertFalse(self.accepted(event))
        self.assertEqual(jobs, [])
        self.ready()
        event, jobs = self.ingest("IMPLEMENTATION_BLOCKED", payload)
        self.assertFalse(self.accepted(event))
        self.assertEqual(jobs, [])
        self.assertEqual(self.controller.state("T-TEST")["state"], "REVIEWING")

    def test_implementation_blocked_role_outcome_and_summary_are_strict(self):
        self.open()
        self.propose()
        self.approve()
        payload = {"outcome": "PLAN_REVISION_REQUIRED", "summary": "Actual plan blocker"}
        for role in ("Reviewer", "Coordinator"):
            with self.assertRaises(InvalidEvent):
                self.controller.ingest(self.event("IMPLEMENTATION_BLOCKED", payload, role=role))
        for outcome in ("PASS", "CODE_CHANGES_REQUIRED", [], None):
            with self.assertRaises(InvalidEvent):
                self.controller.ingest(self.event("IMPLEMENTATION_BLOCKED", {**payload, "outcome": outcome}))
        for summary in (None, "", "   "):
            with self.assertRaises(InvalidEvent):
                self.controller.ingest(self.event("IMPLEMENTATION_BLOCKED", {**payload, "summary": summary}))

    def test_review_environment_resume_retains_implementation_pr(self):
        self.open()
        self.propose()
        self.approve()
        binding = deepcopy(self.controller.state("T-TEST")["binding"])
        binding.update(head_sha="4" * 40, base_sha="5" * 40)
        self.ingest("IMPLEMENTATION_READY", {"implementation_pr": 9}, binding)
        self.review("ENV_BLOCKED")
        self.assertEqual(self.controller.state("T-TEST")["implementation_pr"], 9)
        event, jobs = self.ingest("ENV_RESUMED")
        self.assertTrue(self.accepted(event))
        review_job = next(job for job in jobs if job["kind"] == "review")
        self.assertEqual(review_job["payload"]["implementation_pr"], 9)

    def test_new_plan_clears_previous_implementation_pr(self):
        self.prepare()
        self.assertEqual(self.controller.state("T-TEST")["implementation_pr"], 5)
        self.review("PLAN_REVISION_REQUIRED")
        self.propose(2)
        self.assertIsNone(self.controller.state("T-TEST")["implementation_pr"])
        _, jobs = self.approve()
        self.assertIsNone(jobs[0]["payload"]["implementation_pr"])

    def test_head_changed_preserves_pr_when_omitted_and_updates_when_supplied(self):
        self.prepare()
        binding = deepcopy(self.controller.state("T-TEST")["binding"])
        binding["head_sha"] = "6" * 40
        event, jobs = self.ingest("HEAD_CHANGED", {}, binding)
        self.assertTrue(self.accepted(event))
        self.assertEqual(self.controller.state("T-TEST")["implementation_pr"], 5)
        self.assertEqual(jobs[0]["payload"]["implementation_pr"], 5)
        binding["head_sha"] = "7" * 40
        event, jobs = self.ingest("HEAD_CHANGED", {"implementation_pr": 9}, binding)
        self.assertTrue(self.accepted(event))
        self.assertEqual(self.controller.state("T-TEST")["implementation_pr"], 9)
        self.assertEqual(jobs[0]["payload"]["implementation_pr"], 9)
        # Late PR attachment/change must not leave a queued review bound to the old PR.
        event, jobs = self.ingest("HEAD_CHANGED", {"implementation_pr": 11}, binding)
        self.assertTrue(self.accepted(event))
        self.assertEqual(jobs[0]["payload"]["implementation_pr"], 11)


if __name__ == "__main__":
    unittest.main()
