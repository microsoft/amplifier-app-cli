"""Synthetic collector fixtures: nothing here comes from a real user home."""

from copy import deepcopy
import importlib.metadata as metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from amplifier_app_cli.commands.doctor import doctor
from amplifier_app_cli.lib import diagnostics as diag
from amplifier_app_cli.lib import venv_home_guard as guard

SECRET = "synthetic-private-sentinel-482"


@pytest.fixture
def target(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "absent-home"
    monkeypatch.chdir(project)
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.delenv("AMPLIFIER_ALLOW_SHARED_VENV", raising=False)
    for name in list(os.environ):
        if name.startswith("AMPLIFIER_MODULE_"):
            monkeypatch.delenv(name)
    # Real guard, synthetic site-packages boundary.
    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(guard, "site_packages_dir", lambda: site)
    return diag.current_target()


def write_settings(target, text):
    target.home.mkdir(exist_ok=True)
    (target.home / "settings.yaml").write_text(text, encoding="utf-8")


def safe_report(target):
    report = diag.collect_diagnostics(target)
    assert SECRET not in json.dumps(report)
    assert SECRET not in diag.render_text(report)
    assert SECRET not in diag.render_summary(report)
    return report


def test_missing_files_complete_without_creating_home(target):
    report = safe_report(target)
    assert report["schema_version"] == 1
    assert report["mode"] == "passive"
    assert report["collection_status"] == "complete"
    assert report["health"] == {"status": "not_assessed", "reason": "collection_only"}
    assert "Health: not assessed" in diag.render_text(report)
    assert [row["status"] for row in report["scopes"]] == ["missing"] * 3 + ["not_selected"]
    assert report["effective_settings"]["reason"] == "effective_resolution_not_performed"
    assert report["live_session"]["reason"] == "not_observed"
    assert not target.home.exists()
    assert diag.render_summary(report).splitlines() == [
        "Amplifier doctor",
        "Configuration report collected",
        "",
        "User settings (global): Not present (optional)",
        "Project settings: Not present (optional)",
        "Local settings: Not present (optional)",
        "AI connections and running session: Not tested",
        "Effective settings were not resolved.",
        "No settings were changed.",
        "These results do not confirm Amplifier is working.",
        "Next: Tell Amplifier what went wrong and ask for the CLI doctor agent.",
        "Technical details: amplifier doctor --verbose (review before sharing).",
    ]


@pytest.mark.parametrize("text,status", [
    ("", "empty"), ("{}", "empty"), ("null", "empty"),
    ("[]", "invalid_shape"), ("false", "invalid_shape"),
    ("bundle: [" + SECRET, "invalid_yaml"),
    (f"a: {SECRET}\na: 2", "unsupported_yaml"),
    (f"a: &x [{SECRET}]\nb: *x", "unsupported_yaml"),
    (f"a: !!str {SECRET}", "unsupported_yaml"),
    (f"a: !synthetic {SECRET}", "unsupported_yaml"),
    ("? [bad, key]\n: value", "invalid_yaml"),
    ("a: " + "x" * 16385, "too_large"),
    ("a: " + "[" * 34 + "0" + "]" * 34, "too_large"),
    ("a: [" + ",".join("0" for _ in range(10001)) + "]", "too_large"),
    ("#" * (diag.SETTINGS_LIMIT + 1), "too_large"),
], ids=[
    "empty", "empty-mapping", "null", "list-root", "boolean-root",
    "malformed", "duplicate-key", "alias", "standard-tag", "custom-tag",
    "unhashable-key", "scalar-limit", "depth-limit", "node-limit", "file-limit",
])
def test_bounded_settings(target, text, status):
    write_settings(target, text)
    report = safe_report(target)
    assert report["scopes"][0]["status"] == status
    assert report["collection_status"] == ("complete" if status == "empty" else "partial")
    if status != "empty":
        assert {"code": "settings_" + status, "scope": "global"} in report["issues"]
        assert not any(row["scope"] == "global" for row in report["declarations"])
    if status == "unsupported_yaml":
        assert {"code": "settings_invalid_yaml", "scope": "global"} not in report["issues"]


def test_invalid_utf8_is_safely_partial(target):
    target.home.mkdir()
    (target.home / "settings.yaml").write_bytes(b"\xff")
    report = safe_report(target)
    assert report["scopes"][0]["status"] == "invalid_yaml"
    assert report["collection_status"] == "partial"
    assert {"code": "settings_invalid_yaml", "scope": "global"} in report["issues"]


def test_all_scopes_and_exact_session_layout(target):
    from amplifier_app_cli.lib.settings import SettingsPaths

    selected = diag.DiagnosticTarget(target.cwd, target.home, "session_01")
    paths = SettingsPaths.with_session("session_01", diag.get_project_slug())
    expected = [
        paths.global_settings, paths.project_settings, paths.local_settings, paths.session_settings,
    ]
    for index, path in enumerate(expected):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"routing: {{matrix: synthetic-{index}}}", encoding="utf-8")
    report = safe_report(selected)
    assert [r["path"] for r in report["scopes"]] == [str(p) for p in expected]
    assert [r["status"] for r in report["scopes"]] == ["valid"] * 4
    assert report["target"]["session_selected"] is True


