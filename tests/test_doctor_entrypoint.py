"""Exercise installed public entries in fresh interpreters, not main mocks."""

import ast
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROBE = Path(__file__).with_name("doctor_safety_probe.py")


def test_bytecode_switch_precedes_nonbootstrap_package_imports():
    # A preloaded audit helper can mask stdlib bytecode attempts. Keep the
    # package-owned switch before imports other than the bootstrap sys module.
    package = Path(__file__).resolve().parents[1] / "amplifier_app_cli" / "__init__.py"
    nodes = ast.parse(package.read_text(encoding="utf-8")).body
    assert isinstance(nodes[0], ast.Expr)  # Module docstring.
    assert isinstance(nodes[1], ast.Import)
    assert [alias.name for alias in nodes[1].names] == ["sys"]
    assert isinstance(nodes[2], ast.If)
    assert ast.unparse(nodes[2].body[0]) == "sys.dont_write_bytecode = True"


def _entry(kind):
    if kind == "module":
        return [sys.executable, "-m", "amplifier_app_cli"]
    if kind == "legacy_module":
        return [sys.executable, "-m", "amplifier_app_cli.main"]
    launcher = Path(sys.executable).parent / ("amplifier.exe" if os.name == "nt" else "amplifier")
    assert launcher.exists(), "test requires the installed project console entry"
    return [str(launcher)]


def _run(tmp_path, kind, args, *, settings=None):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    app_home = home / "absent-amplifier"
    if settings is not None:
        app_home.mkdir()
        (app_home / "settings.yaml").write_text(settings, encoding="utf-8")
    instrumentation = tmp_path / "instrumentation"
    instrumentation.mkdir()
    (instrumentation / "sitecustomize.py").write_bytes(PROBE.read_bytes())
    # This contains only instrumentation, not a source checkout/dependency override.
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AMPLIFIER_", "_AMPLIFIER_"))
        and key not in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE")
    }
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "AMPLIFIER_HOME": str(app_home),
        "PYTHONPATH": str(instrumentation),
        "SYNTHETIC_API_KEY": "synthetic-secret-never-read",
        "AMPLIFIER_HOME_ENV": "1", "AMPLIFIER_GIT_HOST": "synthetic-private-value",
        "AMPLIFIER_MODULE_SYNTHETIC": "synthetic-private-value",
    })
    before = sorted(str(p.relative_to(project)) for p in project.rglob("*"))
    result = subprocess.run(_entry(kind) + ["doctor", *args], env=env, cwd=project, text=True, capture_output=True, timeout=30)
    assert sorted(str(p.relative_to(project)) for p in project.rglob("*")) == before
    assert app_home.exists() == (settings is not None)
    lines = result.stderr.splitlines()
    audit = json.loads(next(line.removeprefix("DOCTOR_AUDIT:") for line in lines if line.startswith("DOCTOR_AUDIT:")))
    assert audit["violations"] == [], result.stderr
    assert audit["bytecode_suppressed"] is True
    assert audit["heavy_loaded"] is False
    assert "synthetic-private-value" not in result.stdout + result.stderr
    return result, [line for line in lines if not line.startswith("DOCTOR_AUDIT:")]


@pytest.mark.parametrize("kind", ["console", "module", "legacy_module"])
@pytest.mark.parametrize("args,code", [
    (["--format", "json"], 0),
    ([], 0),
    (["--verbose"], 0),
    (["--format", "text", "--verbose"], 0),
    (["--format", "json", "--verbose"], 0),
    (["--help"], 0),
    (["--verbose", "--help"], 0),
    (["--session", "synthetic_session-01", "--format", "json"], 0),
    (["--format", "synthetic-secret"], 2),
    (["--format", "\x1b[31msynthetic-secret"], 2),
    (["--unknown=synthetic-secret"], 2),
    (["--session", "../synthetic-secret"], 2),
    (["--session"], 2),
    (["synthetic-secret"], 2),
    (["--verbose=synthetic-secret"], 2),
    (["--verbose", "--format", "synthetic-secret"], 2),
], ids=[
    "json", "summary", "verbose", "text-verbose", "json-verbose", "help", "verbose-help",
    "session-json", "bad-format", "control-format", "unknown", "bad-session",
    "missing-session", "extra", "verbose-value", "verbose-bad-format",
])
def test_published_entries_are_passive(tmp_path, kind, args, code):
    result, errors = _run(tmp_path, kind, args)
    assert result.returncode == code, result.stdout + result.stderr
    assert "synthetic-secret" not in result.stdout + result.stderr
    if code == 2:
        assert result.stdout == ""
        assert errors == [
            "Usage: amplifier doctor [--format text|json] [--session SESSION_ID] [--verbose]",
            "Error: invalid_doctor_arguments",
        ]
    else:
        assert errors == []
    if "json" in args:
        assert result.stdout.count("\n") == 1
        assert json.loads(result.stdout)["collection_status"] == "complete"
    elif code == 0 and "--help" in args:
        assert "--verbose" in result.stdout
        assert "Show technical details (text only; does not affect JSON)." in " ".join(result.stdout.split())
    elif code == 0 and "--verbose" in args:
        assert result.stdout.startswith("Amplifier doctor (passive)\n")
        assert "Collection: complete (not a health verdict)" in result.stdout
    elif code == 0:
        assert result.stdout.startswith("Amplifier doctor\nConfiguration report collected\n")
        assert str(tmp_path) not in result.stdout


