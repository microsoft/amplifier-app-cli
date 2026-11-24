"""Public pytest collection/execution and fail-closed coverage receipts."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_pytest_shards.py"
spec = importlib.util.spec_from_file_location("ci_pytest_shards", SCRIPT)
shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shards)


def canonical_ids():
    return [
        "tests/test_new_future_file.py::test_future",
        shards.WITNESS + "::test_policy",
        shards.TRANSFER + "::test_transfer",
        *shards.MEMORY,
    ]


def plan():
    ids = canonical_ids()
    return shards.seal({
        "schema": "pytest-shard-plan-v1",
        "identity": {"commit": "fixture", "python": "3.12"},
        "collection": {"nodeids": ids, "excluded": ["tests/test_pty.py::test_integration"],
                       "skipped": [{"nodeid": "tests/test_other_platform.py", "outcome": "skipped",
                                    "reason": "fixture unsupported"}],
                       "selection": {"markexpr": "not integration"}},
        "partitions": shards.partition(ids),
    })


def phases(status="passed"):
    return [
        {"when": "setup", "outcome": "passed", "wasxfail": False},
        {"when": "call", "outcome": status, "wasxfail": False},
        {"when": "teardown", "outcome": "passed", "wasxfail": False},
    ]


def result(document, name):
    ids = document["partitions"][name]
    return shards.seal({
        "schema": "pytest-shard-result-v1", "plan_sha256": document["sha256"],
        "identity": document["identity"], "shard": name,
        "collection": document["collection"], "selected": ids,
        "state": "completed", "exitcode": 0,
        "reports": {node: phases() for node in ids},
        "statuses": {node: "passed" for node in ids},
    })


def reseal(value):
    return shards.seal({k: v for k, v in value.items() if k != "sha256"})


def test_exact_disjoint_union_future_file_catchall_and_three_cold_cases():
    document = plan()
    actual = shards.validate_plan(document)
    assert actual["partitions"]["general"] == [canonical_ids()[0]]
    assert len(actual["partitions"]["policy-transfer"]) == 2
    for node, name in shards.MEMORY.items():
        assert actual["partitions"][name] == [node]
    report = shards.joined(document, [result(document, name) for name in shards.SHARDS])
    assert report["canonical_items"] == len(canonical_ids())
    assert report["outcomes"] == {"passed": len(canonical_ids())}
    assert report["collection_skips"] == document["collection"]["skipped"]
    assert report["canonical_excluded"] == document["collection"]["excluded"]


@pytest.mark.parametrize("damage", ["missing-memory", "duplicate", "unplanned-memory", "absolute", "traversal", "empty"])
def test_invalid_canonical_collection_refused(damage):
    ids = canonical_ids()
    if damage == "missing-memory":
        ids.pop()
    elif damage == "duplicate":
        ids.append(ids[0])
    elif damage == "unplanned-memory":
        ids.append(shards.HUGE + "[16]")
    elif damage == "absolute":
        ids[0] = "/tmp/test_bad.py::test_bad"
    elif damage == "traversal":
        ids[0] = "../test_bad.py::test_bad"
    else:
        ids = []
    with pytest.raises(shards.InvalidEvidence):
        shards.partition(ids)


@pytest.mark.parametrize("damage", ["missing", "extra", "overlap", "digest"])
def test_plan_tampering_refused(damage):
    document = plan()
    if damage == "missing":
        document["partitions"]["general"] = []
    elif damage == "extra":
        document["partitions"]["general"].append("tests/test_unselected.py::test_extra")
    elif damage == "overlap":
        document["partitions"]["general"].append(document["partitions"]["memory-0"][0])
    else:
        document["identity"]["python"] = "different"
    if damage != "digest":
        document = reseal(document)
    with pytest.raises(shards.InvalidEvidence):
        shards.validate_plan(document)


@pytest.mark.parametrize("damage", [
    "missing-shard", "duplicate-shard", "extra-shard", "cancelled", "nonterminal",
    "exit-failed", "empty-exit5", "different-commit", "different-python", "different-plan",
    "collection-mismatch", "missing-item", "extra-item", "missing-teardown",
    "duplicate-phase", "wrong-order", "failed-call", "false-status", "bad-digest",
])
def test_join_refuses_incomplete_failed_or_mismatched_evidence(damage):
    document = plan()
    results = [result(document, name) for name in shards.SHARDS]
    changed = results[0]
    node = changed["selected"][0]
    if damage == "missing-shard":
        results.pop()
    elif damage == "duplicate-shard":
        results[-1] = deepcopy(results[0])
    elif damage == "extra-shard":
        results.append(deepcopy(results[0]))
    elif damage in {"cancelled", "nonterminal"}:
        changed["state"] = damage
    elif damage in {"exit-failed", "empty-exit5"}:
        changed["exitcode"] = 1 if damage == "exit-failed" else 5
    elif damage in {"different-commit", "different-python"}:
        changed["identity"] = {**changed["identity"], damage.split("-")[1]: "different"}
    elif damage == "different-plan":
        changed["plan_sha256"] = "0" * 64
    elif damage == "collection-mismatch":
        changed["collection"] = {**changed["collection"], "skipped": []}
    elif damage == "missing-item":
        changed["reports"].pop(node)
    elif damage == "extra-item":
        changed["reports"]["tests/test_unselected.py::test_extra"] = phases()
    elif damage == "missing-teardown":
        changed["reports"][node].pop()
    elif damage == "duplicate-phase":
        changed["reports"][node].append(changed["reports"][node][-1])
    elif damage == "wrong-order":
        changed["reports"][node].reverse()
    elif damage == "failed-call":
        changed["reports"][node] = phases("failed")
        changed["statuses"][node] = "failed"
    elif damage == "false-status":
        changed["statuses"][node] = "skipped"
    else:
        changed["sha256"] = "0" * 64
    if damage != "bad-digest":
        results = [reseal(value) for value in results]
    with pytest.raises(shards.InvalidEvidence):
        shards.joined(document, results)


@pytest.mark.parametrize("outcome,xfail,expected", [
    ("passed", False, "passed"), ("skipped", False, "skipped"),
    ("skipped", True, "xfailed"), ("passed", True, "xpassed"),
    ("failed", False, "failed"),
])
def test_terminal_outcomes_keep_skips_and_expected_failures_distinct(outcome, xfail, expected):
    reports = phases(outcome)
    reports[1]["wasxfail"] = xfail
    assert shards.status_from_phases(reports) == expected
    reports = [phases()[0] | {"outcome": "skipped", "wasxfail": xfail}, phases()[-1]]
    assert shards.status_from_phases(reports) == ("xfailed" if xfail else "skipped")


@pytest.fixture
def miniature(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -m 'not integration'\nmarkers =\n    integration: fixture\n",
        encoding="utf-8",
    )
    (tests / "test_general.py").write_text(
        "import pytest\n"
        "def test_pass(): pass\n"
        "@pytest.mark.skip(reason='ordinary runtime skip')\ndef test_skip(): pass\n"
        "@pytest.mark.xfail(reason='fixture expected failure')\ndef test_xfail(): assert False\n"
        "@pytest.mark.integration\ndef test_not_unit(): assert False\n",
        encoding="utf-8",
    )
    (tests / "test_collect_skip.py").write_text(
        "import pytest\npytest.skip('fixture platform unsupported', allow_module_level=True)\n",
        encoding="utf-8",
    )
    (tests / "test_session_storage_transfer.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('mebibytes', [0, 2, 8])\n"
        "def test_huge_full_registered_snapshot_bounded(mebibytes): assert mebibytes in (0, 2, 8)\n"
        "def test_transfer(): pass\n", encoding="utf-8",
    )
    (tests / "test_session_media_policy_witness.py").write_text(
        "def test_witness(): pass\n", encoding="utf-8",
    )
    return tmp_path


def invoke(root, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *map(str, args)],
        text=True, capture_output=True, timeout=45,
    )


def test_real_public_pytest_plan_five_executions_join_and_determinism(miniature):
    root = miniature
    first = root / "plan.json"
    second = root / "second.json"
    for output in (first, second):
        process = invoke(root, "plan", "--output", output)
        assert process.returncode == 0, process.stdout + process.stderr
    assert first.read_bytes() == second.read_bytes()
    document = shards.read(first)
    assert len(document["collection"]["nodeids"]) == 8
    assert document["collection"]["excluded"] == ["tests/test_general.py::test_not_unit"]
    assert document["collection"]["skipped"] == [{
        "nodeid": "tests/test_collect_skip.py", "outcome": "skipped",
        "reason": "Skipped: fixture platform unsupported",
    }]
    results = root / "results"
    for name in shards.SHARDS:
        process = invoke(root, "run", "--plan", first, "--shard", name,
                         "--output", results / f"result-{name}.json")
        assert process.returncode == 0, process.stdout + process.stderr
    process = invoke(root, "join", "--plan", first, "--results", results,
                     "--output", root / "joined.json")
    assert process.returncode == 0, process.stdout + process.stderr
    combined = shards.read(root / "joined.json")
    assert combined["canonical_items"] == 8
    assert combined["outcomes"] == {"passed": 6, "skipped": 1, "xfailed": 1}
    assert len(combined["collection_skips"]) == 1
    (results / "result-memory-2.json").unlink()
    missing = invoke(root, "join", "--plan", first, "--results", results,
                     "--output", root / "must-not-exist.json")
    assert missing.returncode != 0 and not (root / "must-not-exist.json").exists()


@pytest.mark.parametrize("change", ["new-test", "config", "collection-error", "runtime-failure"])
def test_real_runner_refuses_drift_or_reports_actual_failure(miniature, change):
    root = miniature
    planned = invoke(root, "plan", "--output", root / "plan.json")
    assert planned.returncode == 0, planned.stdout + planned.stderr
    if change == "new-test":
        (root / "tests/test_future.py").write_text("def test_future(): pass\n")
    elif change == "config":
        with (root / "pytest.ini").open("a") as stream:
            stream.write("\n# different config identity\n")
    elif change == "collection-error":
        (root / "tests/test_broken.py").write_text("raise RuntimeError('fixture error')\n")
    else:
        (root / "tests/test_general.py").write_text(
            "import pytest\n"
            "def test_pass(): assert False\n"
            "@pytest.mark.skip(reason='ordinary runtime skip')\ndef test_skip(): pass\n"
            "@pytest.mark.xfail(reason='fixture expected failure')\ndef test_xfail(): assert False\n"
            "@pytest.mark.integration\ndef test_not_unit(): assert False\n"
        )
    process = invoke(root, "run", "--plan", root / "plan.json", "--shard", "general",
                     "--output", root / "result-general.json")
    assert process.returncode != 0
    if change == "runtime-failure":
        actual = shards.read(root / "result-general.json")
        assert actual["exitcode"] == 1
        assert actual["statuses"]["tests/test_general.py::test_pass"] == "failed"


def test_real_planner_refuses_fatal_or_empty_collection(miniature):
    (miniature / "tests/test_broken.py").write_text("raise RuntimeError('collection failed')\n")
    process = invoke(miniature, "plan", "--output", miniature / "bad-plan.json")
    assert process.returncode != 0 and not (miniature / "bad-plan.json").exists()


def test_real_planner_refuses_late_collection_wrapper_removal(miniature):
    (miniature / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_collection_modifyitems(items):\n"
        "    yield\n"
        "    items.pop()\n",
        encoding="utf-8",
    )
    process = invoke(miniature, "plan", "--output", miniature / "bad-plan.json")
    assert process.returncode != 0 and not (miniature / "bad-plan.json").exists()


@pytest.mark.parametrize("planner,runner", [
    ("success", "success"), ("failure", "success"), ("cancelled", "success"),
    ("skipped", "success"), ("success", "failure"), ("success", "cancelled"),
    ("success", "skipped"),
])
def test_workflow_join_refuses_failed_cancelled_or_skipped_jobs_even_with_results(planner, runner):
    workflow = yaml.safe_load((SCRIPT.parents[1] / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["linux-coverage"]["steps"]
    gate = next(s for s in steps if s.get("name") == "Require successful planner and shard job lifecycles")
    assert gate["env"] == {
        "PLAN_RESULT": "${{ needs.linux-plan.result }}",
        "SHARD_RESULT": "${{ needs.linux-shard.result }}",
    }
    command = shlex.split(gate["run"])
    assert command[0] == "python"
    process = subprocess.run(
        [sys.executable, *command[1:]],
        env={**os.environ, "PLAN_RESULT": planner, "SHARD_RESULT": runner},
        capture_output=True, text=True, timeout=10,
    )
    assert (process.returncode == 0) == (planner == runner == "success")
    assert steps.index(gate) < next(
        index for index, s in enumerate(steps)
        if s.get("name") == "Require exact full-suite terminal coverage"
    )


def test_workflow_keeps_existing_checks_and_exhaustive_bounded_join():
    workflow = yaml.safe_load((SCRIPT.parents[1] / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["test"]["strategy"]["matrix"]["os"] == ["macos-latest", "windows-latest"]
    assert jobs["test"]["name"] == "pytest (${{ matrix.os }}, py${{ matrix.python-version }})"
    assert jobs["linux-coverage"]["name"] == "pytest (ubuntu-latest, py${{ matrix.python-version }})"
    assert jobs["linux-coverage"]["if"] == "always()"
    assert set(jobs["linux-coverage"]["needs"]) == {"linux-plan", "linux-shard"}
    assert jobs["linux-shard"]["strategy"]["matrix"]["shard"] == list(shards.SHARDS)
    for name in ("test", "integration", "linux-plan", "linux-shard", "linux-coverage"):
        assert jobs[name]["timeout-minutes"] == 10
        assert jobs[name]["strategy"]["fail-fast"] is False
    doctor = [step for job in jobs.values() for step in job["steps"]
              if step.get("name", "").startswith("Verify installed doctor wheel")]
    assert len(doctor) == 1
    assert doctor[0]["if"] == "matrix.python-version == '3.11' && success()"
    assert "check_doctor_wheel.py" in doctor[0]["run"]
    assert next(s for s in jobs["test"]["steps"] if s.get("name") == "Run test suite")["run"] == "uv run pytest -q"
    assert jobs["integration"]["steps"][-1]["run"] == "uv run pytest -m integration -q"