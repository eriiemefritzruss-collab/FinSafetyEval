"""Planner entry contracts using temporary Git objects and a mock service."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "entry.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("finsafety_entry_under_test", MODULE)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class FakeGitHub:
    def __init__(self):
        self.login = "trusted-login"
        self.events = []
        self.issues = {5: {"number": 5, "state": "open", "body": "configured isolated issue"}}
        self.created_issues = 0
        self.pr = {}

    def get_login(self):
        return self.login

    def request(self, method, path="", body=None):
        assert method == "GET"
        if not path:
            return {"full_name": "owner/repo"}
        if path.startswith("issues?"):
            return list(self.issues.values())
        if path.startswith("issues/"):
            return self.issues[int(path.split("/")[1])]
        raise AssertionError(path)

    def create_task_issue(self, title, body):
        self.created_issues += 1
        number = 10 + self.created_issues
        issue = {"number": number, "title": title, "body": body, "state": "open"}
        self.issues[number] = issue
        return issue

    def list_events(self, issue):
        return copy.deepcopy([value for value in self.events if value["issue"] == issue])

    def post_event(self, issue, event):
        for value in self.events:
            if value["event"]["event_id"] == event["event_id"]:
                if value["event"] != event:
                    raise entry.AdapterError("EVENT_CONFLICT", "payload conflict")
                return {**copy.deepcopy(value), "created": False}
        item = {"issue": issue, "comment_id": len(self.events) + 1, "author": self.login,
                "event": copy.deepcopy(event), "url": "https://github.com/owner/repo/issues/5#issuecomment-1"}
        self.events.append(item)
        return {**copy.deepcopy(item), "created": True}

    def get_pr(self, number):
        return copy.deepcopy(self.pr)


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="finsafety-entry-")
        self.addCleanup(self.folder.cleanup)
        self.repo = Path(self.folder.name) / "repo space 中文"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "policy.md").write_text("trusted policy\n", encoding="utf-8")
        self.git("add", "policy.md")
        self.git("commit", "-m", "policy")
        self.policy = self.git("rev-parse", "HEAD")
        self.task = "T-TEST-ENTRY"
        self.plan_path = f"plans/{self.task}/plan-v1.md"
        plan = self.repo / self.plan_path
        plan.parent.mkdir(parents=True)
        plan.write_text("frozen plan\n", encoding="utf-8")
        self.git("add", "plans")
        self.git("commit", "-m", "plan")
        self.plan = self.git("rev-parse", "HEAD")
        self.blob = self.git("rev-parse", "HEAD:" + self.plan_path)
        self.config = {"repository": "owner/repo", "repo_path": str(self.repo),
                       "entry_agent_id": "entry-agent-1234", "controller_login": "trusted-login",
                       "policy_source_sha": self.policy, "required_checks": ["offline-contract"],
                       "required_gates": ["Code", "Integration", "Acceptance"],
                       "allowed_test_tasks": [self.task], "task_issues": {self.task: 5}}
        self.service = FakeGitHub()
        self.operator = entry.Entry(self.config, self.service)

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def open_plan(self, test_mode=True, plan_pr=None):
        self.operator.open(self.task, b"raw goal\r\nsecond line\n", test_mode)
        return self.operator.propose(5, self.task, self.plan, self.plan_path, 1, plan_pr)

    def test_open_raw_goal_hash_idempotency_and_mapping_reuse(self):
        goal = "中文 goal\r\nsecond".encode()
        first = self.operator.open(self.task, goal, True)
        second = self.operator.open(self.task, goal, True)
        self.assertEqual(first["issue"], 5)
        self.assertEqual(self.service.created_issues, 0)
        self.assertEqual(len(self.service.events), 1)
        self.assertFalse(second["receipt"]["created"])
        self.assertEqual(first["event"]["binding"]["requirement_revision"], "sha256:" + hashlib.sha256(goal).hexdigest())
        self.assertTrue(all(first["event"]["binding"][key] is None for key in ("plan_sha", "plan_blob_oid", "head_sha", "base_sha", "plan_version")))

    def test_open_discovers_exact_marker_and_does_not_duplicate_issue(self):
        self.config["task_issues"] = {}
        body = entry.TASK_MARKER + "\n<!-- task_id:" + self.task + " -->\n\nexisting"
        self.service.issues[5]["body"] = body
        result = self.operator.open(self.task, b"goal", True)
        self.assertEqual(result["issue"], 5)
        self.assertEqual(self.service.created_issues, 0)

    def test_open_creates_marked_issue_once(self):
        self.config["task_issues"] = {}
        first = self.operator.open(self.task, b"goal", True)
        second = self.operator.open(self.task, b"goal", True)
        self.assertEqual(first["issue"], second["issue"])
        self.assertEqual(self.service.created_issues, 1)
        self.assertTrue(self.service.issues[first["issue"]]["body"].startswith(entry.TASK_MARKER))

    def test_changed_goal_or_unlisted_test_mode_is_rejected(self):
        self.operator.open(self.task, b"goal", True)
        with self.assertRaises(entry.AdapterError):
            self.operator.open(self.task, b"changed goal", True)
        with self.assertRaises(entry.AdapterError) as raised:
            self.operator.open("T-PRODUCTION", b"goal", True)
        self.assertEqual(raised.exception.code, "TEST_SCOPE")

    def test_propose_binds_real_git_plan_blob_and_original_goal_hash(self):
        result = self.open_plan()
        bind = result["event"]["binding"]
        self.assertEqual(bind["plan_blob_oid"], self.blob)
        self.assertEqual(bind["plan_sha"], self.plan)
        self.assertEqual(bind["requirement_revision"], "sha256:" + hashlib.sha256(b"raw goal\r\nsecond line\n").hexdigest())
        self.assertEqual(set(bind), set(entry.BINDINGS))

    def test_propose_rejects_wrong_path_or_missing_commit(self):
        self.operator.open(self.task, b"goal", True)
        for sha, path in (("f" * 40, self.plan_path), (self.plan, "../plan.md"), (self.plan, self.plan_path.replace("v1", "v2"))):
            with self.subTest(sha=sha, path=path), self.assertRaises(entry.AdapterError):
                self.operator.propose(5, self.task, sha, path, 1)

    def test_frozen_handoff_is_read_from_commit_not_mutable_worktree(self):
        path = f"plans/{self.task}/handoff-v1.json"
        frozen = {"allowed_paths": ["output/result.txt"], "validation_commands": [["python", "-c", "print('ok')"]]}
        (self.repo / path).write_text(json.dumps(frozen), encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-m", "freeze handoff")
        sha = self.git("rev-parse", "HEAD")
        (self.repo / path).write_text('{"allowed_paths":["../escape"]}', encoding="utf-8")
        self.operator.open(self.task, b"goal", True)
        result = self.operator.propose(5, self.task, sha, self.plan_path, 1)
        self.assertEqual(result["event"]["payload"]["handoff_path"], path)

    def test_handoff_rejects_escape_glob_and_shell_string(self):
        for handoff in ({"allowed_paths": ["../escape"], "validation_commands": [["python", "test.py"]]},
                        {"allowed_paths": ["src/*"], "validation_commands": [["python", "test.py"]]},
                        {"allowed_paths": ["ok.txt"], "validation_commands": ["python test.py"]}):
            with self.subTest(handoff=handoff), self.assertRaises(entry.AdapterError):
                entry.Entry._validate_handoff(handoff)
        self.operator.open(self.task, b"goal", True)
        with self.assertRaises(entry.AdapterError):
            self.operator.propose(5, self.task, self.plan, self.plan_path, 1, handoff_path="../escape.json")

    def test_test_approval_requires_explicit_flag_and_whitelist(self):
        self.open_plan()
        with self.assertRaises(entry.AdapterError) as raised:
            self.operator.approve(5, self.task)
        self.assertEqual(raised.exception.code, "APPROVAL_REQUIRED")
        approved = self.operator.approve(5, self.task, True)["event"]
        self.assertEqual(approved["actor"], {"role": "User", "id": "trusted-login"})
        self.assertTrue(approved["payload"]["explicit_user_approval"])
        self.assertFalse(approved["payload"]["plan_merged"])
        self.config["allowed_test_tasks"] = []
        with self.assertRaises(entry.AdapterError):
            self.operator.approve(5, self.task, True)

    def test_production_cannot_use_unmerged_or_missing_pr_claim(self):
        self.open_plan(test_mode=False)
        with self.assertRaises(entry.AdapterError) as raised:
            self.operator.approve(5, self.task, True)
        self.assertEqual(raised.exception.code, "PLAN_PR_REQUIRED")
        proposal = self.service.events[-1]["event"]
        proposal["payload"].update(plan_pr=3, plan_merged=True)
        self.service.pr = {"merged": False, "head": {"sha": self.plan}}
        with self.assertRaises(entry.AdapterError) as raised:
            self.operator.approve(5, self.task, True)
        self.assertEqual(raised.exception.code, "PLAN_NOT_MERGED")

    def test_production_approval_uses_actual_merge_sha_and_blob(self):
        self.open_plan(test_mode=False, plan_pr=3)
        self.git("commit", "--allow-empty", "-m", "actual merge result")
        merged = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", merged)
        self.service.pr = {"merged": True, "head": {"sha": self.plan}, "merge_commit_sha": merged,
                           "base": {"ref": "main", "repo": {"full_name": "owner/repo"}}}
        approved = self.operator.approve(5, self.task, True)["event"]
        self.assertEqual(approved["binding"]["plan_sha"], merged)
        self.assertEqual(approved["binding"]["plan_blob_oid"], self.blob)
        self.assertEqual(approved["payload"]["proposed_plan_sha"], self.plan)
        self.assertTrue(approved["payload"]["plan_merged"])

    def test_ack_copies_notification_binding_and_targets_entry(self):
        proposed = self.open_plan()["event"]
        notification = self.operator._event(self.task, "ENTRY_NOTIFICATION", "Coordinator", proposed["binding"],
                                              {"entry_agent_id": self.config["entry_agent_id"]}, 5)
        self.service.post_event(5, notification)
        result = self.operator.ack(5, notification["event_id"])["event"]
        self.assertEqual(result["binding"], notification["binding"])
        self.assertEqual(result["kind"], "ENTRY_ACK")
        self.assertEqual(result["payload"], {"entry_agent_id": self.config["entry_agent_id"], "notification_event_id": notification["event_id"]})
        self.assertEqual(result["actor"]["role"], "Planner")

    def test_ack_rejects_other_entry_or_forged_author(self):
        proposed = self.open_plan()["event"]
        notification = self.operator._event(self.task, "ENTRY_NOTIFICATION", "Coordinator", proposed["binding"],
                                              {"entry_agent_id": "other-entry-agent"}, 5)
        self.service.post_event(5, notification)
        with self.assertRaises(entry.AdapterError) as raised:
            self.operator.ack(5, notification["event_id"])
        self.assertEqual(raised.exception.code, "ENTRY_MISMATCH")
        self.service.events[-1]["event"]["payload"]["entry_agent_id"] = self.config["entry_agent_id"]
        self.service.events[-1]["author"] = "untrusted-user"
        with self.assertRaises(entry.AdapterError) as raised:
            self.operator.ack(5, notification["event_id"])
        self.assertEqual(raised.exception.code, "NOTIFICATION_SOURCE")

    def test_health_is_read_only_and_stop_writes_only_event(self):
        with patch.object(entry.PaseoClient, "inspect_agent", return_value={"Id": self.config["entry_agent_id"], "Status": "idle"}):
            health = self.operator.health()
        self.assertTrue(health["read_only"])
        self.assertEqual(self.service.events, [])
        self.open_plan()
        stopped = self.operator.stop(5, self.task, "stop test")["event"]
        self.assertEqual(stopped["kind"], "TASK_STOPPED")
        self.assertEqual(stopped["payload"]["reason"], "stop test")

    def test_config_before_and_after_subcommand_and_direct_script(self):
        parsed = entry.parser().parse_args(["--config", "before.json", "health", "--config", "after.json"])
        self.assertEqual(parsed.config, "after.json")
        parsed = entry.parser().parse_args(["--config", "before.json", "health"])
        self.assertEqual(parsed.config, "before.json")
        run = subprocess.run([sys.executable, str(MODULE), "ack", "--help"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--notification-event-id", run.stdout)

    def test_configuration_commit_and_actual_account_are_checked(self):
        path = Path(self.folder.name) / "config.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        self.assertEqual(entry.load_config(path)["policy_source_sha"], self.policy)
        wrong = dict(self.config, policy_source_sha="a" * 40)
        path.write_text(json.dumps(wrong), encoding="utf-8")
        with self.assertRaises(entry.AdapterError):
            entry.load_config(path)
        self.service.login = "another-account"
        with self.assertRaises(entry.AdapterError) as raised:
            entry.Entry(self.config, self.service)
        self.assertEqual(raised.exception.code, "ACTOR_MISMATCH")

    def test_open_atomically_extends_notifier_scope_and_preserves_other_settings(self):
        notifier = Path(self.folder.name) / "notifier.json"
        original = {"task_issues": [2, 3], "entry_agent_id": "entry-agent-1234", "interval": 12,
                    "unrelated": {"preserve": "中文"}}
        notifier.write_text(json.dumps(original), encoding="utf-8")
        self.config["notifier_config_path"] = str(notifier)
        first = self.operator.open(self.task, b"goal", True)
        second = self.operator.open(self.task, b"goal", True)
        actual = json.loads(notifier.read_text(encoding="utf-8"))
        self.assertEqual(actual, {**original, "task_issues": [2, 3, 5]})
        self.assertTrue(first["notifier_scope"]["updated"])
        self.assertFalse(second["notifier_scope"]["updated"])
        self.assertEqual(list(notifier.parent.glob(".notifier-scope-*.json")), [])

    def test_failed_scope_replace_retains_original_and_retry_reuses_task_event(self):
        notifier = Path(self.folder.name) / "notifier.json"
        notifier.write_text('{"task_issues":[2],"keep":true}', encoding="utf-8")
        before = notifier.read_bytes()
        self.config["notifier_config_path"] = str(notifier)
        with patch.object(entry.os, "replace", side_effect=OSError("test failure")), self.assertRaises(entry.AdapterError) as raised:
            self.operator.open(self.task, b"goal", True)
        self.assertEqual(raised.exception.code, "NOTIFIER_CONFIG")
        self.assertEqual(notifier.read_bytes(), before)
        self.assertEqual(list(notifier.parent.glob(".notifier-scope-*.json")), [])
        self.operator.open(self.task, b"goal", True)
        self.assertEqual(len(self.service.events), 1)
        self.assertEqual(json.loads(notifier.read_text())["task_issues"], [2, 5])


if __name__ == "__main__":
    unittest.main()