@pytest.mark.parametrize("home_value", [None, "", "~/alternate", "./relative"])
def test_home_parity_with_installed_foundation(target, monkeypatch, home_value):
    from amplifier_foundation.paths.resolution import get_amplifier_home

    if home_value is None:
        monkeypatch.delenv("AMPLIFIER_HOME", raising=False)
    else:
        monkeypatch.setenv("AMPLIFIER_HOME", home_value)
    assert diag.current_target().home == get_amplifier_home().resolve()


@pytest.mark.parametrize("value", ["../escape", "/absolute", "x/y", "a.b", "", "-a", "x" * 201, "a\nb", "é", "x\\y"])
def test_unsafe_session_rejected_without_echo(target, value):
    assert not diag.valid_session_id(value)
    with pytest.raises(ValueError, match="^invalid_session_id$"):
        diag.current_target(value)
    result = CliRunner().invoke(doctor, ["--session", value])
    assert result.exit_code == 2
    assert value not in result.output or not value  # Click error uses fixed callback text.


def test_declarations_are_opaque_and_equality_shared(target, monkeypatch):
    source = f"https://user:{SECRET}@example.test/repo?token={SECRET}#private"
    settings = {
        SECRET: {SECRET: SECRET},
        "bundle": {"active": SECRET, "app": [SECRET, source]},
        "routing": {"matrix": SECRET},
        "sources": {"modules": {SECRET: source}, "bundles": {SECRET: source}},
        "overrides": {
            SECRET: {"source": source, "config": {"api_key": SECRET}},
            "modules": {SECRET: source}, "bundles": [SECRET],
        },
        "modules": {"providers": [{"id": SECRET, "module": SECRET, "source": source}]},
        "config": {"providers": [{"module": SECRET, "source": source}]},
    }
    write_settings(target, yaml.safe_dump(settings, sort_keys=False))
    monkeypatch.setenv("AMPLIFIER_MODULE_" + SECRET, source)
    monkeypatch.setenv("AMPLIFIER_GIT_HOST", source)
    monkeypatch.setenv("AMPLIFIER_HOME_ENV", source)
    report = safe_report(target)
    rows = report["declarations"]
    active = next(r for r in rows if r["container"] == "bundle" and r["member"] == "active")
    matrix = next(r for r in rows if r["member"] == "matrix")
    provider = next(r for r in rows if r["container"] == "modules.providers")
    assert active["value_ref"] == matrix["value_ref"] == provider["id_ref"] == provider["module_ref"]
    sources = [r for r in rows if r["member"] == "source"]
    assert len({r["value_ref"] for r in sources}) == 1
    assert all("index" in r for r in sources)
    assert provider["source_kind"] == "http"
    assert next(r for r in rows if r["container"] == "overrides" and r["member"] == "modules")["shape"] == "mapping"
    assert report["environment_channels"]["module_count"] == 1
    assert report["environment_channels"]["AMPLIFIER_HOME_ENV"] == {"present": True}
    assert report["collection_status"] == "complete"


