"""Failure drills using real, disposable Git commits; no network or model API."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "check_handoff.py"
SPEC = importlib.util.spec_from_file_location("check_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="finsafety-handoff-")
        cls.repo = Path(cls.temporary.name)
        cls.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        cls.git("init", "-q", "-b", "main")
        cls.git("config", "user.name", "Handoff fixture")
        cls.git("config", "user.email", "fixture@example.invalid")
        cls.git("config", "core.autocrlf", "false")
        (cls.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        cls.commit("initial")
        cls.initial = cls.git("rev-parse", "HEAD")
        plan = cls.repo / "plans/T-001/plan-v1.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("Frozen fixture requirement and plan.\n", encoding="utf-8")
        cls.commit("approve plan fixture")
        cls.plan = cls.git("rev-parse", "HEAD")
        cls.blob = cls.git("rev-parse", "HEAD:plans/T-001/plan-v1.md")
        cls.git("checkout", "-q", "-b", "candidate")
        (cls.repo / "feature.txt").write_text("implementation\n", encoding="utf-8")
        cls.commit("implement")
        cls.head = cls.git("rev-parse", "HEAD")
        cls.git("checkout", "-q", "main")
        (cls.repo / "base.txt").write_text("concurrent base update\n", encoding="utf-8")
        cls.commit("base update")
        cls.base = cls.git("rev-parse", "HEAD")
        cls.git("merge", "-q", "--no-ff", "candidate", "-m", "integration fixture")
        cls.integration = cls.git("rev-parse", "HEAD")
        cls.git("checkout", "-q", "-b", "candidate-plan-edit", cls.head)
        plan.write_text("Candidate changed the approved plan.\n", encoding="utf-8")
        cls.commit("candidate plan drift")
        cls.changed_head = cls.git("rev-parse", "HEAD")
        cls.git("checkout", "-q", "-b", "base-plan-edit", cls.base)
        plan.write_text("Target branch changed the approved plan.\n", encoding="utf-8")
        cls.commit("target plan drift")
        cls.changed_base = cls.git("rev-parse", "HEAD")
        cls.git("checkout", "-q", "main")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.run(
            ["git", "-C", str(cls.repo), *args], check=True, capture_output=True,
            text=True, env=cls.env,
        ).stdout.strip()

    @classmethod
    def commit(cls, message):
        cls.git("add", ".")
        cls.git("commit", "-q", "-m", message)

    def setUp(self):
        common = {
            "task_id": "T-001",
            "repository": "example/FinSafetyEval",
            "requirement_revision": "sha256:" + "1" * 64,
            "plan_version": 1,
            "approved_plan_sha": self.plan,
            "plan_path": "plans/T-001/plan-v1.md",
            "plan_blob_oid": self.blob,
            "code_base_sha": self.plan,
            "policy_version": "workflow-v1",
        }
        self.approval = {
            **common, "approved_by": "human fixture",
            "required_gates": ["Code", "Integration", "Acceptance"],
            "required_checks": ["offline-smoke"],
            "require_integration_commit": True,
        }
        self.current = {**common, "pr_head_sha": self.head, "tested_base_sha": self.base}
        self.review = {
            **self.current, "tested_integration_sha": self.integration,
            "overall": "PASS",
            "gates": {name: {"status": "PASS", "reason": "Independent fixture evidence"}
                      for name in handoff.GATES},
            "required_checks": [{"name": "offline-smoke", "status": "success", "head_sha": self.head}],
            "blocking_findings": [],
        }
        self.review["gates"]["Deployment"] = {"status": "N/A", "reason": "CLI-only task has no deployment"}

    def evaluate(self):
        return handoff.evaluate(self.repo, self.approval, self.review, self.current)

    def assert_state(self, state):
        output = self.evaluate()
        self.assertEqual(output["state"], state, output)
        self.assertTrue(output["advisory"])
        self.assertFalse(output["merge_authorized"])
        return output

    def test_pass_still_requires_human_confirmation(self):
        self.assertEqual(self.assert_state("READY_FOR_HUMAN_MERGE")["next_owner"], "User")

    def test_missing_approval(self):
        self.approval = None
        self.assert_state("NEEDS_HUMAN")

    def test_every_binding_is_required(self):
        for name in handoff.BINDINGS:
            with self.subTest(name=name):
                original = self.approval.pop(name)
                self.assert_state("NEEDS_HUMAN")
                self.approval[name] = original

    def test_no_approver_is_not_approval(self):
        del self.approval["approved_by"]
        self.assert_state("NEEDS_HUMAN")

    def test_abbreviated_sha_rejected(self):
        self.current["pr_head_sha"] = self.head[:7]
        self.assert_state("NEEDS_HUMAN")

    def test_nonexistent_git_commit_rejected(self):
        self.current["pr_head_sha"] = self.review["pr_head_sha"] = "a" * 40
        self.assert_state("NEEDS_HUMAN")

    def test_old_candidate_review_is_stale(self):
        self.current["pr_head_sha"] = self.integration
        self.assertEqual(self.assert_state("REVIEWING")["next_owner"], "Reviewer")

    def test_old_target_base_review_is_stale(self):
        self.review["tested_base_sha"] = self.plan
        self.assert_state("REVIEWING")

    def test_approved_requirement_drift_requires_user(self):
        self.current["requirement_revision"] = "sha256:" + "2" * 64
        self.assert_state("NEEDS_HUMAN")

    def test_old_plan_review_is_stale(self):
        self.review["plan_version"] = 2
        self.assert_state("REVIEWING")

    def test_plan_blob_must_match_approved_path(self):
        wrong_blob = self.git("rev-parse", self.initial + ":README.md")
        for value in (self.approval, self.current, self.review):
            value["plan_blob_oid"] = wrong_blob
        self.assert_state("NEEDS_HUMAN")

    def test_plan_commit_must_be_in_target_base(self):
        self.current["tested_base_sha"] = self.review["tested_base_sha"] = self.initial
        self.assert_state("NEEDS_HUMAN")

    def test_candidate_must_contain_approved_plan(self):
        self.current["pr_head_sha"] = self.review["pr_head_sha"] = self.initial
        self.assert_state("NEEDS_HUMAN")

    def test_candidate_cannot_change_approved_plan(self):
        self.current["pr_head_sha"] = self.review["pr_head_sha"] = self.changed_head
        self.assert_state("NEEDS_HUMAN")

    def test_target_plan_change_invalidates_approval(self):
        self.current["tested_base_sha"] = self.review["tested_base_sha"] = self.changed_base
        self.assert_state("NEEDS_HUMAN")

    def test_implementation_start_must_include_approval(self):
        for value in (self.approval, self.current, self.review):
            value["code_base_sha"] = self.initial
        self.assert_state("NEEDS_HUMAN")

    def test_implementation_start_must_come_from_target_history(self):
        for value in (self.approval, self.current, self.review):
            value["code_base_sha"] = self.head
        self.assert_state("NEEDS_HUMAN")

    def test_implementation_start_must_be_candidate_ancestor(self):
        for value in (self.approval, self.current, self.review):
            value["code_base_sha"] = self.base
        self.assert_state("NEEDS_HUMAN")

    def test_integration_commit_must_include_target_and_candidate(self):
        self.review["tested_integration_sha"] = self.head
        self.assert_state("NEEDS_HUMAN")

    def test_missing_required_integration_evidence_is_blocked(self):
        self.review["tested_integration_sha"] = None
        self.assert_state("ENV_BLOCKED")

    def test_null_integration_allowed_when_explicitly_not_required(self):
        self.approval["require_integration_commit"] = False
        self.review["tested_integration_sha"] = None
        self.assert_state("READY_FOR_HUMAN_MERGE")

    def test_non_pass_outcomes_route_to_responsible_role(self):
        for state, owner in handoff.ROUTES.items():
            with self.subTest(state=state):
                self.review["overall"] = state
                self.assertEqual(self.assert_state(state)["next_owner"], owner)

    def test_failure_on_any_gate_cannot_pass(self):
        for name in handoff.GATES:
            with self.subTest(gate=name):
                original = self.review["gates"][name]["status"]
                self.review["gates"][name]["status"] = "FAIL"
                self.assert_state("CODE_CHANGES_REQUIRED")
                self.review["gates"][name]["status"] = original

    def test_blocked_gate_does_not_pass(self):
        self.review["gates"]["Acceptance"]["status"] = "BLOCKED"
        self.assert_state("ENV_BLOCKED")

    def test_required_gate_cannot_be_waived_by_report(self):
        self.review["gates"]["Integration"]["status"] = "N/A"
        self.assert_state("ENV_BLOCKED")

    def test_na_requires_reason(self):
        self.review["gates"]["Deployment"]["reason"] = ""
        self.assert_state("NEEDS_HUMAN")

    def test_all_four_gates_must_be_present(self):
        del self.review["gates"]["Deployment"]
        self.assert_state("NEEDS_HUMAN")

    def test_blocking_finding_prevents_pass(self):
        self.review["blocking_findings"] = ["F-01"]
        self.assert_state("CODE_CHANGES_REQUIRED")

    def test_failed_ci_prevents_pass(self):
        self.review["required_checks"][0]["status"] = "failure"
        self.assert_state("CODE_CHANGES_REQUIRED")

    def test_ci_cannot_be_empty(self):
        self.review["required_checks"] = []
        self.assert_state("ENV_BLOCKED")

    def test_expected_ci_name_cannot_be_replaced(self):
        self.review["required_checks"][0]["name"] = "unrelated-green-check"
        self.assert_state("ENV_BLOCKED")

    def test_ci_for_old_head_is_stale(self):
        self.review["required_checks"][0]["head_sha"] = self.plan
        self.assert_state("REVIEWING")

    def test_skipped_ci_is_not_success(self):
        self.review["required_checks"][0]["status"] = "skipped"
        self.assert_state("ENV_BLOCKED")

    def test_duplicate_checks_rejected(self):
        self.review["required_checks"] *= 2
        self.assert_state("NEEDS_HUMAN")

    def test_invalid_check_status_is_structured_failure(self):
        self.review["required_checks"][0]["status"] = []
        self.assert_state("NEEDS_HUMAN")

    def test_empty_or_invalid_policy_rejected(self):
        for key, value in (("required_gates", []), ("required_checks", []),
                           ("required_gates", ["Integration"]), ("required_gates", ["unknown"]),
                           ("require_integration_commit", "true")):
            with self.subTest(key=key, value=value):
                original = self.approval[key]
                self.approval[key] = value
                self.assert_state("NEEDS_HUMAN")
                self.approval[key] = original

    def test_malformed_revision_or_path_rejected(self):
        for key, value in (("requirement_revision", "latest"), ("plan_version", True),
                           ("plan_path", "../plan.md"), ("plan_path", "/tmp/plan.md"),
                           ("plan_path", "plans\\plan.md")):
            with self.subTest(key=key, value=value):
                original = self.approval[key]
                self.approval[key] = value
                self.assert_state("NEEDS_HUMAN")
                self.approval[key] = original

    def test_cli_emits_json_and_nonzero_on_blocked_input(self):
        with tempfile.TemporaryDirectory(prefix="handoff-json-") as folder:
            paths = {}
            for name in ("approval", "review", "current"):
                path = Path(folder) / (name + ".json")
                path.write_text(json.dumps(getattr(self, name)), encoding="utf-8")
                paths[name] = str(path)
            command = [sys.executable, str(SCRIPT), "--repo", str(self.repo)]
            for name, path in paths.items():
                command.extend(["--" + name, path])
            passed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(json.loads(passed.stdout)["state"], "READY_FOR_HUMAN_MERGE")
            Path(paths["approval"]).write_text("null", encoding="utf-8")
            blocked = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(blocked.returncode, 1, blocked.stderr)
            self.assertEqual(json.loads(blocked.stdout)["state"], "NEEDS_HUMAN")

    def test_cli_unreadable_input_is_structured_failure(self):
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo),
             "--approval", str(self.repo / "absent.json"),
             "--review", str(self.repo / "absent.json"), "--current", str(self.repo / "absent.json")],
            capture_output=True, text=True,
        )
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["state"], "NEEDS_HUMAN")


if __name__ == "__main__":
    unittest.main()