@pytest.mark.parametrize("kind", ["console", "module", "legacy_module"])
@pytest.mark.parametrize("args", [
    ["--format", "json"], [], ["--verbose"], ["--format", "text", "--verbose"],
    ["--format", "json", "--verbose"],
], ids=["json", "summary", "verbose", "text-verbose", "json-verbose"])
def test_real_partial_entry_returns_one(tmp_path, kind, args):
    result, errors = _run(tmp_path, kind, args, settings="bundle: [synthetic-secret")
    assert result.returncode == 1
    if "json" in args:
        assert json.loads(result.stdout)["collection_status"] == "partial"
    elif "--verbose" in args:
        assert "Collection: partial (not a health verdict)" in result.stdout
        assert "global: invalid_yaml " in result.stdout
    else:
        assert result.stdout.startswith("Amplifier doctor\nConfiguration report incomplete\n")
        assert "User settings (global): Could not parse format or encoding" in result.stdout
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert errors == []


def test_console_metadata_points_to_dispatcher():
    dist = metadata.distribution("amplifier-app-cli")
    entry = next(e for e in dist.entry_points if e.group == "console_scripts" and e.name == "amplifier")
    assert entry.value == "amplifier_app_cli.__main__:main"


def test_importing_legacy_main_on_doctor_route_does_not_dispatch(tmp_path):
    instrumentation = tmp_path / "instrumentation"
    instrumentation.mkdir()
    (instrumentation / "sitecustomize.py").write_bytes(PROBE.read_bytes())
    env = dict(os.environ, PYTHONPATH=str(instrumentation))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    code = """
import sys
sys.argv = ['amplifier', 'doctor', '--format', 'json']
module = __import__('amplifier_app_cli.main', fromlist=['main'])
assert module.__name__ == 'amplifier_app_cli.main'
assert module.__file__.endswith('main.py')
assert callable(module.main)
assert 'amplifier_app_cli.commands.doctor' not in sys.modules
assert 'amplifier_app_cli.lib.diagnostics' not in sys.modules
assert not hasattr(module, 'KeyManager')
assert not hasattr(module, '_llm_error_filter')
assert not hasattr(module, 'unknown_export')
import amplifier_app_cli as package
import amplifier_app_cli.commands as commands
from amplifier_app_cli import main
assert main is module.main
assert package.main is main
assert not module._cli_ready
assert not hasattr(package, 'unknown_export')
assert not hasattr(commands, 'unknown_export')
assert package.__all__ == ['cli', 'main', '__version__']
assert 'register_run_command' in commands.__all__
assert not module._cli_ready
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("DOCTOR_AUDIT:")
    audit = json.loads(lines[0].removeprefix("DOCTOR_AUDIT:"))
    assert audit["violations"] == []
    assert audit["bytecode_suppressed"] and not audit["heavy_loaded"]


def test_probe_records_attempts_even_when_exceptions_are_swallowed(tmp_path):
    code = """
import importlib, os, runpy, socket, subprocess, sys, tempfile
tempfile.tempdir = os.getcwd()
runpy.run_path(sys.argv[1])
attempts = (
    lambda: open('synthetic-write', 'w'),
    lambda: os.mkdir('synthetic-directory'),
    lambda: tempfile.mkstemp(),
    lambda: socket.socket(),
    lambda: subprocess.run([sys.executable, '-c', 'pass']),
    lambda: importlib.import_module('amplifier_foundation'),
    lambda: open('.env'),
    lambda: open('keys.env'),
    lambda: os.environ.get('SYNTHETIC_API_KEY'),
    lambda: os.environ.get('AMPLIFIER_GIT_HOST'),
)
for attempt in attempts:
    try:
        attempt()
    except Exception:
        pass
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(PROBE)], cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    line = next(line for line in result.stderr.splitlines() if line.startswith("DOCTOR_AUDIT:"))
    audit = json.loads(line.removeprefix("DOCTOR_AUDIT:"))
    assert set(audit["violations"]) == {
        "write", "filesystem_mutation", "network_or_subprocess", "forbidden_import",
        "credential_or_session_read", "credential_environment_read",
        "environment_channel_value_read",
    }
    assert audit["violations"].count("credential_or_session_read") == 2
    assert not list(tmp_path.iterdir())