@pytest.mark.parametrize("value,shape", [
    (None, "null"), (False, "false"), (True, "true"), ("", "empty_string"),
    ([], "empty_list"), ({}, "empty_mapping"), (12, "integer"), (2.5, "number"),
])
def test_declaration_shapes_preserved(target, value, shape):
    write_settings(target, yaml.safe_dump({"bundle": {"active": value}}))
    report = safe_report(target)
    assert next(r for r in report["declarations"] if r["member"] == "active")["shape"] == shape


def test_container_null_distinguished_from_absent(target):
    write_settings(target, "bundle: null")
    rows = safe_report(target)["declarations"]
    assert next(r for r in rows if r["member"] == "active")["container_shape"] == "null"
    assert next(r for r in rows if r["member"] == "matrix")["container_shape"] == "absent"


def test_limits_are_explicit(target, monkeypatch):
    write_settings(target, yaml.safe_dump({"sources": {"modules": {f"module-{i}": SECRET for i in range(210)}}}))
    for i in range(210):
        monkeypatch.setenv(f"AMPLIFIER_MODULE_SYNTHETIC_{i}", SECRET)
    report = safe_report(target)
    assert len(report["declarations"]) == 200
    assert len(report["environment_channels"]["modules"]) == 200
    assert report["environment_channels"]["module_count"] == 210
    assert report["collection_status"] == "partial"
    assert {"declarations_truncated", "environment_channels_truncated"} <= {r["code"] for r in report["issues"]}
    summary = diag.render_summary(report)
    assert "Some configuration entries were omitted." in summary
    assert "Some environment entries were omitted." in summary


def test_regular_files_only(target):
    target.home.mkdir()
    (target.home / "settings.yaml").mkdir()
    assert safe_report(target)["scopes"][0]["status"] == "unsupported_file"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
def test_fifo_is_not_opened(target):
    target.home.mkdir()
    os.mkfifo(target.home / "settings.yaml")
    assert safe_report(target)["scopes"][0]["status"] == "unsupported_file"


def test_leaf_and_session_parent_symlinks_rejected(target, tmp_path):
    real = tmp_path / "real.yaml"
    real.write_text("bundle: {active: synthetic}", encoding="utf-8")
    target.home.mkdir()
    try:
        (target.home / "settings.yaml").symlink_to(real)
    except OSError:
        pytest.skip("symlink creation not permitted")
    assert safe_report(target)["scopes"][0]["status"] == "unsupported_symlink"
    (target.home / "projects").symlink_to(tmp_path, target_is_directory=True)
    selected = diag.DiagnosticTarget(target.cwd, target.home, "synthetic")
    assert safe_report(selected)["scopes"][3]["status"] == "unsupported_symlink"


def test_unreadable_and_errors_do_not_leak(target, monkeypatch):
    write_settings(target, "bundle: {active: synthetic}")
    original = os.open

    def fail(path, flags, *args, **kwargs):
        if Path(path) == target.home / "settings.yaml":
            raise PermissionError(SECRET)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail)
    assert safe_report(target)["scopes"][0]["status"] == "unreadable"


def test_guard_refusal_stops_collection_and_override_is_reported(target, monkeypatch):
    site = guard.site_packages_dir()
    foreign = site.parent / SECRET / "cache" / "synthetic-0123456789abcdef"
    (site / "synthetic.pth").write_text(str(foreign), encoding="utf-8")
    report = safe_report(target)
    assert report["collection_status"] == "refused"
    assert report["ownership"]["status"] == "refused"
    assert report["ownership"]["foreign_count"] == 1
    assert report["runtime"] is None
    assert report["declarations"] == []
    monkeypatch.setenv("AMPLIFIER_ALLOW_SHARED_VENV", "1")
    report = safe_report(target)
    assert report["collection_status"] == "complete"
    assert report["ownership"]["status"] == "override"
    assert "An existing ownership override allowed collection." in diag.render_summary(report)
    assert not target.home.exists()


