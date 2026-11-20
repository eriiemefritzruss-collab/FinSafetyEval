from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
import urllib.error
import urllib.request


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dashboard = load("coordinator_dashboard", "dashboard.py")
core = load("coordinator_dashboard_core", "core.py")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="coordinator-dashboard-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        actors = {r: [r.lower()] for r in ("User", "Planner", "Implementer", "Reviewer", "Coordinator")}
        controller = core.Controller(self.root / "events.sqlite3", trusted_actors=actors)
        binding = dict(requirement_revision="sha256:"+"a"*64, plan_version=None, plan_sha=None,
                       plan_blob_oid=None, head_sha=None, base_sha=None, policy_source_sha="1"*40)
        def event(kind, role, payload):
            return dict(schema_version=1, event_id="event-"+kind, task_id="T-DASH", repository="owner/repo", kind=kind,
                        actor={"id": role.lower(), "role": role}, binding=deepcopy(binding), payload=payload,
                        proof={"source": "authenticated-fixture", "url": "https://example.invalid/proof/"+kind})
        controller.ingest(event("TASK_OPENED", "Planner", dict(goal="Live controller fixture", test_mode=True,
                       entry_agent_id="windows-entry", required_checks=["offline-smoke"], required_gates=["Code", "Integration", "Acceptance"])))
        binding.update(plan_version=1, plan_sha="2"*40, plan_blob_oid="3"*40)
        controller.ingest(event("PLAN_PROPOSED", "Planner", {}))
        controller.ingest(event("PLAN_APPROVED", "User", {"decision": "approve"}))
        binding.update(head_sha="4"*40, base_sha="5"*40)
        controller.ingest(event("IMPLEMENTATION_READY", "Implementer", {}))
        controller.ingest(event("REVIEW_COMPLETED", "Reviewer", dict(outcome="PASS", report_url="https://example.invalid/report",
            gates={g:{"status":"N/A" if g=="Deployment" else "PASS", "reason":"fixture evidence"} for g in core.GATES},
            required_checks=[{"name":"offline-smoke","status":"success","head_sha":"4"*40}], blocking_findings=[])))
        self.snapshot = controller.export_snapshot()
        self.metadata = dict(repository="owner/repo", entry_agent_id="windows-entry", controller_pid=42,
                             last_poll="2026-11-20T09:00:00Z", error=None, task_issues={"T-DASH":9},
                             runtime_jobs={}, checks=[], notifications=[], acks=[])
        self.path = self.root / "运行 状态.html"
        # These tests address our loopback server only, never a configured proxy.
        self.urlopen = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def render(self, snapshot=None, metadata=None):
        dashboard.write_dashboard(self.path, self.snapshot if snapshot is None else snapshot, self.metadata if metadata is None else metadata)
        page = self.path.read_text(encoding="utf-8")
        match = re.search(r'<script type="application/json" id="chain-data">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        return page, json.loads(match.group(1))

    def delivery(self, notification_id="notice-1", ack_reference="notice-1"):
        binding = deepcopy(self.snapshot["tasks"][0]["binding"])
        notice = dict(event={"event_id":notification_id,"task_id":"T-DASH","kind":"RESULT_NOTIFICATION","binding":binding,
                            "payload":{"entry_agent_id":"windows-entry","state":"READY_FOR_HUMAN_MERGE"}},
                      comment_id=10, url="https://github.com/owner/repo/issues/9#notice")
        ack = dict(event={"event_id":"ack-1","task_id":"T-DASH","kind":"ENTRY_ACK","binding":binding,
                         "payload":{"entry_agent_id":"windows-entry","notification_event_id":ack_reference}},
                   comment_id=11, url="https://github.com/owner/repo/issues/9#ack")
        self.metadata.update(notifications=[notice],acks=[ack])
        return notice, ack

    def test_file_contains_actual_exported_snapshot_and_has_no_network_runtime(self):
        page, packet = self.render()
        self.assertEqual(packet["snapshot"], self.snapshot)
        self.assertIn("4"*40, page)
        self.assertEqual(packet["view"]["tasks"][0]["state"], "READY_FOR_HUMAN_MERGE")
        for stage in packet["view"]["tasks"][0]["stages"]:
            self.assertIn("4"*40, stage["versions"])
        self.assertNotIn('http-equiv="refresh"', page)
        self.assertNotRegex(page, r"\b(?:fetch|XMLHttpRequest|WebSocket|localStorage)\s*\(")
        self.assertNotRegex(page, r"<iframe|<script[^>]+src=|<link[^>]+href=")
        self.assertIn("T-001 继续保留待审", page)

    def test_ready_state_does_not_invent_original_dialog_ack(self):
        _, packet = self.render()
        ack_stage = next(s for s in packet["view"]["tasks"][0]["stages"] if s["id"] == "ack")
        self.assertEqual(ack_stage["status"], "未确认 ACK")
        self.assertIsNone(packet["view"]["deliveries"]["T-DASH"]["ack_event_id"])
        merge = packet["view"]["tasks"][0]["stages"][-1]
        self.assertEqual(merge["status"], "待用户确认")

    def test_ack_requires_exact_notification_task_entry_and_version(self):
        notice, ack = self.delivery()
        _, packet = self.render()
        self.assertEqual(packet["view"]["deliveries"]["T-DASH"]["ack_event_id"], "ack-1")
        self.assertEqual(packet["view"]["deliveries"]["T-DASH"]["entry_read_by_human"], "not established")
        for change in (lambda a:a["event"]["payload"].update(notification_event_id="other"),
                       lambda a:a["event"].update(task_id="other"),
                       lambda a:a["event"]["payload"].update(entry_agent_id="other"),
                       lambda a:a["event"]["binding"].update(head_sha="9"*40),
                       lambda a:a.update(comment_id=9)):
            altered = deepcopy(ack)
            change(altered)
            self.metadata["acks"] = [altered]
            _, packet = self.render()
            self.assertIsNone(packet["view"]["deliveries"]["T-DASH"]["ack_event_id"])

    def test_old_notification_does_not_satisfy_current_candidate(self):
        notice, ack = self.delivery()
        notice["event"]["binding"] = {**notice["event"]["binding"], "head_sha":"9"*40}
        _, packet = self.render()
        self.assertIsNone(packet["view"]["deliveries"]["T-DASH"]["notification_event_id"])

    def test_missing_runtime_metadata_does_not_claim_delivery(self):
        _, packet = self.render(metadata={})
        self.assertIsNone(packet["view"]["deliveries"]["T-DASH"]["ack_event_id"])
        _, packet = self.render(snapshot={"tasks":[],"jobs":[],"events":[]}, metadata={})
        self.assertEqual(packet["view"]["tasks"], [])

    def test_unstarted_stage_and_job_statuses_are_preserved(self):
        changed = deepcopy(self.snapshot)
        changed["tasks"][0].update(state="PLAN_REVIEW", approved=False, last_review=None)
        changed["tasks"][0]["binding"].update(head_sha=None,base_sha=None)
        changed["jobs"] = [dict(id=i+1, task_id="T-DASH", kind="implement",status=s,payload={"binding":deepcopy(changed["tasks"][0]["binding"])},attempts=1)
                           for i,s in enumerate(("pending","claimed","dispatched","completed","uncertain","superseded"))]
        _, packet = self.render(snapshot=changed)
        self.assertEqual([j["status"] for j in packet["view"]["jobs"]], [j["status"] for j in changed["jobs"]])
        review = next(s for s in packet["view"]["tasks"][0]["stages"] if s["id"]=="review")
        self.assertEqual(review["status"], "未开始")

    def test_rejected_events_remain_visible(self):
        changed = deepcopy(self.snapshot)
        changed["events"].append(dict(event_id="stale",task_id="T-DASH",kind="REVIEW_COMPLETED",accepted=False,reason="stale head",created_at="now"))
        _, packet = self.render(snapshot=changed)
        self.assertIn("已拒绝", packet["view"]["events"][-1]["title"])
        self.assertEqual(packet["view"]["events"][-1]["detail"], "stale head")

    def test_embedded_data_cannot_close_script_or_inject_markup(self):
        changed = deepcopy(self.snapshot)
        hostile = '</script><img src=x onerror="bad()">'
        changed["tasks"][0]["goal"] = hostile
        page, packet = self.render(snapshot=changed)
        self.assertNotIn(hostile, page)
        self.assertEqual(packet["snapshot"]["tasks"][0]["goal"], hostile)

    def server(self):
        self.render()
        server, thread = dashboard.serve_dashboard(self.path)
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(stop)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        return "http://127.0.0.1:"+str(server.server_address[1])

    def test_http_auto_refresh_and_pause_are_navigation_only(self):
        url = self.server()
        with self.urlopen(url+"/") as response:
            page = response.read().decode()
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn('<meta http-equiv="refresh" content="10">', page)
        self.assertIn('href="/?paused=1"', page)
        with self.urlopen(url+"/?paused=1") as response:
            paused = response.read().decode()
        self.assertNotIn('http-equiv="refresh"', paused)
        self.assertIn('href="/"', paused)
        self.assertIn("自动刷新已暂停", paused)

    def test_http_reads_new_snapshot_and_refuses_other_files_paths_queries(self):
        url = self.server()
        changed = deepcopy(self.snapshot)
        changed["tasks"][0]["goal"] = "new snapshot marker"
        self.render(snapshot=changed)
        with self.urlopen(url+"/") as response:
            self.assertIn("new snapshot marker", response.read().decode())
        for path in ("/core.py", "/../core.py", "/%2e%2e/core.py", "/dashboard.template.html", "/?file=core.py"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.urlopen(url+path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_invalid_server_port_and_missing_file_are_rejected(self):
        self.render()
        for port in (True,-1,65536,"0"):
            with self.assertRaises(ValueError):
                dashboard.serve_dashboard(self.path,port)
        with self.assertRaises(FileNotFoundError):
            dashboard.serve_dashboard(self.root/"absent.html")


if __name__ == "__main__":
    unittest.main()
