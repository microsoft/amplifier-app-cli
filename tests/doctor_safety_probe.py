"""Fresh-process instrumentation; also usable in an isolated wheel environment.

Install this file as sitecustomize.py before starting the published entry.
The test harness, not the doctor, owns that setup. All attempted forbidden
operations are recorded even when application code swallows their exceptions.
"""

import atexit
import json
import linecache  # noqa: F401 -- Python 3.13 registers -c source before executing its body.
import os
import runpy  # noqa: F401 -- Finish -m bootstrap before measuring app-owned activity.
import sys

violations = []
initial_bytecode = []
owned_started = False
initial_fd_pending = False


def deny(code):
    violations.append(code)
    raise RuntimeError("doctor_probe_denied")


def forbidden_import(name):
    return (
        name.startswith(("amplifier_foundation", "amplifier_core", "amplifier_module_"))
        or (name.startswith("amplifier_app_cli") and name not in (
            "amplifier_app_cli", "amplifier_app_cli.__main__", "amplifier_app_cli.main",
            "amplifier_app_cli.commands", "amplifier_app_cli.commands.doctor",
            "amplifier_app_cli.lib", "amplifier_app_cli.lib.diagnostics",
            "amplifier_app_cli.lib.venv_home_guard", "amplifier_app_cli.project_utils",
        ))
    )


class BoundaryFinder:
    def find_spec(self, fullname, path=None, target=None):
        if forbidden_import(fullname):
            deny("forbidden_import")


def audit(event, args):
    global owned_started, initial_fd_pending
    if event == "exec" and str(args[0].co_filename).replace("\\", "/").endswith("/amplifier_app_cli/__init__.py"):
        owned_started = True
    if event == "import":
        if forbidden_import(args[0]):
            deny("forbidden_import")
    if event.startswith(("socket.", "subprocess.")) or event in (
        "os.system", "os.posix_spawn", "os.fork", "pty.spawn",
    ):
        deny("network_or_subprocess")
    if event == "open":
        path, mode, flags = args
        path = str(path).replace("\\", "/")
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if writing:
            if not owned_started and "/amplifier_app_cli/__pycache__/__init__." in path:
                initial_bytecode.append("initial_package_bytecode")
                initial_fd_pending = True
            elif not owned_started and isinstance(args[0], int) and initial_fd_pending:
                initial_bytecode.append("initial_package_bytecode")
                initial_fd_pending = False
            else:
                deny("write")
        elif any(piece in path.split("/") for piece in (
            ".env", ".netrc", "keys.env", "keys.yaml", "keys.json", "credentials", "credentials.json",
            "transcript.jsonl", "metadata.json",
        )):
            deny("credential_or_session_read")
    if event in (
        "os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.link", "os.symlink",
        "os.chmod", "os.chown", "os.truncate", "tempfile.mkstemp", "tempfile.mkdtemp",
    ):
        path = str(args[0]).replace("\\", "/") if args else ""
        if (
            event in ("os.mkdir", "os.rename")
            and "/amplifier_app_cli/__pycache__" in path
            and not owned_started
        ):
            initial_bytecode.append("initial_package_bytecode")
        else:
            deny("filesystem_mutation")


original_getitem = os._Environ.__getitem__


def environ_getitem(self, key):
    if any(word in str(key).upper() for word in ("API_KEY", "PASSWORD", "TOKEN", "CREDENTIAL")):
        deny("credential_environment_read")
    if str(key).startswith("AMPLIFIER_MODULE_") or key in ("AMPLIFIER_GIT_HOST", "AMPLIFIER_HOME_ENV"):
        deny("environment_channel_value_read")
    return original_getitem(self, key)


def environ_contains(self, key):
    # Presence checks are allowed; unlike __getitem__, they return no value.
    try:
        original_getitem(self, key)
        return True
    except KeyError:
        return False


os._Environ.__getitem__ = environ_getitem
os._Environ.__contains__ = environ_contains
sys.meta_path.insert(0, BoundaryFinder())
sys.addaudithook(audit)


def finish():
    sys.stderr.write("DOCTOR_AUDIT:" + json.dumps({
        "violations": violations,
        "initial_bytecode": initial_bytecode,
        "bytecode_suppressed": sys.dont_write_bytecode,
        "heavy_loaded": any(n.startswith(("amplifier_foundation", "amplifier_core")) for n in sys.modules),
    }) + "\n")


atexit.register(finish)