import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from orchestrator.adapters import AdapterError
from orchestrator.notifier import Notifier, InvalidNotification, validate_config


class FakeGitHub:
    def __init__(self, events=None):
        self.events = events or {}
        self.read_issues = []

    def list_events(self, issue):
        self.read_issues.append(issue)
        return copy.deepcopy(self.events.get(issue, []))


class FakePaseo:
    def __init__(self):
        self.agent = {"Id": "planner-1234", "Status": "idle", "Archived": False}
        self.sent = []
        self.error = None
        self.delivery = "queued"

    def inspect_agent(self, agent_id):
        return dict(self.agent)

    def send_prompt(self, agent_id, prompt):
        self.sent.append((agent_id, prompt))
        if self.error:
            raise self.error
        return {"agent_id": agent_id, "receipt": {"agentId": agent_id, "status": self.delivery}}


def notification(event_id="notify-1", comment_id=10, author="owner"):
    return {"comment_id": comment_id, "author": author, "url": "https://github.com/owner/repo/issues/4#issuecomment-10",
            "event": {"schema_version": 1, "event_id": event_id, "task_id": "T-TEST", "repository": "owner/repo",
                      "kind": "ENTRY_NOTIFICATION", "actor": {"role": "Coordinator", "id": "claimed-id"},
                      "binding": {"head_sha": "a"*40}, "payload": {"entry_agent_id": "planner-1234", "summary": "Independent review passed",
                      "report_id": "report-1", "report_url": "https://github.com/owner/repo/issues/4#issuecomment-9",
                      "ack_command": "python entry.py ack --notification-event-id notify-1"}}}