def test_unknown_guard_fails_closed(target, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(guard, "detect_home_conflict", fail)
    report = safe_report(target)
    assert report["collection_status"] == "refused"
    assert report["ownership"]["status"] == "unknown"
    assert report["runtime"] is None
    assert report["issues"] == [{"code": "ownership_unknown"}]
    assert "Collection stopped: Installation ownership could not be checked." in diag.render_summary(report)


@pytest.mark.parametrize("suffix", ["\x1b[31m", "\u202e", "/https://user:password@example.test", "?token=synthetic"])
def test_unsafe_path_withheld(target, tmp_path, suffix):
    path = tmp_path / ("unsafe" + suffix)
    report = safe_report(diag.DiagnosticTarget(path, target.home))
    assert report["target"]["cwd"] is None
    assert {"code": "path_withheld"} in report["issues"]
    assert suffix not in diag.render_text(report)
    assert "Some location details were withheld." in diag.render_summary(report)
    assert report["collection_status"] == "partial"


def fake_distribution(tmp_path, monkeypatch, direct_url, version="1.2.3"):
    root = tmp_path / "installed"
    info = root / "synthetic.dist-info"
    info.mkdir(parents=True)
    path = info / "direct_url.json"
    path.write_text(json.dumps(direct_url), encoding="utf-8")
    dist = SimpleNamespace(
        version=version,
        files=[metadata.PackagePath("synthetic.dist-info/direct_url.json")],
        locate_file=lambda entry: root / entry,
    )
    monkeypatch.setattr(metadata, "distribution", lambda name: dist)
    return path


def test_distribution_metadata_allowlist(target, tmp_path, monkeypatch):
    fake_distribution(tmp_path, monkeypatch, {
        "url": f"git+https://user:{SECRET}@example.test/repo?token={SECRET}",
        "vcs_info": {"vcs": SECRET, "requested_revision": SECRET, "commit_id": "abcdef0123456789"},
        "dir_info": {"editable": True}, SECRET: SECRET,
    })
    report = safe_report(target)
    direct = report["runtime"]["distributions"][0]["direct_url"]
    assert direct["commit"] == "abcdef0123456789"
    assert direct["local_target"] is None
    assert direct["editable"] is True


@pytest.mark.parametrize("url,expected", [
    ("file:///synthetic/a%20b", "/synthetic/a b"),
    ("file:///synthetic/%2541", None),  # no second decoding or percent-shaped disclosure
    ("file://authority/synthetic", None),
    ("file:///synthetic?token=hidden", None),
    ("file:///synthetic#fragment", None),
])
def test_direct_url_local_paths_only(target, tmp_path, monkeypatch, url, expected):
    fake_distribution(tmp_path, monkeypatch, {"url": url})
    report = safe_report(target)
    actual = report["runtime"]["distributions"][0]["direct_url"]["local_target"]
    if os.name == "nt" and expected is not None:
        # A POSIX-root URL has no Windows drive and is not native absolute.
        expected = None
    assert actual == expected


def test_direct_url_native_absolute_target(target, tmp_path, monkeypatch):
    fake_distribution(tmp_path, monkeypatch, {"url": target.cwd.as_uri(), "dir_info": {"editable": True}})
    report = safe_report(target)
    direct = report["runtime"]["distributions"][0]["direct_url"]
    assert direct["local_target"] == str(target.cwd)
    assert direct["editable"] is True


@pytest.mark.parametrize("kind", ["large", "invalid_json", "version", "local_version", "error"])
def test_bad_metadata_is_partial_without_payload(target, tmp_path, monkeypatch, kind):
    version = SECRET if kind == "version" else "1.0+" + SECRET if kind == "local_version" else "1.0"
    path = fake_distribution(tmp_path, monkeypatch, {"url": SECRET}, version=version)
    if kind == "large":
        path.write_text(SECRET * 4000, encoding="utf-8")
    if kind == "invalid_json":
        path.write_text(SECRET, encoding="utf-8")
    if kind == "error":
        def fail(name):
            raise RuntimeError(SECRET)
        monkeypatch.setattr(metadata, "distribution", fail)
    report = safe_report(target)
    assert report["collection_status"] == "partial"
    assert "Some installation details could not be read or shown." in diag.render_summary(report)


def test_text_and_json_exit_contract(target):
    runner = CliRunner()
    result = runner.invoke(doctor, ["--format", "json"])
    assert result.exit_code == 0
    assert result.output.count("\n") == 1
    assert json.loads(result.output)["collection_status"] == "complete"
    write_settings(target, "bundle: [")
    result = runner.invoke(doctor, ["--format", "json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["collection_status"] == "partial"
    result = runner.invoke(doctor)
    assert result.exit_code == 1
    assert "Configuration report incomplete" in result.output
    result = runner.invoke(doctor, ["--verbose"])
    assert result.exit_code == 1
    assert "not a health verdict" in result.output


@pytest.mark.parametrize("status,copy", [
    ("valid", "Read"),
    ("empty", "No entries"),
    ("missing", "Not present (optional)"),
    ("invalid_yaml", "Could not parse format or encoding"),
    ("invalid_shape", "Could not inspect: expected named settings"),
    ("unsupported_yaml", "Doctor cannot inspect this YAML syntax"),
    ("unsupported_file", "Not read: not a regular file"),
    ("unsupported_symlink", "Not read: symbolic links are not inspected"),
    ("unreadable", "Could not read"),
    ("too_large", "Doctor's inspection limit was reached"),
    ("unknown", "Not read"),
    ("not_selected", "Not read"),
    ("\x1b[31mfuture-private-status", "Not read"),
], ids=[
    "read", "empty", "missing", "format", "shape", "syntax", "file", "symlink",
    "unreadable", "limit", "unknown", "not-selected", "future",
])
def test_summary_scope_statuses(target, status, copy):
    report = safe_report(target)
    report["scopes"][0]["status"] = status
    assert diag.render_summary(report).splitlines()[3] == "User settings (global): " + copy


@pytest.mark.parametrize("selected", [False, True], ids=["no-session", "session"])
def test_summary_refusal_never_claims_scopes_were_read(target, selected):
    report = safe_report(target)
    report["target"]["session_selected"] = selected
    report["collection_status"] = "refused"
    report["issues"] = [{"code": "ownership_conflict"}]
    for row, status in zip(report["scopes"], ("valid", "missing", "empty", "not_selected")):
        row["status"] = status
    lines = diag.render_summary(report).splitlines()
    assert lines[1] == "Configuration report could not be collected"
    expected = [
        "User settings (global): Not read", "Project settings: Not read", "Local settings: Not read",
    ]
    if selected:
        expected.append("Session settings: Not read")
    assert lines[3:3 + len(expected)] == expected
    assert ("Session settings: Not read" in lines) is selected
    assert "Collection stopped: This installation is linked to another Amplifier home." in lines


def test_summary_selected_session_only_and_fixed_order(target):
    report = safe_report(diag.DiagnosticTarget(target.cwd, target.home, "synthetic_session"))
    report["scopes"].reverse()
    lines = diag.render_summary(report).splitlines()
    assert lines[3:8] == [
        "User settings (global): Not present (optional)",
        "Project settings: Not present (optional)",
        "Local settings: Not present (optional)",
        "Session settings: Not present (optional)",
        "AI connections and running session: Not tested",
    ]
    report["target"]["session_selected"] = False
    report["scopes"][0]["status"] = "valid"
    assert "Session settings:" not in diag.render_summary(report)


def test_summary_notices_are_all_preserved_and_deduplicated(target):
    report = safe_report(target)
    report["collection_status"] = "partial"
    report["ownership"]["status"] = "override"
    report["issues"] = [
        {"code": code} for code in (
            "ownership_conflict", "ownership_unknown", "target_unavailable", "invalid_session_id",
            "distribution_metadata_unavailable", "distribution_version_withheld",
            "path_withheld", "declarations_truncated", "environment_channels_truncated",
            "future_limit", "future_limit",
        )
    ] * 2
    lines = diag.render_summary(report).splitlines()
    for notice in (
        "Collection stopped: This installation is linked to another Amplifier home.",
        "Collection stopped: Installation ownership could not be checked.",
        "Collection stopped: The local environment could not be inspected.",
        "Collection stopped: The session selection was rejected.",
        "An existing ownership override allowed collection.",
        "Some installation details could not be read or shown.",
        "Some location details were withheld.",
        "Some configuration entries were omitted.",
        "Some environment entries were omitted.",
        "Other collection limits were reported; see technical details.",
    ):
        assert lines.count(notice) == 1
    report["collection_status"] = "complete"
    report["issues"] = []
    assert "An existing ownership override allowed collection." in diag.render_summary(report)


def test_summary_scope_issues_explained_only_when_represented(target):
    report = safe_report(target)
    report["scopes"][0]["status"] = "unsupported_yaml"
    report["issues"] = [{"code": "settings_unsupported_yaml", "scope": "global"}]
    assert "Other collection limits" not in diag.render_summary(report)
    for issue in (
        {"code": "settings_unsupported_yaml", "scope": "session"},
        {"code": "settings_unsupported_yaml"},
        {"code": "settings_unreadable", "scope": "global"},
        {"code": "settings_future", "scope": "global"},
        {"code": "future", "scope": "global"},
        {"code": "settings_unsupported_yaml", "scope": "future"},
    ):
        report["issues"] = [issue]
        assert "Other collection limits were reported; see technical details." in diag.render_summary(report)


def test_summary_is_fixed_ascii_copy_and_does_not_mutate_report(target):
    report = safe_report(target)
    payload = "private-target-\x1b[31m\u202e\N{SNOWMAN}"
    report["target"].update(cwd=payload, home=payload, session_selected=True)
    report["runtime"] = {"version": payload, "local_target": payload}
    report["declarations"] = [{"value_ref": payload, "source_kind": payload}]
    report["environment_channels"] = {"modules": [payload]}
    report["ownership"]["status"] = payload
    report["scopes"][0].update(path=payload, status=payload)
    report["scopes"][3].update(path=payload, status="valid", session_id=payload)
    report["issues"] = [{"code": payload, "scope": payload}]
    before = deepcopy(report)
    summary = diag.render_summary(report)
    assert report == before
    assert summary.isascii()
    assert payload not in summary
    assert "Other collection limits were reported; see technical details." in summary
    assert "All checks pass" not in summary
    assert "Session settings: Read" in summary


@pytest.mark.parametrize("status,code", [
    ("complete", 0), ("partial", 1), ("refused", 1),
], ids=["complete", "partial", "refused"])
def test_json_bytes_unchanged_with_verbose_and_same_collector(target, status, code):
    if status == "partial":
        write_settings(target, "bundle: [")
    elif status == "refused":
        foreign = target.home.parent / "foreign-home" / "cache" / "synthetic-0123456789abcdef"
        (guard.site_packages_dir() / "synthetic.pth").write_text(str(foreign), encoding="utf-8")
    report = safe_report(target)
    assert report["collection_status"] == status
    expected = (json.dumps(report, ensure_ascii=True) + "\n").encode()
    runner = CliRunner()
    for args in (["--format", "json"], ["--format", "json", "--verbose"]):
        result = runner.invoke(doctor, args)
        assert result.exit_code == code, result.output
        assert result.stdout_bytes == expected


@pytest.mark.parametrize("args", [["--verbose"], ["--format", "text", "--verbose"]],
                         ids=["verbose", "text-verbose"])
def test_verbose_is_exact_original_technical_renderer(target, args):
    write_settings(target, "bundle: {active: synthetic}")
    report = safe_report(target)
    result = CliRunner().invoke(doctor, args)
    assert result.exit_code == 0
    assert result.output == diag.render_text(report) + "\n"


def test_help_explains_presentation_and_saved_session(target):
    result = CliRunner().invoke(doctor, ["--help"], terminal_width=120)
    assert result.exit_code == 0
    assert "Read local configuration without changing settings." in result.output
    assert "text checklist (default)" in result.output
    assert "exact session's settings, not its live state." in result.output
    assert "Show technical details (text only; does not affect JSON)." in result.output