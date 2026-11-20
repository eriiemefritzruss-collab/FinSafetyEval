#!/usr/bin/env python3
"""Check a manually supplied handoff against immutable local Git objects.

All JSON inputs must come from a trusted human/controller. This advisory tool
cannot authenticate approvers, obtain fresh GitHub state, verify that tests ran,
or grant merge permission. It never fetches, checks out, writes, or merges code.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath


BINDINGS = (
    "task_id", "repository", "requirement_revision", "plan_version",
    "approved_plan_sha", "plan_path", "plan_blob_oid", "code_base_sha",
    "policy_version", "policy_source_sha",
)
GATES = ("Code", "Integration", "Deployment", "Acceptance")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REVISION = re.compile(r"sha256:[0-9a-f]{64}\Z")
ROUTES = {
    "CODE_CHANGES_REQUIRED": "Implementer",
    "PLAN_REVISION_REQUIRED": "Planner",
    "ENV_BLOCKED": "Environment owner",
    "NEEDS_HUMAN": "User",
}
CHECK_STATUSES = {
    "success", "failure", "pending", "cancelled", "timed_out", "skipped",
    "neutral", "action_required", "startup_failure", "stale",
}


class InvalidEvidence(ValueError):
    """Malformed, incomplete, or internally inconsistent evidence."""


def result(state, reasons):
    return {
        "state": state,
        "next_owner": (
            "User" if state == "READY_FOR_HUMAN_MERGE" else
            "Reviewer" if state == "REVIEWING" else ROUTES[state]
        ),
        "reasons": reasons,
        "advisory": True,
        "merge_authorized": False,
        "note": "Human confirmation is required before merge. Input identity, "
                "freshness and CI authenticity are outside this local check.",
    }


def object_value(value, label):
    if not isinstance(value, dict):
        raise InvalidEvidence(f"{label} must be a JSON object")
    return value


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InvalidEvidence(f"{label} must be a nonempty string")


def full_sha(value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise InvalidEvidence(f"{label} must be a full lowercase 40-character Git SHA")


def bindings(value, label):
    object_value(value, label)
    for key in BINDINGS:
        if key not in value:
            raise InvalidEvidence(f"{label}.{key} is required")
    for key in ("task_id", "repository", "plan_path", "policy_version"):
        nonempty(value[key], f"{label}.{key}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]):
        raise InvalidEvidence(f"{label}.repository must be owner/name")
    if (type(value["plan_version"]) is not int or value["plan_version"] < 1):
        raise InvalidEvidence(f"{label}.plan_version must be a positive integer")
    revision = value["requirement_revision"]
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise InvalidEvidence(f"{label}.requirement_revision must be sha256:<64 lowercase hex>")
    for key in ("approved_plan_sha", "plan_blob_oid", "code_base_sha", "policy_source_sha"):
        full_sha(value[key], f"{label}.{key}")
    path = value["plan_path"]
    if (PurePosixPath(path).is_absolute() or "\\" in path or ":" in path
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise InvalidEvidence(f"{label}.plan_path must be a normalized relative POSIX path")


def name_list(value, label, allowed=None):
    if not isinstance(value, list) or not value:
        raise InvalidEvidence(f"{label} must be a nonempty list")
    for item in value:
        nonempty(item, label)
        if allowed is not None and item not in allowed:
            raise InvalidEvidence(f"{label} has unknown value: {item}")
    if len(set(value)) != len(value):
        raise InvalidEvidence(f"{label} must not contain duplicate names")


def validate(approval, review, current):
    for value, label in ((approval, "approval"), (review, "review"), (current, "current")):
        bindings(value, label)
    nonempty(approval.get("approved_by"), "approval.approved_by")
    name_list(approval.get("required_gates"), "approval.required_gates", GATES)
    if "Code" not in approval["required_gates"]:
        raise InvalidEvidence("approval.required_gates must include Code")
    name_list(approval.get("required_checks"), "approval.required_checks")
    if type(approval.get("require_integration_commit", False)) is not bool:
        raise InvalidEvidence("approval.require_integration_commit must be a boolean")
    for value, label in ((review, "review"), (current, "current")):
        for key in ("pr_head_sha", "tested_base_sha"):
            full_sha(value.get(key), f"{label}.{key}")
    if "tested_integration_sha" not in review:
        raise InvalidEvidence("review.tested_integration_sha is required (null if not tested)")
    if review["tested_integration_sha"] is not None:
        full_sha(review["tested_integration_sha"], "review.tested_integration_sha")
    if review.get("overall") not in ("PASS", *ROUTES):
        raise InvalidEvidence("review.overall is not a supported review outcome")
    gates = object_value(review.get("gates"), "review.gates")
    if set(gates) != set(GATES):
        raise InvalidEvidence("review.gates must contain exactly Code/Integration/Deployment/Acceptance")
    for name in GATES:
        gate = object_value(gates[name], f"review.gates.{name}")
        if gate.get("status") not in ("PASS", "FAIL", "BLOCKED", "N/A"):
            raise InvalidEvidence(f"review.gates.{name}.status is invalid")
        nonempty(gate.get("reason"), f"review.gates.{name}.reason")
    checks = review.get("required_checks")
    if not isinstance(checks, list):
        raise InvalidEvidence("review.required_checks must be a list")
    names = []
    for index, check in enumerate(checks):
        label = f"review.required_checks[{index}]"
        object_value(check, label)
        nonempty(check.get("name"), f"{label}.name")
        if not isinstance(check.get("status"), str) or check["status"] not in CHECK_STATUSES:
            raise InvalidEvidence(f"{label}.status is invalid")
        full_sha(check.get("head_sha"), f"{label}.head_sha")
        names.append(check["name"])
    if len(set(names)) != len(names):
        raise InvalidEvidence("review.required_checks contains duplicate names")
    findings = review.get("blocking_findings")
    if not isinstance(findings, list):
        raise InvalidEvidence("review.blocking_findings must be a list")
    for finding in findings:
        nonempty(finding, "review.blocking_findings item")


def git(repo, *args):
    process = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=15,
    )
    return process.returncode, process.stdout.strip()


def require_object(repo, sha, kind, label):
    code, actual_kind = git(repo, "cat-file", "-t", sha)
    if code or actual_kind != kind:
        raise InvalidEvidence(f"{label} is not an available Git {kind}: {sha}")


def ancestor(repo, older, newer):
    code, _ = git(repo, "merge-base", "--is-ancestor", older, newer)
    return code == 0


def evaluate(repo, approval, review, current):
    """Return an advisory routing result; never mutate the repository."""
    try:
        validate(approval, review, current)
        mismatched = [key for key in BINDINGS if approval[key] != current[key]]
        if mismatched:
            return result("NEEDS_HUMAN", ["Current task differs from approved snapshot: " + ", ".join(mismatched)])
        mismatched = [key for key in (*BINDINGS, "pr_head_sha", "tested_base_sha")
                      if review[key] != current[key]]
        if mismatched:
            return result("REVIEWING", ["Review evidence is stale: " + ", ".join(mismatched)])
        code, inside = git(repo, "rev-parse", "--is-inside-work-tree")
        if code or inside != "true":
            return result("ENV_BLOCKED", ["--repo is not an available Git working tree"])
        for key in ("approved_plan_sha", "code_base_sha", "pr_head_sha", "tested_base_sha", "policy_source_sha"):
            require_object(repo, current[key], "commit", key)
        require_object(repo, current["plan_blob_oid"], "blob", "plan_blob_oid")
        for key in ("approved_plan_sha", "tested_base_sha", "pr_head_sha"):
            code, blob = git(repo, "rev-parse", f"{current[key]}:{current['plan_path']}")
            if code or blob != current["plan_blob_oid"]:
                raise InvalidEvidence(f"Plan path/blob at {key} differs from the approved plan")
        if not ancestor(repo, current["approved_plan_sha"], current["tested_base_sha"]):
            raise InvalidEvidence("Approved plan commit is not an ancestor of the current target base")
        if not ancestor(repo, current["approved_plan_sha"], current["pr_head_sha"]):
            raise InvalidEvidence("Candidate does not contain the approved plan commit")
        if not ancestor(repo, current["approved_plan_sha"], current["code_base_sha"]):
            raise InvalidEvidence("Implementation start does not contain the approved plan commit")
        if not ancestor(repo, current["code_base_sha"], current["tested_base_sha"]):
            raise InvalidEvidence("Implementation start is not in the current target base history")
        if not ancestor(repo, current["code_base_sha"], current["pr_head_sha"]):
            raise InvalidEvidence("code_base_sha is not an ancestor of the candidate")
        integration = review["tested_integration_sha"]
        if integration is not None:
            require_object(repo, integration, "commit", "tested_integration_sha")
            if not all(ancestor(repo, current[key], integration)
                       for key in ("pr_head_sha", "tested_base_sha")):
                raise InvalidEvidence("Integration commit does not contain both candidate and target base")
        if review["overall"] != "PASS":
            return result(review["overall"], ["Reviewer routed task: " + review["overall"]])
        if approval.get("require_integration_commit", False) and integration is None:
            return result("ENV_BLOCKED", ["Approved policy requires evidence from an integration commit"])
        if review["blocking_findings"]:
            return result("CODE_CHANGES_REQUIRED", ["Unresolved blocking findings: " + ", ".join(review["blocking_findings"])])
        failed = [name for name in GATES if review["gates"][name]["status"] == "FAIL"]
        if failed:
            return result("CODE_CHANGES_REQUIRED", ["Failed gates: " + ", ".join(failed)])
        blocked = [name for name in GATES if review["gates"][name]["status"] == "BLOCKED"]
        if blocked:
            return result("ENV_BLOCKED", ["Blocked gates: " + ", ".join(blocked)])
        missing = [name for name in approval["required_gates"]
                   if review["gates"][name]["status"] != "PASS"]
        if missing:
            return result("ENV_BLOCKED", ["Required gates are not PASS: " + ", ".join(missing)])
        checks = {check["name"]: check for check in review["required_checks"]}
        missing = [name for name in approval["required_checks"] if name not in checks]
        if missing:
            return result("ENV_BLOCKED", ["Missing required CI checks: " + ", ".join(missing)])
        stale = [name for name, check in checks.items() if check["head_sha"] != current["pr_head_sha"]]
        if stale:
            return result("REVIEWING", ["CI evidence belongs to another candidate: " + ", ".join(stale)])
        failing = [name for name, check in checks.items() if check["status"] in ("failure", "startup_failure")]
        if failing:
            return result("CODE_CHANGES_REQUIRED", ["Failed CI checks: " + ", ".join(failing)])
        incomplete = [name for name, check in checks.items() if check["status"] != "success"]
        if incomplete:
            return result("ENV_BLOCKED", ["CI checks are not successful: " + ", ".join(incomplete)])
        return result("READY_FOR_HUMAN_MERGE", ["Supplied snapshots, local Git objects, gates and CI records agree"])
    except (InvalidEvidence, OSError, subprocess.SubprocessError) as error:
        return result("NEEDS_HUMAN", [str(error)])


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    for name in ("approval", "review", "current"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = evaluate(args.repo, load_json(args.approval), load_json(args.review), load_json(args.current))
    except (OSError, ValueError) as error:
        output = result("NEEDS_HUMAN", ["Cannot read input JSON: " + str(error)])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["state"] == "READY_FOR_HUMAN_MERGE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
