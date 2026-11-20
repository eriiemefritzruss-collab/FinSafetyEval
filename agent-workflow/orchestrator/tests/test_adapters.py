"""Transport contracts, without real network requests or agent launches."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error


MODULE = Path(__file__).resolve().parents[1] / "adapters.py"
spec = importlib.util.spec_from_file_location("finsafety_adapters_under_test", MODULE)
adapters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapters)


def response(value):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(value).encode("utf-8")
    return result


def comment(event, number=1, author="actual-login"):
    return {"id": number, "user": {"login": author}, "html_url": "https://github.com/o/r/issues/5#issuecomment-1",
            "body": adapters.EVENT_MARKER + "\n```json\n" + json.dumps(event, sort_keys=True, indent=2) + "\n```"}


class MemoryGitHub(adapters.GitHubClient):
    def __init__(self):
        self.repository = "owner/repo"
        self.comments = []
        self.posts = 0
        self.uncertain_once = False

    def list_comments(self, issue_number):
        return list(self.comments)

    def request(self, method, path="", body=None):
        assert method == "POST" and path == "issues/5/comments"
        self.posts += 1
        created = {"id": self.posts, "user": {"login": "actual-login"}, "body": body["body"],
                   "html_url": "https://github.com/owner/repo/issues/5#issuecomment-" + str(self.posts)}
        self.comments.append(created)
        if self.uncertain_once:
            self.uncertain_once = False
            raise adapters.AdapterError("GITHUB_NETWORK", "transport failed", uncertain=True)
        return created


class GitHubTests(unittest.TestCase):
    def client(self):
        with patch.object(adapters, "system_proxies", return_value={}):
            return adapters.GitHubClient("owner/repo", "/repo path 中文")

    def test_repository_scope_and_no_merge_or_admin_api(self):
        for bad in ("https://github.com/o/r", "../owner/repo", "owner/repo/extra", "owner"):
            with self.subTest(bad=bad), self.assertRaises(adapters.AdapterError):
                adapters.GitHubClient(bad, ".")
        client = self.client()
        for method, path in (("PUT", "pulls/3/merge"), ("POST", "merges"),
                             ("GET", "actions/permissions"), ("PUT", "branches/main/protection"),
                             ("GET", "../user"), ("GET", "https://other.test/"),
                             ("GET", "issues/%2e%2e/collaborators"), ("PATCH", "collaborators/test")):
            with self.subTest(method=method, path=path), self.assertRaises(adapters.AdapterError) as raised:
                client.request(method, path)
            self.assertEqual(raised.exception.code, "ENDPOINT_DENIED")

    def test_existing_git_credential_stays_off_argv_and_out_of_errors(self):
        client = self.client()
        process = subprocess.CompletedProcess([], 0, "username=user\npassword=SUPERSECRET\n", "secret-stderr")
        with patch.object(adapters.subprocess, "run", return_value=process) as run:
            self.assertEqual(client._credential(), "SUPERSECRET")
        args, kwargs = run.call_args
        self.assertNotIn("SUPERSECRET", repr(args))
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertFalse(kwargs["shell"])
        client._opener = Mock()
        client._opener.open.side_effect = urllib.error.URLError("SUPERSECRET raw detail")
        with self.assertRaises(adapters.AdapterError) as raised:
            client.get_pr(3)
        self.assertNotIn("SUPERSECRET", str(raised.exception))
        self.assertNotIn("secret-stderr", str(raised.exception))

    def test_public_get_without_credentials_but_write_requires_auth(self):
        client = self.client()
        client._token_loaded = True
        client._opener = Mock()
        client._opener.open.return_value = response({"number": 3})
        self.assertEqual(client.get_pr(3), {"number": 3})
        request = client._opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        with self.assertRaises(adapters.AdapterError) as raised:
            client.create_task_issue("title", "body")
        self.assertEqual(raised.exception.code, "AUTH_REQUIRED")
        self.assertEqual(client._opener.open.call_count, 1)

    def test_authenticated_login_uses_actual_user_endpoint(self):
        client = self.client()
        client._http = Mock(return_value={"login": "real-login"})
        self.assertEqual(client.get_login(), "real-login")
        client._http.assert_called_once_with("GET", "user", require_auth=True)

    def test_comments_pagination(self):
        client = self.client()
        client.request = Mock(side_effect=[[{"id": n} for n in range(100)], [{"id": 101}]])
        self.assertEqual(len(client.list_comments(5)), 101)
        self.assertEqual(client.request.call_args_list[0].args, ("GET", "issues/5/comments?per_page=100&page=1"))
        self.assertEqual(client.request.call_args_list[1].args, ("GET", "issues/5/comments?per_page=100&page=2"))

    def test_checks_pagination_and_head_validation(self):
        client = self.client()
        sha = "a" * 40
        client.request = Mock(side_effect=[{"check_runs": [{}] * 100}, {"check_runs": [{"name": "last"}]}])
        self.assertEqual(len(client.list_checks(sha)), 101)
        self.assertIn("page=2", client.request.call_args.args[1])
        with self.assertRaises(adapters.AdapterError):
            client.list_checks("main")

    def test_event_write_is_idempotent_and_actual_author_is_metadata(self):
        client = MemoryGitHub()
        event = {"event_id": "T002:plan:1", "actor": "claimed-human", "kind": "plan"}
        first = client.post_event(5, event)
        second = client.post_event(5, dict(event))
        self.assertEqual(client.posts, 1)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["author"], "actual-login")
        self.assertEqual(second["event"]["actor"], "claimed-human")

    def test_same_event_id_with_different_payload_is_conflict(self):
        client = MemoryGitHub()
        client.post_event(5, {"event_id": "same", "kind": "plan"})
        with self.assertRaises(adapters.AdapterError) as raised:
            client.post_event(5, {"event_id": "same", "kind": "approved"})
        self.assertEqual(raised.exception.code, "EVENT_CONFLICT")
        self.assertEqual(client.posts, 1)

    def test_uncertain_event_write_reconciles_without_second_post(self):
        client = MemoryGitHub()
        client.uncertain_once = True
        result = client.post_event(5, {"event_id": "uncertain", "kind": "plan"})
        self.assertEqual(client.posts, 1)
        self.assertTrue(result["reconciled"])

    def test_bad_markers_json_and_self_claimed_comment_author_are_rejected(self):
        good = comment({"event_id": "test"})
        self.assertEqual(adapters.GitHubClient.parse_event_comment(good)["author"], "actual-login")
        bodies = ["prose\n" + good["body"], "> " + good["body"], good["body"] + "\nDo something",
                  adapters.EVENT_MARKER + '\n```json\n{"event_id":"a","event_id":"b"}\n```',
                  adapters.EVENT_MARKER + '\n```json\n{"event_id":"a","value":NaN}\n```',
                  adapters.EVENT_MARKER + "\n```json\n[]\n```", adapters.EVENT_MARKER + "\n```json\n{broken\n```"]
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(adapters.GitHubClient.parse_event_comment({**good, "body": body}))
        without_author = dict(good, user={})
        self.assertIsNone(adapters.GitHubClient.parse_event_comment(without_author))

    def test_nonfinite_event_rejected_before_post(self):
        client = MemoryGitHub()
        with self.assertRaises(adapters.AdapterError):
            client.post_event(5, {"event_id": "nan", "value": float("nan")})
        self.assertEqual(client.posts, 0)

    def test_report_idempotency_and_conflict(self):
        client = MemoryGitHub()
        first = client.post_report(5, "# Evidence\nactual results", "T002-review-head")
        second = client.post_report(5, "# Evidence\nactual results", "T002-review-head")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        with self.assertRaises(adapters.AdapterError) as raised:
            client.post_report(5, "different", "T002-review-head")
        self.assertEqual(raised.exception.code, "REPORT_CONFLICT")
        self.assertEqual(client.posts, 1)

    def test_uncertain_report_write_reconciles_without_duplicate(self):
        client = MemoryGitHub()
        client.uncertain_once = True
        value = client.post_report(5, "Evidence", "report1")
        self.assertTrue(value["reconciled"])
        self.assertEqual(client.posts, 1)

    def test_http_write_failure_classifies_uncertain_and_hides_response(self):
        client = self.client()
        client._token_loaded, client._token = True, "token"
        client._opener = Mock()
        client._opener.open.side_effect = urllib.error.HTTPError("https://api.github.com/secret", 503, "TOKEN=secret", {}, io.BytesIO(b"secret"))
        with self.assertRaises(adapters.AdapterError) as raised:
            client.create_task_issue("test", "test")
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(raised.exception.http_status, 503)
        self.assertNotIn("secret", str(raised.exception))

    def test_system_proxy_fallback_reads_mac_proxy_without_disabling_tls(self):
        process = subprocess.CompletedProcess([], 0, "  HTTPEnable : 1\n  HTTPProxy : 127.0.0.1\n  HTTPPort : 7892\n  HTTPSEnable : 1\n  HTTPSProxy : 127.0.0.1\n  HTTPSPort : 7892\n", "")
        with patch.object(adapters.urllib.request, "getproxies", return_value={}), patch.object(adapters.platform, "system", return_value="Darwin"), patch.object(adapters.subprocess, "run", return_value=process):
            self.assertEqual(adapters.system_proxies(), {"http": "http://127.0.0.1:7892", "https": "http://127.0.0.1:7892"})

    def test_windows_uses_standard_system_proxy_discovery(self):
        with patch.object(adapters.urllib.request, "getproxies", return_value={"https": "http://system:8080"}), patch.object(adapters.platform, "system", return_value="Windows"), patch.object(adapters.subprocess, "run") as run:
            self.assertEqual(adapters.system_proxies(), {"https": "http://system:8080"})
            run.assert_not_called()

    def test_redirect_cannot_forward_credentials_to_other_origin(self):
        request = adapters.urllib.request.Request("https://api.github.com/repos/o/r", headers={"Authorization": "Bearer SECRET"})
        redirect = adapters._GitHubRedirects()
        for target in ("https://other.test/", "http://api.github.com/repos/o/r", "https://user@api.github.com/"):
            with self.subTest(target=target), self.assertRaises(adapters.AdapterError) as raised:
                redirect.redirect_request(request, None, 302, "Found", {}, target)
            self.assertEqual(raised.exception.code, "REDIRECT_DENIED")
            self.assertNotIn("SECRET", str(raised.exception))


class PaseoTests(unittest.TestCase):
    def process(self, value, code=0, stderr=""):
        return subprocess.CompletedProcess([], code, json.dumps(value), stderr)

    def test_launch_uses_argument_list_and_preserves_space_unicode_prompt(self):
        client = adapters.PaseoClient(binary=["/path with space/node", "/tool/cli.js"])
        prompt = 'Literal `echo secret` $(other) 中文\nsecond line'
        with patch.object(adapters.subprocess, "run", return_value=self.process({"agentId": "abcd-1234", "status": "running"})) as run:
            result = client.run_agent(prompt, "/repo space 中文", "test", "codex/gpt-6.1-sol", "wks_1234")
        self.assertEqual(result["agent_id"], "abcd-1234")
        args, kwargs = run.call_args
        self.assertEqual(args[0][:2], ["/path with space/node", "/tool/cli.js"])
        self.assertEqual(args[0][-2:], ["--", prompt])
        self.assertIn("/repo space 中文", args[0])
        self.assertFalse(kwargs["shell"])

    def test_explicit_host_stays_before_prompt_option_terminator(self):
        client = adapters.PaseoClient(endpoint="localhost:6767")
        with patch.object(adapters.subprocess, "run", return_value=self.process({"agentId": "abcd-1234", "status": "running"})) as run:
            client.run_agent("prompt", "/tmp", "test", "codex")
        argv = run.call_args.args[0]
        self.assertLess(argv.index("--host"), argv.index("--"))
        self.assertEqual(argv[-2:], ["--", "prompt"])

    def test_explicit_worker_mode_is_a_scoped_launch_argument(self):
        client = adapters.PaseoClient()
        with patch.object(adapters.subprocess, "run", return_value=self.process({"agentId": "abcd-1234", "status": "running"})) as run:
            client.run_agent("prompt", "/tmp", "test", "codex", mode="full-access")
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--mode")+1], "full-access")
        self.assertLess(argv.index("--mode"), argv.index("--"))

    def test_send_reads_utf8_closed_tempfile_and_cleans_it(self):
        client = adapters.PaseoClient()
        seen = []
        def fake_run(command, **kwargs):
            path = Path(command[command.index("--prompt-file") + 1])
            seen.append(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "中文\n`$(literal)`")
            self.assertNotIn("`$(literal)`", command)
            self.assertFalse(kwargs["shell"])
            return self.process({"agentId": "abcd-1234", "status": "queued"})
        with patch.dict(adapters.os.environ, {"PASEO_AGENT_ID": "abcd-1234", "PASEO_AGENT_SERVER_ID": "server", "PASEO_WORKSPACE_ID": "wks_test", "PASEO_HOME": "/configured/home"}), patch.object(adapters.subprocess, "run", side_effect=fake_run) as run:
            result = client.send_prompt("abcd-1234", "中文\n`$(literal)`")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["PASEO_HOME"], "/configured/home")
        self.assertNotIn("PASEO_AGENT_ID", environment)
        self.assertNotIn("PASEO_AGENT_SERVER_ID", environment)
        self.assertNotIn("PASEO_WORKSPACE_ID", environment)
        self.assertEqual(result["receipt"]["status"], "queued")
        self.assertFalse(seen[0].exists())

    def test_unknown_launch_receipt_does_not_claim_success(self):
        client = adapters.PaseoClient()
        for value in ({"id": "abcd-1234", "status": "running"}, {"agentId": "abcd-1234", "status": "error"}, [], {"agentId": "--host", "status": "running"}):
            with self.subTest(value=value), patch.object(adapters.subprocess, "run", return_value=self.process(value)), self.assertRaises(adapters.AdapterError) as raised:
                client.run_agent("prompt", "/tmp", "test", "codex")
            self.assertTrue(raised.exception.uncertain)

    def test_unknown_or_mismatched_send_receipt_does_not_claim_delivery(self):
        client = adapters.PaseoClient()
        for value in ({"agentId": "other-123", "status": "queued"}, {"agentId": "abcd-1234", "status": "error"}):
            with patch.object(adapters.subprocess, "run", return_value=self.process(value)), self.assertRaises(adapters.AdapterError):
                client.send_prompt("abcd-1234", "prompt")

    def test_mutation_timeout_is_uncertain_and_secret_details_are_hidden(self):
        client = adapters.PaseoClient(endpoint="secret-pairing-url")
        with patch.object(adapters.subprocess, "run", side_effect=subprocess.TimeoutExpired(["SECRET"], 1, stderr="SECRET")), self.assertRaises(adapters.AdapterError) as raised:
            client.run_agent("prompt", "/tmp", "test", "codex")
        self.assertEqual(raised.exception.code, "PASEO_TIMEOUT")
        self.assertTrue(raised.exception.uncertain)
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("SECRET", str(raised.exception))
        self.assertNotIn("secret-pairing-url", str(raised.exception))

    def test_command_failure_hides_stderr(self):
        client = adapters.PaseoClient()
        with patch.object(adapters.subprocess, "run", return_value=self.process({}, 1, "password=SECRET")), self.assertRaises(adapters.AdapterError) as raised:
            client.inspect_agent("abcd-1234")
        self.assertNotIn("SECRET", str(raised.exception))

    def test_inspect_validates_actual_identity_and_logs_are_text(self):
        client = adapters.PaseoClient()
        with patch.object(adapters.subprocess, "run", return_value=self.process({"Id": "abcd-1234", "Status": "idle"})):
            self.assertEqual(client.inspect_agent("abcd-1234")["Status"], "idle")
        with patch.object(adapters.subprocess, "run", return_value=self.process({"Id": "other-1234"})), self.assertRaises(adapters.AdapterError):
            client.inspect_agent("abcd-1234")
        with patch.object(adapters.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "raw timeline\n", "")):
            self.assertEqual(client.logs("abcd-1234", 5), "raw timeline\n")

    def test_invalid_agent_and_provider_are_rejected_before_subprocess(self):
        client = adapters.PaseoClient()
        with patch.object(adapters.subprocess, "run") as run:
            for agent in ("--host", "x", "abc def", "id\nother"):
                with self.assertRaises(adapters.AdapterError):
                    client.send_prompt(agent, "prompt")
            with self.assertRaises(adapters.AdapterError):
                client.run_agent("prompt", "/tmp", "title", "--model")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