def ack(note=None, comment_id=11, author="owner"):
    event = copy.deepcopy((note or notification())["event"])
    event.update(event_id="ack-1", kind="ENTRY_ACK", actor={"role": "Planner", "id": "owner"})
    event["payload"] = {"notification_event_id": (note or notification())["event"]["event_id"], "entry_agent_id": "planner-1234"}
    return {"comment_id": comment_id, "author": author, "event": event}


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="notifier-tests-")
        self.path = Path(self.folder.name) / "notifier.sqlite"
        self.config = {"repository": "owner/repo", "repo_path": self.folder.name, "paseo_binary": "paseo",
                       "entry_agent_id": "planner-1234", "controller_login": "owner", "task_issues": [4], "state_dir": self.folder.name}
        self.github = FakeGitHub({4: [notification()]})
        self.paseo = FakePaseo()
        self.workers = []
        self.worker = self.create()

    def create(self):
        worker = Notifier(self.config, self.path, github=self.github, paseo=self.paseo)
        self.workers.append(worker)
        return worker

    def tearDown(self):
        for worker in self.workers:
            try:
                worker.close()
            except Exception:
                pass
        self.folder.cleanup()

    def test_busy_agent_defers_without_sending(self):
        self.paseo.agent["Status"] = "running"
        result = self.worker.run_once()
        self.assertEqual(result["deferred"], 1)
        self.assertFalse(self.paseo.sent)
        self.assertEqual(self.worker.status(4, "notify-1")["status"], "pending")
        self.paseo.agent["Status"] = "idle"
        self.assertEqual(self.worker.run_once()["queued"], 1)

    def test_actual_author_overrides_claimed_role(self):
        self.github.events[4][0]["author"] = "attacker"
        self.github.events[4][0]["event"]["actor"] = {"role": "Coordinator", "id": "owner"}
        result = self.worker.run_once()
        self.assertEqual(len(result["rejected"]), 1)
        self.assertFalse(self.paseo.sent)

    def test_repeated_poll_and_restart_never_repeat_queued_send(self):
        self.worker.run_once()
        self.worker.run_once()
        restarted = self.create()
        result = restarted.run_once()
        self.assertEqual(len(self.paseo.sent), 1)
        self.assertEqual(result["awaiting_ack"], 1)
        self.assertEqual(restarted.status(4, "notify-1")["status"], "queued")

    def test_interrupted_sending_intent_is_not_repeated(self):
        self.paseo.agent["Status"] = "running"
        self.worker.run_once()
        self.worker.db.execute("UPDATE notifications SET status='sending'")
        self.paseo.agent["Status"] = "idle"
        result = self.create().run_once()
        self.assertFalse(self.paseo.sent)
        self.assertEqual(result["awaiting_ack"], 1)

    def test_only_matching_ack_closes_notification(self):
        self.worker.run_once()
        self.github.events[4].append(ack())
        result = self.worker.run_once()
        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(self.worker.status(4, "notify-1")["status"], "acknowledged")
        self.assertEqual(self.worker.run_once()["acknowledged"], 0)
        self.assertEqual(len(self.paseo.sent), 1)

    def test_sent_receipt_is_not_consumption_ack(self):
        self.paseo.delivery = "sent"
        result = self.worker.run_once()
        self.assertEqual(result["acknowledged"], 0)
        self.assertEqual(self.worker.status(4, "notify-1")["status"], "queued")

    def test_ack_with_wrong_author_binding_target_or_old_comment_is_rejected(self):
        self.worker.run_once()
        for field in ("author", "binding", "target", "comment_id"):
            item = ack()
            if field == "author": item["author"] = "attacker"
            if field == "binding": item["event"]["binding"]["head_sha"] = "b"*40
            if field == "target": item["event"]["payload"]["entry_agent_id"] = "other-agent"
            if field == "comment_id": item["comment_id"] = 9
            self.github.events[4] = [notification(), item]
            with self.subTest(field=field):
                self.assertEqual(len(self.worker.run_once()["rejected"]), 1)
                self.assertEqual(self.worker.status(4, "notify-1")["status"], "queued")

    def test_malformed_notification_is_rejected(self):
        mutations = [("schema_version", True), ("repository", "owner/other"), ("binding", []), ("event_id", "bad id")]
        for key, value in mutations:
            item = notification()
            item["event"][key] = value
            self.github.events[4] = [item]
            with self.subTest(key=key):
                self.assertTrue(self.worker.run_once()["rejected"])
        self.assertFalse(self.paseo.sent)

    def test_target_agent_is_enforced(self):
        self.github.events[4][0]["event"]["payload"]["entry_agent_id"] = "other-agent"
        self.assertTrue(self.worker.run_once()["rejected"])
        self.assertFalse(self.paseo.sent)

    def test_only_configured_issue_is_polled(self):
        self.github.events[5] = [notification("outside-issue", comment_id=12)]
        self.worker.run_once()
        self.assertEqual(self.github.read_issues, [4])
        self.assertIsNone(self.worker.status(5, "outside-issue"))

    def test_uncertain_send_is_persistent_and_never_retried(self):
        self.paseo.error = AdapterError("PASEO_TIMEOUT", "timeout", uncertain=True)
        result = self.worker.run_once()
        self.assertEqual(result["errors"][0]["code"], "PASEO_TIMEOUT")
        self.assertEqual(self.worker.status(4, "notify-1")["status"], "uncertain")
        self.paseo.error = None
        self.create().run_once()
        self.assertEqual(len(self.paseo.sent), 1)
        self.github.events[4].append(ack())
        self.assertEqual(self.worker.run_once()["acknowledged"], 1)

    def test_definitely_unsent_failure_is_classified_without_looping(self):
        self.paseo.error = AdapterError("PASEO_UNAVAILABLE", "missing binary")
        self.worker.run_once()
        self.assertEqual(self.worker.status(4, "notify-1")["status"], "failed_unsent")
        self.worker.run_once()
        self.assertEqual(len(self.paseo.sent), 1)

    def test_event_id_conflict_does_not_replace_existing_snapshot(self):
        self.worker.run_once()
        self.github.events[4][0]["event"]["payload"]["summary"] = "different result"
        self.assertTrue(self.worker.run_once()["rejected"])
        self.assertEqual(len(self.paseo.sent), 1)

    def test_conflicting_pending_event_is_not_dispatched_after_agent_becomes_idle(self):
        self.paseo.agent["Status"] = "running"
        self.worker.run_once()
        self.github.events[4][0]["event"]["payload"]["summary"] = "changed content"
        self.paseo.agent["Status"] = "idle"
        self.assertTrue(self.worker.run_once()["rejected"])
        self.assertFalse(self.paseo.sent)
        self.assertEqual(self.worker.status(4, "notify-1")["status"], "conflict")

    def test_state_database_is_pinned_to_the_entry_identity(self):
        for key, value in (("entry_agent_id", "another-entry"), ("repository", "owner/another"), ("controller_login", "another-owner")):
            with self.subTest(key=key), self.assertRaises(InvalidNotification):
                Notifier({**self.config, key: value}, self.path, github=self.github, paseo=self.paseo)

    def test_prompt_preserves_scope_and_explicit_ack_command(self):
        self.worker.run_once()
        prompt = self.paseo.sent[0][1]
        self.assertIn("不是新的用户需求或批准", prompt)
        self.assertIn("notification_event_id：notify-1", prompt)
        self.assertIn("python entry.py ack --notification-event-id notify-1", prompt)
        self.assertIn("不要重复启动实施", prompt)

    def test_empty_scope_is_safe_and_bad_scope_is_rejected(self):
        self.config["task_issues"] = []
        worker = self.create()
        self.assertEqual(worker.run_once()["issues"], [])
        self.assertFalse(self.github.read_issues)
        for issues in ([True], [0], [4, 4], "all"):
            with self.subTest(issues=issues), self.assertRaises(InvalidNotification):
                validate_config({**self.config, "task_issues": issues})

    @unittest.skipIf(os.name == "nt", "Windows termination semantics are verified in its live notifier task")
    def test_watch_exits_cleanly_on_sigterm_without_waiting_for_interval(self):
        config_path = Path(self.folder.name) / "empty-scope.json"
        config_path.write_text(json.dumps({**self.config, "task_issues": []}), encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "notifier.py"
        process = subprocess.Popen([sys.executable, "-B", str(script), "--config", str(config_path),
                                    "--state", str(Path(self.folder.name) / "watch.sqlite"), "--watch", "--interval", "60"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            first = process.stdout.readline()
            self.assertEqual(json.loads(first)["status"], "ok")
            process.terminate()
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