def _run_import_check(tmp_path, code, *args):
    home = tmp_path / "home"
    home.mkdir()
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("AMPLIFIER_", "_AMPLIFIER_")) and name != "PYTHONPATH"
    }
    env.update(HOME=str(home), USERPROFILE=str(home), AMPLIFIER_HOME=str(home / "app"))
    result = subprocess.run(
        [sys.executable, "-c", code, *args], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_ordinary_click_group_uses_same_doctor_flag_and_json_contract(tmp_path):
    code = """
import importlib
import json
import os
from click.testing import CliRunner
from amplifier_app_cli import cli
from amplifier_app_cli.lib import diagnostics
module = importlib.import_module('amplifier_app_cli.commands.doctor')
assert cli.commands['doctor'] is module.doctor
assert next(p for p in module.doctor.params if p.name == 'verbose').is_flag
runner = CliRunner()
for status, exit_code in (('complete', 0), ('partial', 1), ('refused', 1)):
    report = diagnostics.unavailable_report()
    report['collection_status'] = status
    module.collect_diagnostics = lambda target: report
    expected = (json.dumps(report, ensure_ascii=True) + os.linesep).encode('utf-8')
    outputs = []
    for args in (['--format', 'json'], ['--format', 'json', '--verbose']):
        result = runner.invoke(cli, ['doctor', *args])
        assert result.exit_code == exit_code, result.output
        assert result.stdout_bytes == expected
        outputs.append(result.stdout_bytes)
    assert outputs[0] == outputs[1]
    result = runner.invoke(cli, ['doctor', '--verbose'])
    assert result.exit_code == exit_code, result.output
    assert result.output == diagnostics.render_text(report) + '\\n'
"""
    _run_import_check(tmp_path, code)


@pytest.mark.parametrize("route", ["ordinary", "foreign_doctor"])
@pytest.mark.parametrize("order", ["package_first", "main_first", "module_cli", "command_first", "submodule_first"])
def test_ordinary_exports_and_command_identity(order, route, tmp_path):
    code = """
import importlib
import sys
order, route = sys.argv[1:]
sys.argv = ['foreign-program', 'doctor'] if route == 'foreign_doctor' else ['foreign-program']
if order in ('main_first', 'module_cli'):
    module = importlib.import_module('amplifier_app_cli.main')
    if order == 'module_cli':
        import click
        assert isinstance(module.cli, click.Group)
elif order == 'command_first':
    from amplifier_app_cli.commands import register_run_command
elif order == 'submodule_first':
    for name in ('provider', 'module', 'tool', 'source'):
        importlib.import_module('amplifier_app_cli.commands.' + name)
import amplifier_app_cli as package
from amplifier_app_cli import cli, main
module = importlib.import_module('amplifier_app_cli.main')
import click
import amplifier_app_cli.commands as commands
from amplifier_app_cli.commands import auto_init_from_env, register_run_command
from amplifier_app_cli.commands.doctor import doctor
assert callable(main)
assert package.main is main is module.main
assert package.cli is cli is module.cli
assert isinstance(cli, click.Group) and cli.name == 'cli'
assert module.cli.commands['doctor'] is doctor
assert callable(auto_init_from_env) and callable(register_run_command)
assert package.__all__ == ['cli', 'main', '__version__']
expected = {
    'auto_init_from_env': 'init', 'check_first_run': 'init', 'init_cmd': 'init',
    'prompt_first_run_init': 'init', 'module': 'module', 'provider': 'provider',
    'register_run_command': 'run', 'register_session_commands': 'session',
    'source': 'source', 'tool': 'tool',
}
assert set(commands.__all__) == set(expected)
for name, origin in expected.items():
    child = importlib.import_module('amplifier_app_cli.commands.' + origin)
    assert getattr(commands, name) is getattr(child, name), name
assert 'amplifier_app_cli.truststore_shim' in sys.modules
params = tuple(cli.params)
registered = dict(cli.commands)
run = module._run_command
error_filter = module._llm_error_filter
def no_keys():
    raise AssertionError('repeat initialization loaded keys')
module.KeyManager = no_keys
for _ in range(3):
    module._initialize_cli()
    from amplifier_app_cli import cli as again, main as dispatch
    assert again is cli and dispatch is main
assert tuple(cli.params) == params
assert len({param.name for param in params}) == len(params) == 2
assert cli.commands == registered
assert module._run_command is run
assert module._llm_error_filter is error_filter
"""
    _run_import_check(tmp_path, code, order, route)


@pytest.mark.parametrize("route", ["ordinary", "foreign_doctor"])
def test_submodule_first_dispatcher_only_preserves_package_identity(route, tmp_path):
    code = """
import importlib
import sys
from types import ModuleType
route = sys.argv[1]
sys.argv = ['foreign-program', 'doctor'] if route == 'foreign_doctor' else ['foreign-program']
child = importlib.import_module('amplifier_app_cli.main')
package = sys.modules['amplifier_app_cli']
spec, path, exports = package.__spec__, package.__path__, package.__all__
if route == 'foreign_doctor':
    assert package.__dict__['main'] is child
from amplifier_app_cli import main
assert main is child.main
for _ in range(3):
    assert package.main is main
assert isinstance(package, ModuleType)
assert importlib.import_module('amplifier_app_cli') is package
assert sys.modules[package.__name__] is package
assert sys.modules[child.__name__] is child
assert package.__spec__ is spec and spec.name == 'amplifier_app_cli'
assert package.__path__ is path and spec.submodule_search_locations is path
assert package.__all__ is exports
assert exports == ['cli', 'main', '__version__']
sentinel = object()
package.main = sentinel
assert package.main is sentinel
package.main = child
assert package.main is main
if route == 'foreign_doctor':
    assert not child._cli_ready and not hasattr(child, 'KeyManager')
    assert 'amplifier_foundation' not in sys.modules
    assert 'amplifier_app_cli.truststore_shim' not in sys.modules
"""
    _run_import_check(tmp_path, code, route)


@pytest.mark.parametrize("route", ["ordinary", "foreign_doctor"])
@pytest.mark.parametrize("name", ["module", "provider", "source", "tool"])
def test_submodule_first_same_name_command_only(name, route, tmp_path):
    code = """
import importlib
import sys
from types import ModuleType
name, route = sys.argv[1:]
sys.argv = ['foreign-program', 'doctor'] if route == 'foreign_doctor' else ['foreign-program']
child = importlib.import_module('amplifier_app_cli.commands.' + name)
package = sys.modules['amplifier_app_cli.commands']
spec, path, exports = package.__spec__, package.__path__, package.__all__
if route == 'foreign_doctor':
    assert package.__dict__[name] is child
if name == 'module':
    from amplifier_app_cli.commands import module as exported
elif name == 'provider':
    from amplifier_app_cli.commands import provider as exported
elif name == 'source':
    from amplifier_app_cli.commands import source as exported
else:
    from amplifier_app_cli.commands import tool as exported
import click
assert isinstance(exported, click.Group)
assert exported is getattr(child, name)
for _ in range(3):
    package._load_exports()
    assert getattr(package, name) is exported
assert isinstance(package, ModuleType)
assert importlib.import_module('amplifier_app_cli.commands') is package
assert sys.modules[package.__name__] is package
assert sys.modules[child.__name__] is child
assert package.__spec__ is spec and spec.name == 'amplifier_app_cli.commands'
assert package.__path__ is path and spec.submodule_search_locations is path
assert package.__all__ is exports and name in exports
sentinel = object()
setattr(package, name, sentinel)
package._load_exports()
assert getattr(package, name) is sentinel
# Even when exports are already loaded, a child collision returns its command.
setattr(package, name, child)
assert getattr(package, name) is exported
assert getattr(package, name) is exported
if route == 'foreign_doctor':
    assert 'amplifier_app_cli.main' not in sys.modules
"""
    _run_import_check(tmp_path, code, name, route)


def test_concurrent_submodule_collision_reads_finish(tmp_path):
    code = """
import importlib
import sys
import threading
import time
sys.argv = ['foreign-program', 'doctor']
child = importlib.import_module('amplifier_app_cli.main')
command_child = importlib.import_module('amplifier_app_cli.commands.provider')
root = sys.modules['amplifier_app_cli']
commands = sys.modules['amplifier_app_cli.commands']
barrier = threading.Barrier(4)
errors = []
results = []
def read_exports():
    try:
        barrier.wait(timeout=10)
        for _ in range(3):
            assert root.main is child.main
            assert commands.provider is command_child.provider
        results.append(True)
    except BaseException as error:
        errors.append(repr(error))
threads = [threading.Thread(target=read_exports, daemon=True) for _ in range(4)]
for thread in threads:
    thread.start()
deadline = time.monotonic() + 20
for thread in threads:
    thread.join(max(0, deadline - time.monotonic()))
assert not any(thread.is_alive() for thread in threads), 'export reads deadlocked'
assert errors == [], errors
assert len(results) == len(threads)
assert not child._cli_ready
"""
    _run_import_check(tmp_path, code)


def test_package_dispatcher_alone_stays_passive(tmp_path):
    code = """
import sys
sys.argv = ['foreign-program', 'doctor']
from amplifier_app_cli import main
module = sys.modules['amplifier_app_cli.main']
assert main is module.main
assert not module._cli_ready
assert not hasattr(module, 'KeyManager')
assert not hasattr(module, 'unrecognized')
assert 'amplifier_foundation' not in sys.modules
assert 'amplifier_core' not in sys.modules
assert 'amplifier_app_cli.truststore_shim' not in sys.modules
assert 'amplifier_app_cli.commands.doctor' not in sys.modules
"""
    _run_import_check(tmp_path, code)


@pytest.mark.parametrize("completion", ["ordinary", "completion"])
def test_cached_passive_import_initializes_for_current_normal_argv(tmp_path, completion):
    code = """
import importlib
import os
import sys
mode = sys.argv[1]
sys.argv = ['foreign-program', 'doctor']
module = importlib.import_module('amplifier_app_cli.main')
import amplifier_app_cli as package
assert not module._cli_ready
events = []
key_calls = []
original_tls = package._ensure_tls_shim
def tls():
    events.append('tls')
    original_tls()
package._ensure_tls_shim = tls
class ImportOrder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('amplifier_core', 'amplifier_foundation', 'prompt_toolkit')):
            assert 'tls' in events, fullname
finder = ImportOrder()
sys.meta_path.insert(0, finder)
def profile(frame, event, arg):
    if (event == 'call' and frame.f_code.co_name == '__init__'
            and frame.f_globals.get('__name__') == 'amplifier_app_cli.key_manager'):
        key_calls.append('keys')
sys.setprofile(profile)
original_initialize = module._initialize_cli
groups = []
def initialize():
    original_initialize()
    groups.append(module.cli)
    events.append('initialized')
    module.cli = lambda: events.append('cli')
module._initialize_cli = initialize
for name, label in (
    ('_ensure_utf8_output', 'utf8'), ('_configure_console_logging', 'logging'),
    ('_attach_llm_error_filter', 'filter'), ('_guard_shared_venv_home', 'guard'),
    ('_activate_home_env', 'activate'),
):
    setattr(module, name, lambda label=label: events.append(label))
if mode == 'completion':
    os.environ['_AMPLIFIER_COMPLETE'] = 'bash_complete'
sys.argv = ['foreign-program', '--help']
module.main()
sys.setprofile(None)
sys.meta_path.remove(finder)
assert module._cli_ready
assert key_calls == ([] if mode == 'completion' else ['keys']), key_calls
expected = ['initialized', 'cli'] if mode == 'completion' else [
    'initialized', 'utf8', 'logging', 'filter', 'guard', 'activate', 'cli',
]
assert [event for event in events if event != 'tls'] == expected, events
import click
assert isinstance(groups[0], click.Group)
patched_cli = module.cli
original_initialize()
assert module.cli is patched_cli
assert module.interactive_chat.__globals__ is module.__dict__
assert module.execute_single.__globals__ is module.__dict__

# Once loaded, a later doctor dispatch must not rerun ordinary initialization.
doctor_module = importlib.import_module('amplifier_app_cli.commands.doctor')
doctor_module.main = lambda: 'passive'
def forbidden():
    raise AssertionError('doctor initialized ordinary CLI')
module._initialize_cli = forbidden
sys.argv = ['foreign-program', 'doctor']
assert module.main() == 'passive'
"""
    _run_import_check(tmp_path, code, completion)


@pytest.mark.parametrize("entry", ["amplifier_app_cli.__main__", "amplifier_app_cli.main"])
def test_cached_entry_suppresses_further_bytecode(entry, tmp_path):
    code = """
import importlib
import sys
entry_name = sys.argv[1]
sys.argv = ['foreign-program', 'doctor']
entry = importlib.import_module(entry_name)
doctor = importlib.import_module('amplifier_app_cli.commands.doctor')
doctor.main = lambda: 'passive'
sys.dont_write_bytecode = False
assert entry.main() == 'passive'
assert sys.dont_write_bytecode
assert 'amplifier_foundation' not in sys.modules
"""
    _run_import_check(tmp_path, code, entry)