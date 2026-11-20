"""Bounded GitHub and Paseo transports for the handoff controller.

These adapters do not infer approval, run merges, retry uncertain writes, or
authenticate claims inside an event body. A single controller must serialize
writes: GitHub comments do not provide compare-and-swap or distributed locks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


EVENT_MARKER = "<!-- finsafety-agent-event:v1 -->"
REPORT_MARKER = "<!-- finsafety-agent-report:v1 -->"
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{3,127}\Z")


class AdapterError(RuntimeError):
    """Transport failure with safe, machine-readable classification."""

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 uncertain: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain
        self.http_status = http_status


class _GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        target = urllib.parse.urlsplit(new_url)
        if target.scheme != "https" or target.hostname != "api.github.com" or target.username or target.password:
            raise AdapterError("REDIRECT_DENIED", "GitHub redirect left the trusted API origin")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AdapterError("INVALID_INPUT", f"{label} must be a nonempty string")
    return value


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise AdapterError("INVALID_INPUT", f"{label} must be a positive integer")
    return value


def system_proxies() -> dict[str, str]:
    """Use OS proxy settings without changing the environment or TLS policy."""
    proxies = {k: v for k, v in urllib.request.getproxies().items()
               if k in ("http", "https") and isinstance(v, str)}
    if platform.system() != "Darwin" or ("http" in proxies and "https" in proxies):
        return proxies
    try:
        result = subprocess.run(["scutil", "--proxy"], capture_output=True,
                                text=True, encoding="utf-8", timeout=5,
                                check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        return proxies
    if result.returncode:
        return proxies
    fields = dict(re.findall(r"^\s*(\w+)\s*:\s*([^\r\n]+)$", result.stdout, re.M))
    for protocol, prefix in (("http", "HTTP"), ("https", "HTTPS")):
        if protocol in proxies or fields.get(prefix + "Enable") != "1":
            continue
        host, port = fields.get(prefix + "Proxy", ""), fields.get(prefix + "Port", "")
        if re.fullmatch(r"[A-Za-z0-9.:-]+", host) and port.isdigit() and 0 < int(port) < 65536:
            if ":" in host and not host.startswith("["):
                host = "[" + host + "]"
            proxies[protocol] = f"http://{host}:{port}"
    return proxies


class GitHubClient:
    """Repository-scoped JSON API; credentials remain in process memory only."""

    def __init__(self, repository: str, repo_path: str | Path,
                 proxy: str | dict[str, str] | None = None, *, timeout: float = 20):
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise AdapterError("INVALID_INPUT", "repository must be owner/name")
        self.repository = repository
        self.repo_path = str(repo_path)
        self.timeout = timeout
        self._token_loaded = False
        self._token: str | None = None
        if isinstance(proxy, str):
            parsed = urllib.parse.urlsplit(proxy)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise AdapterError("INVALID_INPUT", "proxy must be an HTTP(S) URL")
            proxies = {"http": proxy, "https": proxy}
        elif proxy is None:
            proxies = system_proxies()
        elif isinstance(proxy, dict):
            proxies = proxy
        else:
            raise AdapterError("INVALID_INPUT", "proxy configuration is invalid")
        # HTTPSHandler's default context verifies certificates and hostnames.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), _GitHubRedirects())

    def _credential(self) -> str | None:
        if not self._token_loaded:
            self._token_loaded = True
            try:
                result = subprocess.run(
                    ["git", "-C", self.repo_path, "credential", "fill"],
                    input="protocol=https\nhost=github.com\n\n", text=True,
                    encoding="utf-8", capture_output=True, timeout=8, check=False,
                    shell=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                if result.returncode == 0:
                    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                    token = values.get("password")
                    if token and "\r" not in token and "\n" not in token:
                        self._token = token
            except (OSError, subprocess.SubprocessError, UnicodeError):
                pass
        return self._token

    @staticmethod
    def _allowed(method: str, path: str) -> bool:
        if not isinstance(path, str) or path.startswith(("/", "http:", "https:")):
            return False
        parsed = urllib.parse.urlsplit(path)
        normalized = urllib.parse.unquote(parsed.path)
        if parsed.scheme or parsed.netloc or parsed.fragment or any(p in (".", "..") for p in normalized.split("/")):
            return False
        if "\\" in normalized:
            return False
        read_paths = (r"", r"issues", r"issues/[1-9]\d*(?:/comments)?", r"pulls",
                      r"pulls/[1-9]\d*(?:/reviews)?", r"commits/[0-9a-f]{40}/(?:check-runs|status)",
                      r"actions/runs", r"actions/runs/[1-9]\d*(?:/jobs)?", r"branches/[A-Za-z0-9_.-]+")
        write_paths = (r"issues", r"pulls", r"issues/[1-9]\d*/comments")
        patch_paths = (r"issues/[1-9]\d*", r"pulls/[1-9]\d*", r"issues/comments/[1-9]\d*")
        allowed = read_paths if method == "GET" else write_paths if method == "POST" else patch_paths if method == "PATCH" else ()
        return any(re.fullmatch(pattern, normalized) for pattern in allowed)

    def _http(self, method: str, api_path: str, body: dict | None = None,
              *, require_auth: bool = False) -> Any:
        token = self._credential()
        write = method != "GET"
        if (write or require_auth) and not token:
            raise AdapterError("AUTH_REQUIRED", "Existing GitHub credentials are required for this operation")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "FinSafetyEval-handoff",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            payload = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            raise AdapterError("INVALID_INPUT", "GitHub request body must contain finite JSON data") from None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request("https://api.github.com/" + api_path,
                                         data=payload, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise AdapterError("GITHUB_HTTP", f"GitHub returned HTTP {status}",
                               retryable=status == 429 or status >= 500,
                               uncertain=write and status >= 500,
                               http_status=status) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise AdapterError("GITHUB_NETWORK", "GitHub transport failed; no automatic write retry was attempted",
                               retryable=not write, uncertain=write) from None
        except (ValueError, UnicodeError):
            raise AdapterError("GITHUB_RESPONSE", "GitHub returned an invalid JSON response",
                               uncertain=write) from None

    def request(self, method: str, path: str = "", body: dict | None = None) -> Any:
        """Call an allowed path relative to this repository; no merge/admin API."""
        method = method.upper() if isinstance(method, str) else ""
        if not self._allowed(method, path):
            raise AdapterError("ENDPOINT_DENIED", "GitHub endpoint or method is outside this adapter's scope")
        if body is not None and not isinstance(body, dict):
            raise AdapterError("INVALID_INPUT", "GitHub request body must be an object")
        return self._http(method, f"repos/{self.repository}" + ("/" + path if path else ""), body)

    def get_login(self) -> str:
        value = self._http("GET", "user", require_auth=True)
        login = value.get("login") if isinstance(value, dict) else None
        if not isinstance(login, str) or not login:
            raise AdapterError("GITHUB_RESPONSE", "Authenticated GitHub login is missing")
        return login

    def get_pr(self, number: int) -> dict:
        return self.request("GET", f"pulls/{_positive(number, 'PR number')}")

    def _pages(self, path: str) -> list:
        results = []
        # Bounded pagination must fail rather than silently omit evidence.
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            value = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(value, list):
                raise AdapterError("GITHUB_RESPONSE", "Expected a paginated GitHub list")
            results.extend(value)
            if len(value) < 100:
                return results
        raise AdapterError("PAGINATION_LIMIT", "GitHub pagination limit reached; evidence was not truncated")

    def list_comments(self, issue_number: int) -> list[dict]:
        return self._pages(f"issues/{_positive(issue_number, 'issue number')}/comments")

    def list_checks(self, head_sha: str) -> list[dict]:
        if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise AdapterError("INVALID_INPUT", "head_sha must be a complete Git SHA")
        results = []
        for page in range(1, 101):
            value = self.request("GET", f"commits/{head_sha}/check-runs?per_page=100&page={page}")
            runs = value.get("check_runs") if isinstance(value, dict) else None
            if not isinstance(runs, list):
                raise AdapterError("GITHUB_RESPONSE", "Expected GitHub check_runs")
            results.extend(runs)
            if len(runs) < 100:
                return results
        raise AdapterError("PAGINATION_LIMIT", "GitHub check pagination limit reached")

    def create_task_issue(self, title: str, body: str) -> dict:
        return self.request("POST", "issues", {"title": _nonempty(title, "title"), "body": _nonempty(body, "body")})

    @staticmethod
    def parse_event_comment(comment: dict) -> dict | None:
        """Parse a whole comment, never a marker hidden in quoted prose."""
        if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
            return None
        match = re.fullmatch(re.escape(EVENT_MARKER) + r"\n```json\n(.*?)\n```\s*", comment["body"], re.S)
        if not match:
            return None
        try:
            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result
            value = json.loads(match.group(1), object_pairs_hook=unique_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        except (ValueError, RecursionError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get("event_id"), str) or not _IDENTIFIER.fullmatch(value["event_id"]):
            return None
        user = comment.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        if type(comment.get("id")) is not int or not isinstance(author, str) or not author:
            return None
        return {"event": value, "comment_id": comment["id"], "author": author,
                "url": comment.get("html_url"), "created_at": comment.get("created_at")}

    def list_events(self, issue_number: int) -> list[dict]:
        return [parsed for comment in self.list_comments(issue_number)
                if (parsed := self.parse_event_comment(comment)) is not None]

    def _find_event(self, issue_number: int, event: dict) -> dict | None:
        matches = [item for item in self.list_events(issue_number)
                   if item["event"]["event_id"] == event["event_id"]]
        for item in matches:
            if item["event"] != event:
                raise AdapterError("EVENT_CONFLICT", "Existing event_id has a different payload")
        return matches[0] if matches else None

    def post_event(self, issue_number: int, event: dict) -> dict:
        _positive(issue_number, "issue number")
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str) or not _IDENTIFIER.fullmatch(event["event_id"]):
            raise AdapterError("INVALID_INPUT", "event must contain a valid event_id")
        try:
            serialized = json.dumps(event, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise AdapterError("INVALID_INPUT", "event must contain finite JSON data") from None
        existing = self._find_event(issue_number, event)
        if existing:
            return {**existing, "created": False}
        body = EVENT_MARKER + "\n```json\n" + serialized + "\n```"
        try:
            posted = self.request("POST", f"issues/{issue_number}/comments", {"body": body})
        except AdapterError as error:
            if error.uncertain:
                # A timeout may follow a successful server write. Reconcile,
                # but never blindly repeat POST after an uncertain receipt.
                try:
                    existing = self._find_event(issue_number, event)
                except AdapterError:
                    existing = None
                if existing:
                    return {**existing, "created": False, "reconciled": True}
            raise
        parsed = self.parse_event_comment(posted)
        if parsed is None or parsed["event"] != event:
            raise AdapterError("GITHUB_RESPONSE", "Event write receipt could not be verified", uncertain=True)
        return {**parsed, "created": True}

    def post_report(self, issue_number: int, markdown: str, report_id: str) -> dict:
        _positive(issue_number, "issue number")
        _nonempty(markdown, "report")
        if not isinstance(report_id, str) or not _IDENTIFIER.fullmatch(report_id):
            raise AdapterError("INVALID_INPUT", "report_id is invalid")
        prefix = REPORT_MARKER + "\n<!-- report_id:" + report_id + " -->\n"
        body = prefix + markdown
        def find():
            matches = [c for c in self.list_comments(issue_number)
                       if isinstance(c, dict) and isinstance(c.get("body"), str) and c["body"].startswith(prefix)]
            if any(c["body"] != body for c in matches):
                raise AdapterError("REPORT_CONFLICT", "Existing report_id has different report content")
            return matches[0] if matches else None
        existing = find()
        if existing:
            return {"comment_id": existing["id"], "url": existing.get("html_url"), "created": False}
        try:
            posted = self.request("POST", f"issues/{issue_number}/comments", {"body": body})
        except AdapterError as error:
            if error.uncertain:
                try:
                    existing = find()
                except AdapterError:
                    existing = None
                if existing:
                    return {"comment_id": existing["id"], "url": existing.get("html_url"), "created": False, "reconciled": True}
            raise
        if not isinstance(posted, dict) or posted.get("body") != body or type(posted.get("id")) is not int:
            raise AdapterError("GITHUB_RESPONSE", "Report write receipt could not be verified", uncertain=True)
        return {"comment_id": posted["id"], "url": posted.get("html_url"), "created": True}


class PaseoClient:
    """Bounded local/explicit-host CLI calls, without a shell or merge API."""

    def __init__(self, binary: str | Path | list[str] = "paseo", endpoint: str | None = None,
                 *, timeout: float = 45):
        prefix = [str(binary)] if isinstance(binary, (str, Path)) else binary
        if not isinstance(prefix, (list, tuple)) or not prefix:
            raise AdapterError("INVALID_INPUT", "Paseo binary must be a path or command prefix")
        self.binary = [_nonempty(item, "Paseo command component") for item in prefix]
        self.endpoint = _nonempty(endpoint, "endpoint") if endpoint is not None else None
        self.timeout = timeout

    @staticmethod
    def _agent_id(agent_id: Any) -> str:
        if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
            raise AdapterError("INVALID_INPUT", "agent_id is invalid")
        return agent_id

    def _command(self, args: list[str], *, parse_json: bool = True, mutation: bool = False) -> Any:
        command_args = list(args)
        if self.endpoint:
            # Launch prompts follow an option terminator; host flags must stay
            # before it or Commander would treat them as prompt arguments.
            index = command_args.index("--") if "--" in command_args else len(command_args)
            command_args[index:index] = ["--host", self.endpoint]
        command = self.binary + command_args
        try:
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("PASEO_") or key == "PASEO_HOME"}
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=self.timeout, check=False, shell=False,
                                    env=environment)
        except subprocess.TimeoutExpired:
            raise AdapterError("PASEO_TIMEOUT", "Paseo command timed out; inspect existing jobs before retry",
                               retryable=not mutation, uncertain=mutation) from None
        except OSError:
            raise AdapterError("PASEO_UNAVAILABLE", "Paseo executable could not be started") from None
        if result.returncode:
            raise AdapterError("PASEO_COMMAND", "Paseo command failed; raw stderr was withheld",
                               uncertain=mutation)
        if not parse_json:
            return result.stdout
        try:
            value = json.loads(result.stdout)
        except (ValueError, RecursionError):
            raise AdapterError("PASEO_RESPONSE", "Paseo returned an invalid JSON receipt", uncertain=mutation) from None
        if not isinstance(value, dict):
            raise AdapterError("PASEO_RESPONSE", "Paseo receipt must be an object", uncertain=mutation)
        return value

    def run_agent(self, prompt: str, cwd: str | Path, title: str, provider: str,
                  workspace_id: str | None = None, mode: str | None = None) -> dict:
        _nonempty(prompt, "prompt")
        _nonempty(str(cwd), "cwd")
        _nonempty(title, "title")
        if len(title) > 60:
            raise AdapterError("INVALID_INPUT", "title must be at most 60 characters")
        if not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?", provider):
            raise AdapterError("INVALID_INPUT", "provider/model is invalid")
        args = ["run", "--no-wait", "--json", "--provider", provider,
                "--cwd", str(cwd), "--title", title]
        if workspace_id is not None:
            if not isinstance(workspace_id, str) or not _AGENT_ID.fullmatch(workspace_id):
                raise AdapterError("INVALID_INPUT", "workspace_id is invalid")
            args += ["--workspace", workspace_id]
        if mode is not None:
            if not isinstance(mode, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", mode):
                raise AdapterError("INVALID_INPUT", "mode is invalid")
            args += ["--mode", mode]
        args += ["--", prompt]
        receipt = self._command(args, mutation=True)
        agent_id = receipt.get("agentId")
        if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id) or receipt.get("status") not in ("created", "running", "initializing"):
            raise AdapterError("PASEO_RESPONSE", "Paseo launch receipt could not be verified", uncertain=True)
        return {"agent_id": agent_id, "receipt": receipt}

    def send_prompt(self, agent_id: str, prompt: str) -> dict:
        self._agent_id(agent_id)
        _nonempty(prompt, "prompt")
        # A closed file is reopenable by the CLI on Windows as well as macOS.
        with tempfile.TemporaryDirectory(prefix="finsafety-prompt-") as folder:
            path = Path(folder) / "prompt.txt"
            path.write_text(prompt, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            receipt = self._command(["send", agent_id, "--prompt-file", str(path),
                                     "--behavior", "steer", "--no-wait", "--json"], mutation=True)
        if receipt.get("agentId") != agent_id or receipt.get("status") not in ("queued", "sent", "delivered"):
            raise AdapterError("PASEO_RESPONSE", "Paseo send receipt could not be verified", uncertain=True)
        return {"agent_id": agent_id, "receipt": receipt}

    def inspect_agent(self, agent_id: str) -> dict:
        self._agent_id(agent_id)
        value = self._command(["agent", "inspect", agent_id, "--json"])
        if value.get("Id", value.get("id")) != agent_id:
            raise AdapterError("PASEO_RESPONSE", "Paseo inspect receipt identifies another agent")
        return value

    def logs(self, agent_id: str, tail: int = 30) -> str:
        self._agent_id(agent_id)
        if type(tail) is not int or not 1 <= tail <= 200:
            raise AdapterError("INVALID_INPUT", "tail must be between 1 and 200")
        return self._command(["agent", "logs", agent_id, "--tail", str(tail)], parse_json=False)
