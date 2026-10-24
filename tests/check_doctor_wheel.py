"""Installed-wheel acceptance outside the checkout, without PYTHONPATH.

Run only in a disposable wheel virtualenv. A synthetic wheel asks the actual
installer to generate the legacy amplifier console script, as another
distribution can do. No handwritten launcher replaces that acceptance check.
"""

import base64
import csv
import hashlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile


FIXTURE_NAME = "doctor-legacy-launcher-fixture"


def _legacy_wheel(root):
    info = "doctor_legacy_launcher_fixture-0.0.0.dist-info"
    files = {
        info + "/METADATA": (
            "Metadata-Version: 2.1\nName: " + FIXTURE_NAME + "\nVersion: 0.0.0\n\n"
        ).encode(),
        info + "/WHEEL": b"Wheel-Version: 1.0\nGenerator: synthetic-doctor-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        info + "/entry_points.txt": b"[console_scripts]\namplifier = amplifier_app_cli.main:main\n",
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, str(len(data))))
    writer.writerow((info + "/RECORD", "", ""))
    files[info + "/RECORD"] = record.getvalue().encode()
    wheel = root / "doctor_legacy_launcher_fixture-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return wheel


def _run(command, args, env, project, code, *, collection=None):
    result = subprocess.run(
        command + ["doctor", *args], env=env, cwd=project,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == code, result.stdout + result.stderr
    errors = result.stderr.splitlines()
    audit_lines = [line for line in errors if line.startswith("DOCTOR_AUDIT:")]
    assert len(audit_lines) == 1, result.stderr
    audit = json.loads(audit_lines[0].removeprefix("DOCTOR_AUDIT:"))
    assert audit["violations"] == [], audit
    assert audit["bytecode_suppressed"] and not audit["heavy_loaded"], audit
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert "synthetic-private-value" not in result.stdout + result.stderr
    errors.remove(audit_lines[0])
    if code == 2:
        assert result.stdout == ""
        assert errors == [
            "Usage: amplifier doctor [--format text|json] [--session SESSION_ID]",
            "Error: invalid_doctor_arguments",
        ]
    else:
        assert errors == [], errors
    if collection is not None:
        assert result.stdout.count("\n") == 1
        report = json.loads(result.stdout)
        assert report["collection_status"] == collection
        assert report["health"] == {"status": "not_assessed", "reason": "collection_only"}
        return report


def _check_entry(command, root, env, ownership_pth):
    project = root / "project"
    app_home = Path(env["AMPLIFIER_HOME"])
    for args, code, status in (
        (["--format", "json"], 0, "complete"), ([], 0, None),
        (["--help"], 0, None),
        (["--session", "synthetic_session-01", "--format", "json"], 0, "complete"),
        (["--format", "synthetic-secret"], 2, None),
        (["--unknown=synthetic-secret"], 2, None),
        (["--session", "../synthetic-secret"], 2, None),
        (["--session"], 2, None),
        (["synthetic-secret"], 2, None),
    ):
        _run(command, args, env, project, code, collection=status)
        assert not app_home.exists()
        assert not list(project.iterdir())

    app_home.mkdir()
    settings = app_home / "settings.yaml"
    settings.write_text("bundle: [synthetic-secret", encoding="utf-8")
    before = settings.read_bytes()
    try:
        _run(command, ["--format", "json"], env, project, 1, collection="partial")
        assert settings.read_bytes() == before
        assert list(app_home.iterdir()) == [settings]
    finally:
        settings.unlink()
        app_home.rmdir()

    foreign = root / "synthetic-secret" / "cache" / "synthetic-0123456789abcdef"
    ownership_pth.write_text(str(foreign) + "\n", encoding="utf-8")
    try:
        for override, status, code, collection in (
            (False, "refused", 1, "refused"), (True, "override", 0, "complete"),
        ):
            guarded_env = dict(env)
            if override:
                guarded_env["AMPLIFIER_ALLOW_SHARED_VENV"] = "1"
            report = _run(
                command, ["--format", "json"], guarded_env, project, code,
                collection=collection,
            )
            assert report["ownership"]["status"] == status
            assert report["ownership"]["foreign_count"] == 1
            assert not app_home.exists()
            assert not list(project.iterdir())
    finally:
        ownership_pth.unlink()


def main():
    assert "PYTHONPATH" not in os.environ
    dist = metadata.distribution("amplifier-app-cli")
    location = Path(dist.locate_file("amplifier_app_cli")).resolve()
    assert location.is_relative_to(Path(sys.prefix).resolve())
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable")
    entry = next(e for e in dist.entry_points if e.group == "console_scripts" and e.name == "amplifier")
    assert entry.value == "amplifier_app_cli.__main__:main"
    for relative in (
        "__main__.py", "main.py", "lib/diagnostics.py", "commands/doctor.py",
        "_bundle/bundle.md", "_bundle/behaviors/cli-expertise.yaml",
        "_bundle/agents/cli-doctor.md",
    ):
        assert (location / relative).is_file(), relative

    installer = shutil.which("uv")
    assert installer is not None, "uv is required to generate the fixture launcher"
    sitecustomize = Path(sysconfig.get_paths()["purelib"]) / "sitecustomize.py"
    ownership_pth = sitecustomize.with_name("doctor-ownership-test.pth")
    assert not sitecustomize.exists(), "only use a fresh dedicated wheel virtualenv"
    assert not ownership_pth.exists()
    probe = Path(__file__).with_name("doctor_safety_probe.py").read_bytes()
    console = Path(sys.executable).parent / ("amplifier.exe" if os.name == "nt" else "amplifier")
    original_console, original_mode = console.read_bytes(), console.stat().st_mode
    fixture_installed = False
    try:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "project").mkdir()
            (root / "home").mkdir()
            env = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("AMPLIFIER_", "_AMPLIFIER_"))
                and key not in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE")
            }
            env.update(
                HOME=str(root / "home"), USERPROFILE=str(root / "home"),
                AMPLIFIER_HOME=str(root / "home" / "absent-amplifier"),
                AMPLIFIER_HOME_ENV="1", AMPLIFIER_GIT_HOST="synthetic-private-value",
                AMPLIFIER_MODULE_SYNTHETIC="synthetic-private-value",
            )
            sitecustomize.write_bytes(probe)
            for command in (
                [str(console)], [sys.executable, "-m", "amplifier_app_cli"],
                [sys.executable, "-m", "amplifier_app_cli.main"],
            ):
                _check_entry(command, root, env, ownership_pth)
            sitecustomize.unlink()

            # Install a valid fixture wheel, not a script crafted by this test.
            wheel = _legacy_wheel(root)
            subprocess.run(
                [installer, "pip", "install", "--python", sys.executable,
                 "--no-deps", "--no-index", str(wheel)],
                cwd=root, env=env, check=True, capture_output=True, text=True,
            )
            fixture_installed = True
            fixture = metadata.distribution(FIXTURE_NAME)
            legacy = next(e for e in fixture.entry_points if e.name == "amplifier")
            assert legacy.value == "amplifier_app_cli.main:main"
            assert console.read_bytes() != original_console
            sitecustomize.write_bytes(probe)
            _check_entry([str(console)], root, env, ownership_pth)
    finally:
        ownership_pth.unlink(missing_ok=True)
        sitecustomize.unlink(missing_ok=True)
        try:
            if fixture_installed:
                subprocess.run(
                    [installer, "pip", "uninstall", "--python", sys.executable, FIXTURE_NAME],
                    check=True, capture_output=True, text=True,
                )
        finally:
            console.write_bytes(original_console)
            console.chmod(original_mode)
    print("Installed wheel: new console, module, legacy module, installer-generated legacy console, and doctor assets verified")


if __name__ == "__main__":
    main()