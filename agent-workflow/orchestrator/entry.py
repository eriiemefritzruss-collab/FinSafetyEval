"""Fixed Planner entry point. Writes versioned events; never merges a PR.

The --explicit-user-approval flag records an explicit instruction received by
the trusted entry operator. It is not proof of a separate GitHub identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

try:
    from .adapters import AdapterError, GitHubClient, PaseoClient
except ImportError:  # python /absolute/path/entry.py ... on Windows
    from adapters import AdapterError, GitHubClient, PaseoClient


TASK_MARKER = "<!-- finsafety-orchestration-task:v1 -->"
BINDINGS = ("requirement_revision", "plan_version", "plan_sha", "plan_blob_oid",
            "head_sha", "base_sha", "policy_source_sha")
SHA = re.compile(r"[0-9a-f]{40}\Z")
TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")


def error(code, message):
    raise AdapterError(code, message)


def git(config, *args):
    try:
        result = subprocess.run(["git", "-C", config["repo_path"], *args],
                                capture_output=True, timeout=12, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        error("GIT_UNAVAILABLE", "Git could not read the configured repository")
    if result.returncode:
        error("GIT_OBJECT_MISSING", "Required Git object/ref is unavailable; fetch the exact task refs first")
    return result.stdout


def commit(config, value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        error("INVALID_INPUT", label + " must be a complete lowercase Git commit SHA")
    if git(config, "cat-file", "-t", value).strip() != b"commit":
        error("GIT_OBJECT_TYPE", label + " must identify a Git commit")
    return value


def load_config(path):
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        error("CONFIG_ERROR", "Cannot read the entry JSON configuration")
    if not isinstance(config, dict):
        error("CONFIG_ERROR", "Entry configuration must be an object")
    for key in ("repository", "repo_path", "entry_agent_id", "controller_login", "policy_source_sha"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            error("CONFIG_ERROR", "Missing entry configuration field: " + key)
    for key in ("required_checks", "required_gates"):
        value = config.get(key)
        if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value) or len(set(value)) != len(value):
            error("CONFIG_ERROR", key + " must be a nonempty unique list")
    if "Code" not in config["required_gates"] or set(config["required_gates"]) - {"Code", "Integration", "Deployment", "Acceptance"}:
        error("CONFIG_ERROR", "required_gates must contain Code and use the four defined gate names")
    allowed = config.get("allowed_test_tasks", [])
    if not isinstance(allowed, list) or any(not isinstance(x, str) or not TASK.fullmatch(x) for x in allowed):
        error("CONFIG_ERROR", "allowed_test_tasks must be an explicit task identifier list")
    mappings = config.get("task_issues", {})
    if not isinstance(mappings, dict) or any(not isinstance(k, str) or not TASK.fullmatch(k) or type(v) is not int or v < 1 for k, v in mappings.items()):
        error("CONFIG_ERROR", "task_issues must map task IDs to positive issue numbers")
    config["repo_path"] = str(Path(config["repo_path"]).resolve())
    config["_config_dir"] = str(Path(path).resolve().parent)
    commit(config, config["policy_source_sha"], "policy_source_sha")
    return config


def binding(config, revision):
    return {"requirement_revision": revision, "plan_version": None, "plan_sha": None,
            "plan_blob_oid": None, "head_sha": None, "base_sha": None,
            "policy_source_sha": config["policy_source_sha"]}


def validate_binding(value):
    if not isinstance(value, dict) or set(value) != set(BINDINGS):
        error("INVALID_EVENT", "Event binding must contain the seven protocol fields")
    if not isinstance(value["requirement_revision"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["requirement_revision"]):
        error("INVALID_EVENT", "Event requirement revision is invalid")
    for key in ("plan_sha", "plan_blob_oid", "head_sha", "base_sha", "policy_source_sha"):
        if value[key] is not None and (not isinstance(value[key], str) or not SHA.fullmatch(value[key])):
            error("INVALID_EVENT", "Event binding contains an invalid SHA")
    if value["policy_source_sha"] is None or (value["plan_version"] is not None and (type(value["plan_version"]) is not int or value["plan_version"] < 1)):
        error("INVALID_EVENT", "Event binding has an invalid policy or plan version")
    return dict(value)


class Entry:
    def __init__(self, config, github=None):
        self.config = config
        self.github = github or GitHubClient(config["repository"], config["repo_path"], proxy=config.get("proxy"))
        self.login = self.github.get_login()
        if self.login != config.get("entry_login", config["controller_login"]):
            error("ACTOR_MISMATCH", "Authenticated GitHub account does not match the configured entry operator")

    def _task(self, task_id):
        if not isinstance(task_id, str) or not TASK.fullmatch(task_id):
            error("INVALID_INPUT", "task_id is not a portable task identifier")

    def _events(self, issue, task_id):
        self._task(task_id)
        records = []
        for item in self.github.list_events(issue):
            event = item.get("event", {})
            actor = event.get("actor", {})
            if (item.get("author") == self.login and actor.get("id") == item.get("author")
                    and event.get("schema_version") == 1 and event.get("repository") == self.config["repository"]
                    and event.get("task_id") == task_id):
                records.append(item)
        return records

    def _opened(self, issue, task_id):
        matches = [x["event"] for x in self._events(issue, task_id) if x["event"].get("kind") == "TASK_OPENED"]
        if not matches:
            error("TASK_MISSING", "No authenticated TASK_OPENED event was found for this task")
        opened = matches[-1]
        payload = opened.get("payload", {})
        if payload.get("entry_agent_id") != self.config["entry_agent_id"]:
            error("ENTRY_MISMATCH", "Task belongs to a different entry conversation")
        validate_binding(opened.get("binding"))
        goal = payload.get("goal")
        if not isinstance(goal, str) or "sha256:" + hashlib.sha256(goal.encode("utf-8")).hexdigest() != opened["binding"]["requirement_revision"]:
            error("INVALID_EVENT", "TASK_OPENED goal does not match its frozen revision")
        return opened

    def _event(self, task_id, kind, actor_role, bind, payload, issue):
        validate_binding(bind)
        content = {"kind": kind, "binding": bind, "payload": payload}
        suffix = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:20]
        return {"schema_version": 1, "event_id": task_id + ":" + kind + ":" + suffix,
                "task_id": task_id, "repository": self.config["repository"], "kind": kind,
                "actor": {"role": actor_role, "id": self.login},
                "binding": bind, "payload": payload,
                "proof": {"source": "explicit-entry-command", "url": f"https://github.com/{self.config['repository']}/issues/{issue}"}}

    def _post(self, issue, event):
        receipt = self.github.post_event(issue, event)
        return {"ok": True, "issue": issue, "event": event, "receipt": receipt}

    def health(self):
        repo = self.github.request("GET", "")
        paseo = PaseoClient(self.config.get("paseo_binary", "paseo"), self.config.get("paseo_endpoint"))
        agent = paseo.inspect_agent(self.config["entry_agent_id"])
        return {"ok": True, "repository": repo.get("full_name", self.config["repository"]),
                "authenticated_login": self.login, "entry_agent_id": self.config["entry_agent_id"],
                "entry_status": agent.get("Status", agent.get("status")),
                "policy_source_sha": self.config["policy_source_sha"], "read_only": True}

    def _find_issue(self, task_id):
        mapped = self.config.get("task_issues", {}).get(task_id)
        if mapped:
            value = self.github.request("GET", f"issues/{mapped}")
            if not isinstance(value, dict) or value.get("number") != mapped or "pull_request" in value:
                error("TASK_SCOPE", "Configured task issue could not be verified")
            return value
        needle = TASK_MARKER + "\n<!-- task_id:" + task_id + " -->"
        matches = []
        for page in range(1, 101):
            batch = self.github.request("GET", f"issues?state=all&per_page=100&page={page}")
            if not isinstance(batch, list):
                error("GITHUB_RESPONSE", "Expected a repository issue list")
            matches.extend(x for x in batch if isinstance(x, dict) and "pull_request" not in x
                           and isinstance(x.get("body"), str) and x["body"].startswith(needle + "\n"))
            if len(batch) < 100:
                break
        else:
            error("PAGINATION_LIMIT", "Task discovery was not complete; no issue was created")
        if len(matches) > 1:
            error("TASK_CONFLICT", "More than one issue claims the task ID; reconcile before continuing")
        return matches[0] if matches else None

    def open(self, task_id, goal_bytes, test_mode=False):
        self._task(task_id)
        if type(test_mode) is not bool or (test_mode and task_id not in self.config.get("allowed_test_tasks", [])):
            error("TEST_SCOPE", "Test mode is only available for configured isolated test tasks")
        try:
            goal = goal_bytes.decode("utf-8")
        except (AttributeError, UnicodeError):
            error("INVALID_INPUT", "Goal input must be raw UTF-8 bytes")
        if not goal.strip():
            error("INVALID_INPUT", "Goal cannot be empty")
        issue = self._find_issue(task_id)
        if issue is None:
            body = TASK_MARKER + "\n<!-- task_id:" + task_id + " -->\n\n" + goal
            # If creation times out, do not blindly repeat this POST.
            issue = self.github.create_task_issue(task_id + "：" + goal.splitlines()[0][:100], body)
        number = issue.get("number") if isinstance(issue, dict) else None
        if type(number) is not int or number < 1:
            error("GITHUB_RESPONSE", "Task issue creation did not return a verified issue number")
        revision = "sha256:" + hashlib.sha256(goal_bytes).hexdigest()
        payload = {"goal": goal, "test_mode": test_mode, "entry_agent_id": self.config["entry_agent_id"],
                   "required_checks": self.config["required_checks"], "required_gates": self.config["required_gates"]}
        prior = [x["event"] for x in self._events(number, task_id) if x["event"].get("kind") == "TASK_OPENED"]
        if prior and (prior[-1].get("binding") != binding(self.config, revision) or prior[-1].get("payload") != payload):
            error("TASK_CONFLICT", "This task ID already has a different goal or entry configuration")
        event = self._event(task_id, "TASK_OPENED", "Planner", binding(self.config, revision), payload, number)
        result = self._post(number, event)
        if self.config.get("notifier_config_path"):
            result["notifier_scope"] = self._update_notifier_scope(number)
        return result

    def _update_notifier_scope(self, issue):
        """Atomically add a successful task to the local notification scope.

        The fixed Planner serializes task creation. This is a local atomic file
        replacement, not a distributed/concurrent-writer claiming mechanism.
        """
        path = Path(self.config["notifier_config_path"])
        if not path.is_absolute():
            path = Path(self.config.get("_config_dir", ".")) / path
        temporary = None
        try:
            if path.is_symlink():
                error("NOTIFIER_CONFIG", "Notifier configuration must be a regular owned file")
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            issues = value.get("task_issues") if isinstance(value, dict) else None
            if not isinstance(issues, list) or any(type(x) is not int or x < 1 for x in issues):
                error("NOTIFIER_CONFIG", "Notifier task_issues must be a positive integer list")
            if issue in issues:
                return {"updated": False, "issue": issue}
            value["task_issues"] = [*issues, issue]
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             prefix=".notifier-scope-", suffix=".json", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            return {"updated": True, "issue": issue}
        except AdapterError:
            raise
        except (OSError, ValueError, TypeError):
            error("NOTIFIER_CONFIG", "Task event was saved, but notifier scope could not be updated; fix configuration and rerun open")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def propose(self, issue, task_id, plan_sha, plan_path, plan_version, plan_pr=None, handoff_path=None):
        opened = self._opened(issue, task_id)
        if type(plan_version) is not int or plan_version < 1:
            error("INVALID_INPUT", "plan_version must be a positive integer")
        expected = f"plans/{task_id}/plan-v{plan_version}.md"
        if plan_path != expected:
            error("PLAN_PATH", "plan_path must be " + expected)
        commit(self.config, plan_sha, "plan_sha")
        blob = git(self.config, "rev-parse", plan_sha + ":" + plan_path).decode().strip()
        if not SHA.fullmatch(blob) or git(self.config, "cat-file", "-t", blob).strip() != b"blob":
            error("PLAN_BLOB", "Plan path does not identify a Git blob")
        if plan_pr is not None and (type(plan_pr) is not int or plan_pr < 1):
            error("INVALID_INPUT", "plan_pr must be a positive PR number")
        bind = binding(self.config, opened["binding"]["requirement_revision"])
        bind.update(plan_version=plan_version, plan_sha=plan_sha, plan_blob_oid=blob)
        selected_handoff = handoff_path or f"plans/{task_id}/handoff-v{plan_version}.json"
        self._relative_path(selected_handoff, "handoff_path")
        found = git(self.config, "ls-tree", "--name-only", plan_sha, "--", selected_handoff).decode().strip()
        payload = {"plan_path": plan_path, "plan_pr": plan_pr}
        if found:
            try:
                frozen = json.loads(git(self.config, "show", plan_sha + ":" + selected_handoff).decode("utf-8"))
            except (ValueError, UnicodeError):
                error("HANDOFF_INVALID", "Frozen handoff must be valid UTF-8 JSON")
            self._validate_handoff(frozen)
            payload["handoff_path"] = selected_handoff
        elif handoff_path is not None:
            error("HANDOFF_MISSING", "Explicit handoff_path does not exist in the proposed commit")
        return self._post(issue, self._event(task_id, "PLAN_PROPOSED", "Planner", bind,
                                            payload, issue))

    @staticmethod
    def _relative_path(path, label):
        if (not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
                or "\\" in path or ":" in path or "\x00" in path
                or any(p in ("", ".", "..", ".git") for p in path.split("/"))
                or any(c in path for c in "*?[]{}")):
            error("HANDOFF_PATH", label + " must be an exact normalized repository-relative file path")

    @classmethod
    def _validate_handoff(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("allowed_paths"), list) or not value["allowed_paths"]:
            error("HANDOFF_INVALID", "Frozen handoff needs a nonempty allowed_paths array")
        for path in value["allowed_paths"]:
            cls._relative_path(path, "allowed_paths item")
        if len(set(value["allowed_paths"])) != len(value["allowed_paths"]):
            error("HANDOFF_INVALID", "Frozen allowed_paths must not contain duplicates")
        commands = value.get("validation_commands")
        if not isinstance(commands, list) or not commands:
            error("HANDOFF_INVALID", "Frozen handoff needs validation_commands argument arrays")
        for command in commands:
            if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command):
                error("HANDOFF_INVALID", "Validation commands must be nonempty argument arrays; shell strings are unsupported")
        if "goal" in value and (not isinstance(value["goal"], str) or not value["goal"].strip()):
            error("HANDOFF_INVALID", "Frozen goal must be nonempty text when supplied")

    def approve(self, issue, task_id, explicit_user_approval=False):
        if explicit_user_approval is not True:
            error("APPROVAL_REQUIRED", "An explicit current user approval must be supplied")
        opened = self._opened(issue, task_id)
        proposals = [x["event"] for x in self._events(issue, task_id) if x["event"].get("kind") == "PLAN_PROPOSED"]
        if not proposals:
            error("PLAN_MISSING", "No authenticated plan proposal was found")
        proposal = proposals[-1]
        bind = validate_binding(proposal.get("binding"))
        if bind["policy_source_sha"] != self.config["policy_source_sha"]:
            error("POLICY_CHANGED", "The proposed policy differs from the configured trusted policy")
        path = proposal.get("payload", {}).get("plan_path")
        expected = f"plans/{task_id}/plan-v{bind['plan_version']}.md"
        if path != expected:
            error("PLAN_PATH", "Proposed plan path does not match task/version")
        commit(self.config, bind["plan_sha"], "proposed plan_sha")
        if git(self.config, "rev-parse", bind["plan_sha"] + ":" + path).decode().strip() != bind["plan_blob_oid"]:
            error("PLAN_BLOB", "Proposed blob does not match the actual plan commit")
        test_mode = opened["payload"].get("test_mode") is True
        if test_mode and task_id not in self.config.get("allowed_test_tasks", []):
            error("TEST_SCOPE", "Test approval cannot bypass the configured task allowlist")
        proposed_sha = bind["plan_sha"]
        plan_pr = proposal.get("payload", {}).get("plan_pr")
        if not test_mode:
            if type(plan_pr) is not int or plan_pr < 1:
                error("PLAN_PR_REQUIRED", "Production approval needs an actual merged plan PR")
            pr = self.github.get_pr(plan_pr)
            if pr.get("merged") is not True or pr.get("head", {}).get("sha") != proposed_sha:
                error("PLAN_NOT_MERGED", "Plan PR is unmerged or its head differs from the proposed version")
            base = pr.get("base", {})
            if base.get("ref") != self.config.get("base_branch", "main") or base.get("repo", {}).get("full_name") != self.config["repository"]:
                error("PLAN_BASE", "Plan PR targets a different repository or branch")
            merged_sha = commit(self.config, pr.get("merge_commit_sha"), "actual plan merge commit")
            if git(self.config, "rev-parse", merged_sha + ":" + path).decode().strip() != bind["plan_blob_oid"]:
                error("PLAN_BLOB", "Merged plan blob differs from the proposed frozen plan")
            git(self.config, "merge-base", "--is-ancestor", merged_sha, "origin/" + self.config.get("base_branch", "main"))
            bind["plan_sha"] = merged_sha
        payload = {"decision": "approve", "plan_merged": not test_mode,
                   "proposed_plan_sha": proposed_sha, "plan_pr": plan_pr,
                   "explicit_user_approval": True, "test_mode": test_mode,
                   "authorization": "Current user authorized this isolated collaboration test" if test_mode else "Explicit user instruction recorded by the trusted entry operator"}
        event = self._event(task_id, "PLAN_APPROVED", "User", bind, payload, issue)
        return self._post(issue, event)

    def ack(self, issue, notification_event_id):
        matches = [x for x in self.github.list_events(issue)
                   if x.get("event", {}).get("event_id") == notification_event_id]
        if len(matches) != 1:
            error("NOTIFICATION_MISSING", "Expected exactly one original notification event")
        record, event = matches[0], matches[0]["event"]
        if (record.get("author") != self.config["controller_login"] or event.get("actor", {}).get("id") != record.get("author")
                or event.get("actor", {}).get("role") != "Coordinator" or event.get("kind") != "ENTRY_NOTIFICATION"
                or event.get("repository") != self.config["repository"] or event.get("schema_version") != 1):
            error("NOTIFICATION_SOURCE", "Notification is not from the configured controller account")
        if event.get("payload", {}).get("entry_agent_id") != self.config["entry_agent_id"]:
            error("ENTRY_MISMATCH", "Notification targets a different entry conversation")
        bind = validate_binding(event.get("binding"))
        self._opened(issue, event.get("task_id"))
        payload = {"notification_event_id": notification_event_id, "entry_agent_id": self.config["entry_agent_id"]}
        return self._post(issue, self._event(event["task_id"], "ENTRY_ACK", "Planner", bind, payload, issue))

    def stop(self, issue, task_id, reason="User requested stop"):
        self._opened(issue, task_id)
        records = self._events(issue, task_id)
        bind = validate_binding(records[-1]["event"].get("binding"))
        return self._post(issue, self._event(task_id, "TASK_STOPPED", "User", bind,
                                            {"reason": reason}, issue))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--config")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("health", "open", "propose", "approve", "ack", "stop"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", default=argparse.SUPPRESS)
        if name not in ("health", "ack"):
            sub.add_argument("--task-id", required=True)
        if name in ("propose", "approve", "ack", "stop"):
            sub.add_argument("--issue", type=int, required=True)
        if name == "open":
            goals = sub.add_mutually_exclusive_group(required=True)
            goals.add_argument("--goal-file", type=Path)
            goals.add_argument("--goal")
            sub.add_argument("--test-mode", action="store_true")
        elif name == "propose":
            sub.add_argument("--plan-sha", required=True)
            sub.add_argument("--plan-path", required=True)
            sub.add_argument("--plan-version", type=int, required=True)
            sub.add_argument("--plan-pr", type=int)
            sub.add_argument("--handoff-path")
        elif name == "approve":
            sub.add_argument("--explicit-user-approval", action="store_true")
        elif name == "ack":
            sub.add_argument("--notification-event-id", required=True)
        elif name == "stop":
            sub.add_argument("--reason", default="User requested stop")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if not args.config:
            error("CONFIG_ERROR", "--config is required")
        config = load_config(args.config)
        entry = Entry(config)
        if hasattr(args, "issue") and args.issue < 1:
            error("INVALID_INPUT", "issue must be a positive integer")
        if args.command == "health":
            result = entry.health()
        elif args.command == "open":
            raw = args.goal_file.read_bytes() if args.goal_file else args.goal.encode("utf-8")
            result = entry.open(args.task_id, raw, args.test_mode)
        elif args.command == "propose":
            result = entry.propose(args.issue, args.task_id, args.plan_sha, args.plan_path, args.plan_version, args.plan_pr, args.handoff_path)
        elif args.command == "approve":
            result = entry.approve(args.issue, args.task_id, args.explicit_user_approval)
        elif args.command == "ack":
            result = entry.ack(args.issue, args.notification_event_id)
        else:
            result = entry.stop(args.issue, args.task_id, args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AdapterError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc),
                                                "retryable": exc.retryable, "uncertain": exc.uncertain}}, ensure_ascii=False))
        return 1
    except (OSError, UnicodeError):
        print(json.dumps({"ok": False, "error": {"code": "LOCAL_INPUT", "message": "Cannot read a local entry input"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
