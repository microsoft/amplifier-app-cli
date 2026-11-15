"""Exhaustive pytest partitions with fail-closed collection and result joins."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[1]
SHARDS = ("general", "policy-transfer", "memory-0", "memory-2", "memory-8")
TRANSFER = "tests/test_session_storage_transfer.py"
WITNESS = "tests/test_session_media_policy_witness.py"
HUGE = TRANSFER + "::test_huge_full_registered_snapshot_bounded"
MEMORY = {f"{HUGE}[{size}]": f"memory-{size}" for size in (0, 2, 8)}


class InvalidEvidence(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def seal(value):
    return {**value, "sha256": digest(value)}


def unseal(value):
    require(isinstance(value, dict) and "sha256" in value, "Missing evidence digest")
    payload = {k: v for k, v in value.items() if k != "sha256"}
    require(value["sha256"] == digest(payload), "Evidence digest mismatch")
    return payload


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A cancelled process cannot publish a partial JSON success document.
    fd, temporary = tempfile.mkstemp(prefix=".pytest-evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def identity(root):
    import pytest

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(REPOSITORY), *args], text=True,
        ).strip()

    def file_hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    return {
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "machine": platform.machine(),
        "pytest": pytest.__version__,
        "helper": file_hash(Path(__file__)),
        "configuration": {
            name: file_hash(root / name)
            for name in ("pyproject.toml", "uv.lock", "pytest.ini", "setup.cfg", "tox.ini")
        },
        "pytest_addopts": hashlib.sha256(os.environ.get("PYTEST_ADDOPTS", "").encode()).hexdigest(),
    }


def validate_ids(ids):
    require(isinstance(ids, list) and ids, "Empty or invalid canonical collection")
    require(all(isinstance(n, str) for n in ids), "Invalid node identity")
    require(len(ids) == len(set(ids)), "Duplicate node identity")
    for node in ids:
        path = PurePosixPath(node.split("::", 1)[0])
        require(not path.is_absolute() and ".." not in path.parts
                and path.suffix == ".py" and "\\" not in str(path),
                "Node path must be a relative Python test")


def partition(ids):
    validate_ids(ids)
    require(set(MEMORY) <= set(ids), "Required memory cases missing")
    require(not any(n.startswith(HUGE) and n not in MEMORY for n in ids),
            "Unplanned memory parameter")
    groups = {name: [] for name in SHARDS}
    for node in ids:
        name = MEMORY.get(node)
        if name is None:
            name = "policy-transfer" if node.split("::", 1)[0] in (TRANSFER, WITNESS) else "general"
        groups[name].append(node)
    require(all(groups.values()), "Empty partition")
    flattened = [node for name in SHARDS for node in groups[name]]
    require(Counter(flattened) == Counter(ids), "Partition is not exact canonical coverage")
    return groups


def validate_plan(document):
    plan = unseal(document)
    require(set(plan) == {"schema", "identity", "collection", "partitions"}
            and plan["schema"] == "pytest-shard-plan-v1", "Invalid plan schema")
    require(set(plan["collection"]) == {"nodeids", "excluded", "skipped", "selection"},
            "Invalid canonical collection schema")
    expected = partition(plan["collection"]["nodeids"])
    require(plan["partitions"] == expected, "Missing, extra or overlapping partition")
    return plan


def status_from_phases(phases):
    require(isinstance(phases, list), "Missing phase reports")
    by_phase = {}
    for report in phases:
        require(isinstance(report, dict) and set(report) == {"when", "outcome", "wasxfail"},
                "Malformed phase report")
        when = report["when"]
        require(when in {"setup", "call", "teardown"} and when not in by_phase,
                "Duplicate or invalid phase")
        require(report["outcome"] in {"passed", "failed", "skipped"}
                and type(report["wasxfail"]) is bool, "Invalid phase outcome")
        by_phase[when] = report
    require("setup" in by_phase and "teardown" in by_phase, "Nonterminal item")
    setup = by_phase["setup"]
    require(("call" in by_phase) == (setup["outcome"] == "passed"),
            "Incomplete item lifecycle")
    require([p["when"] for p in phases] == (
        ["setup", "call", "teardown"] if setup["outcome"] == "passed"
        else ["setup", "teardown"]), "Out-of-order lifecycle")
    if any(p["outcome"] == "failed" for p in phases):
        return "failed"
    if any(p["outcome"] == "skipped" for p in phases):
        return "xfailed" if any(p["wasxfail"] for p in phases) else "skipped"
    return "xpassed" if any(p["wasxfail"] for p in phases) else "passed"


def validate_result(document, plan_document):
    plan = validate_plan(plan_document)
    result = unseal(document)
    require(set(result) == {"schema", "plan_sha256", "identity", "shard", "collection",
                            "selected", "state", "exitcode", "reports", "statuses"},
            "Invalid result schema")
    require(result["schema"] == "pytest-shard-result-v1", "Invalid result version")
    name = result["shard"]
    require(name in SHARDS, "Unknown shard")
    require(result["plan_sha256"] == plan_document["sha256"]
            and result["identity"] == plan["identity"], "Result source/plan mismatch")
    require(result["collection"] == plan["collection"], "Collection changed")
    require(result["selected"] == plan["partitions"][name], "Shard selection differs")
    require(result["state"] == "completed" and type(result["exitcode"]) is int
            and result["exitcode"] == 0, "Shard failed, cancelled or nonterminal")
    require(set(result["reports"]) == set(result["selected"]), "Unaccounted or extra executed items")
    derived = {node: status_from_phases(result["reports"][node]) for node in result["selected"]}
    require(result["statuses"] == derived, "Claimed outcomes disagree with phase reports")
    require("failed" not in derived.values(), "Failed item in success result")
    return result


def joined(plan_document, documents):
    plan = validate_plan(plan_document)
    require(len(documents) == len(SHARDS), "Missing or extra shard artifact")
    results = [validate_result(d, plan_document) for d in documents]
    require(Counter(r["shard"] for r in results) == Counter(SHARDS),
            "Duplicate or missing shard")
    executed = [node for r in results for node in r["selected"]]
    require(Counter(executed) == Counter(plan["collection"]["nodeids"]),
            "Global execution is not disjoint exhaustive coverage")
    return seal({
        "schema": "pytest-shard-join-v1", "plan_sha256": plan_document["sha256"],
        "identity": plan["identity"], "state": "completed",
        "canonical_items": len(executed),
        "outcomes": dict(Counter(s for r in results for s in r["statuses"].values())),
        # These are canonical collection-level skips, NOT five executions.
        "collection_skips": plan["collection"]["skipped"],
        "canonical_excluded": plan["collection"]["excluded"],
        "result_sha256": {r["shard"]: d["sha256"] for r, d in zip(results, documents)},
    })


def pytest_session(root, plan_document=None, shard=None):
    """One ordinary pytest session; public hooks only, no private node APIs."""
    import pytest

    class Capture:
        def __init__(self):
            self.excluded = []
            self.skipped = []
            self.collection_failed = False
            self.collection = None
            self.selected = []
            self.reports = {}
            self.own_deselection = False

        def pytest_deselected(self, items):
            if not self.own_deselection:
                self.excluded.extend(item.nodeid for item in items)

        def pytest_collectreport(self, report):
            if report.failed:
                self.collection_failed = True
            elif report.skipped:
                # Paths/line numbers can differ across checkout locations.
                reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else "collection skip"
                self.skipped.append({"nodeid": report.nodeid, "outcome": "skipped",
                                     "reason": str(reason)})

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, session, config, items):
            ids = [item.nodeid for item in items]
            validate_ids(ids)
            self.collection = {
                "nodeids": ids, "excluded": sorted(self.excluded),
                "skipped": sorted(self.skipped, key=lambda r: (r["nodeid"], r["reason"])),
                "selection": {
                    "addopts": config.getini("addopts"),
                    "markexpr": config.getoption("markexpr"),
                    "keyword": config.getoption("keyword"),
                },
            }
            if plan_document is not None:
                plan = validate_plan(plan_document)
                require(self.collection == plan["collection"], "Fresh canonical collection mismatch")
                chosen = set(plan["partitions"][shard])
                removed = [item for item in items if item.nodeid not in chosen]
                items[:] = [item for item in items if item.nodeid in chosen]
                self.own_deselection = True
                try:
                    config.hook.pytest_deselected(items=removed)
                finally:
                    self.own_deselection = False
            self.selected = [item.nodeid for item in items]

        def pytest_collection_finish(self, session):
            # Include reports emitted after modifyitems and reject collection
            # errors even if a plugin tries to continue with a partial suite.
            require(not self.collection_failed and self.collection is not None,
                    "Incomplete canonical collection")
            require([item.nodeid for item in session.items] == self.selected,
                    "Final selected items changed after capture")
            self.collection["skipped"] = sorted(
                self.skipped, key=lambda r: (r["nodeid"], r["reason"]))
            if plan_document is not None:
                require(self.collection == plan_document["collection"], "Collection reports changed")
                require(self.selected == plan_document["partitions"][shard], "Selected items changed")

        def pytest_runtest_logreport(self, report):
            self.reports.setdefault(report.nodeid, []).append({
                "when": report.when, "outcome": report.outcome,
                "wasxfail": hasattr(report, "wasxfail"),
            })

    plugin = Capture()
    args = ["-q"] if plan_document is not None else ["--collect-only", "-q"]
    previous = Path.cwd()
    os.chdir(root)
    try:
        code = int(pytest.main(args, plugins=[plugin]))
    finally:
        os.chdir(previous)
    return code, plugin


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_command = commands.add_parser("plan")
    plan_command.add_argument("--output", type=Path, required=True)
    run_command = commands.add_parser("run")
    run_command.add_argument("--plan", type=Path, required=True)
    run_command.add_argument("--shard", choices=SHARDS, required=True)
    run_command.add_argument("--output", type=Path, required=True)
    join_command = commands.add_parser("join")
    join_command.add_argument("--plan", type=Path, required=True)
    join_command.add_argument("--results", type=Path, required=True)
    join_command.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(argv)
    root = options.root.resolve(strict=True)
    document = read(options.plan) if options.command != "plan" else None
    if options.command == "join":
        require(identity(root) == validate_plan(document)["identity"], "Join source/environment changed")
        result = joined(document, [read(p) for p in sorted(options.results.rglob("result-*.json"))])
        write(options.output, result)
        print(json.dumps({"canonical_items": result["canonical_items"],
                          "outcomes": result["outcomes"]}, sort_keys=True))
        return 0
    current = identity(root)
    if document is not None:
        require(current == validate_plan(document)["identity"], "Runner source/environment changed")
    code, capture = pytest_session(root, document, getattr(options, "shard", None))
    if options.command == "plan":
        require(code == 0 and not capture.collection_failed, "Canonical collection failed")
        result = seal({"schema": "pytest-shard-plan-v1", "identity": current,
                       "collection": capture.collection,
                       "partitions": partition(capture.collection["nodeids"])})
        write(options.output, result)
        print(json.dumps({k: len(v) for k, v in result["partitions"].items()}, sort_keys=True))
        return 0
    result = {
        "schema": "pytest-shard-result-v1", "plan_sha256": document["sha256"],
        "identity": current, "shard": options.shard, "collection": capture.collection,
        "selected": capture.selected, "state": "completed", "exitcode": code,
        "reports": capture.reports, "statuses": {},
    }
    try:
        result["statuses"] = {node: status_from_phases(phases)
                              for node, phases in capture.reports.items()}
    except InvalidEvidence:
        code = code or 2
        result["exitcode"] = code
    write(options.output, seal(result))
    if code == 0:
        validate_result(seal(result), document)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InvalidEvidence, OSError, ValueError, KeyError, TypeError) as error:
        print(f"pytest shard evidence refused: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(2)