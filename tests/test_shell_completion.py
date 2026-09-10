"""Read-only Click shell-completion and installer regressions."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

main_module = importlib.import_module("amplifier_app_cli.main")
completion = importlib.import_module("amplifier_app_cli.utils.shell_completion")


def _values(items):
    return [item.value for item in items]


class _Settings:
    def __init__(self, merged=None, providers=None):
        self.merged = merged or {}
        self.providers = providers or []

    def get_merged_settings(self):
        return self.merged

    def get_provider_overrides(self):
        return self.providers


@pytest.fixture(autouse=True)
def isolated_completion_state(tmp_path, monkeypatch):
    """Every callback sees an empty scratch home and current project by default."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(completion, "get_amplifier_home", lambda: home)
    return home, project


def _context(params=None):
    ctx = click.Context(click.Command("test"))
    ctx.params = params or {}
    return ctx


def _parameter(command, name):
    return next(param for param in command.params if param.name == name)


def _custom_callback(command, name):
    return _parameter(command, name)._custom_shell_complete


def test_bundle_callback_uses_only_safe_local_sources_without_migration(
    isolated_completion_state, monkeypatch
):
    home, project = isolated_completion_state
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings(
            {"bundle": {"added": {"added": "uri", "bad value": "uri", 9: "uri"}}}
        ),
    )
    local = project / ".amplifier" / "bundles" / "local"
    local.mkdir(parents=True)
    (local / "bundle.yaml").write_text("name: local", encoding="utf-8")
    (home / "registry.json").write_text(
        json.dumps(
            {
                "bundles": {
                    "cached": {"uri": "file:///cached", "explicitly_requested": True},
                    "transitive": {
                        "uri": "file:///dependency",
                        "explicitly_requested": False,
                    },
                    "unsafe\nname": {
                        "uri": "file:///unsafe",
                        "explicitly_requested": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (home / "bundle-registry.yaml").write_text(
        "bundles:\n  legacy:\n    uri: file:///bundle\n", encoding="utf-8"
    )

    values = _values(completion.complete_bundle_names(_context(), None, ""))

    assert {"anchors", "added", "cached", "legacy", "local"} <= set(values)
    assert "transitive" not in values
    assert "bad value" not in values
    assert not (home / "bundle-registry.yaml.migrated").exists()


def test_removable_bundle_callback_excludes_defaults_and_respects_app_mode(monkeypatch):
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings(
            {
                "bundle": {
                    "added": {"added": "uri"},
                    "app": ["git+https://example.test/app-bundle@main"],
                }
            }
        ),
    )
    monkeypatch.setattr(completion, "_legacy_bundle_names", lambda: set())

    assert _values(
        completion.complete_removable_bundle_names(_context(), None, "")
    ) == ["added", "app-bundle"]
    assert _values(
        completion.complete_removable_bundle_names(_context({"app": True}), None, "")
    ) == ["app-bundle"]


@pytest.mark.parametrize("app_mode", [False, True])
@pytest.mark.parametrize(
    "source", ["my-bundle", "relative/path/my-bundle", "./my-bundle"]
)
def test_removable_local_app_bundle_names_are_suggested(source, app_mode, monkeypatch):
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings({"bundle": {"app": [source]}}),
    )
    assert _values(
        completion.complete_removable_bundle_names(
            _context({"app": app_mode}), None, "my-"
        )
    ) == ["my-bundle"]


@pytest.mark.parametrize(
    "source",
    [
        "relative/my-bundle?token=synthetic-secret",
        "//user:password@example.test/my-bundle",
        "//example.test/my-bundle",
    ],
)
def test_schemeless_app_sources_do_not_expose_query_or_authority(source):
    assert completion._extract_app_bundle_name(source) is None


def test_provider_callback_uses_execution_identity_without_config_values(monkeypatch):
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings(
            providers=[
                {"id": "named", "module": "provider-openai", "source": "secret-source"},
                {"module": "provider-anthropic", "config": {"api_key": "secret"}},
                {"id": "unsafe\tname", "module": "provider-ignored"},
                {"module": "not-a-provider"},
            ]
        ),
    )

    items = completion.complete_configured_provider_names(_context(), None, "")

    assert _values(items) == ["anthropic", "named"]
    assert all(item.help == "Configured provider" for item in items)
    assert "secret" not in repr(items)
    assert "secret-source" not in repr(items)


def test_callbacks_handle_absent_and_malformed_local_state(
    isolated_completion_state, monkeypatch
):
    home, _project = isolated_completion_state
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings({"bundle": []}, providers=[None, {"id": 3}]),
    )
    (home / "registry.json").write_text("not json", encoding="utf-8")
    (home / "bundle-registry.yaml").write_text("bundles: [not-a-map]", encoding="utf-8")

    assert completion.complete_bundle_names(_context(), None, "missing-") == []
    assert completion.complete_configured_provider_names(_context(), None, "") == []
    assert completion.complete_current_project_session_ids(_context(), None, "") == []


def test_session_callback_ignores_an_unreadable_sessions_directory(
    isolated_completion_state, monkeypatch
):
    home, _project = isolated_completion_state
    sessions = home / "projects" / completion.get_project_slug() / "sessions"
    sessions.mkdir(parents=True)

    def unreadable_iterdir(path):
        if path == sessions:
            raise PermissionError
        return original_iterdir(path)

    original_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", unreadable_iterdir)

    assert completion.complete_current_project_session_ids(_context(), None, "") == []


def test_session_callback_is_project_scoped_root_only_and_prefix_capped(
    isolated_completion_state,
):
    home, _project = isolated_completion_state
    sessions = home / "projects" / completion.get_project_slug() / "sessions"
    sessions.mkdir(parents=True)
    for index in range(101):
        (sessions / f"match-{index:03d}").mkdir()
    (sessions / "match-child_agent").mkdir()
    (sessions / ".match-hidden").mkdir()
    (sessions / "other").mkdir()

    values = _values(
        completion.complete_current_project_session_ids(_context(), None, "match-")
    )

    assert len(values) == 100
    assert all(value.startswith("match-") for value in values)
    assert "match-child_agent" not in values


def test_callbacks_never_construct_mutating_services(monkeypatch):
    class Forbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError("must not be constructed during completion")

    monkeypatch.setattr(completion, "AppSettings", lambda: _Settings())
    monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", Forbidden)
    monkeypatch.setattr("amplifier_app_cli.key_manager.KeyManager", Forbidden)

    assert completion.complete_bundle_names(_context(), None, "")
    assert completion.complete_configured_provider_names(_context(), None, "") == []
    assert completion.complete_current_project_session_ids(_context(), None, "") == []


@pytest.mark.parametrize(
    "kind, words, expected_value",
    [
        ("bundle", "amplifier run --bundle tea", "team"),
        ("provider", "amplifier run --provider na", "named"),
        ("session", "amplifier run --resume se", "session-1"),
    ],
)
@pytest.mark.parametrize(
    ("shell", "cword", "expected"),
    [
        ("bash", "3", "plain,{value}\n"),
        ("zsh", "3", "plain\n{value}\n{help}\n"),
        ("fish", "{incomplete}", "plain,{value}\t{help}\n"),
    ],
)
def test_actual_cli_protocol_emits_native_dynamic_records(
    kind,
    words,
    expected_value,
    shell,
    cword,
    expected,
    isolated_completion_state,
    monkeypatch,
):
    home, _project = isolated_completion_state
    sessions = (
        home / "projects" / completion.get_project_slug() / "sessions" / "session-1"
    )
    sessions.mkdir(parents=True)
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings(
            {"bundle": {"added": {"team": "uri"}}},
            providers=[{"id": "named", "module": "provider-openai"}],
        ),
    )

    result = CliRunner().invoke(
        main_module.cli,
        [],
        prog_name="amplifier",
        env={
            "_AMPLIFIER_COMPLETE": f"{shell}_complete",
            "COMP_WORDS": words,
            "COMP_CWORD": cword.format(incomplete=words.rsplit(" ", 1)[-1]),
        },
    )

    assert result.exit_code == 0
    assert result.output == expected.format(
        value=expected_value,
        help={
            "bundle": "Available bundle",
            "provider": "Configured provider",
            "session": "Current-project session",
        }[kind],
    )


@pytest.mark.parametrize(
    ("words", "cword", "expected_records"),
    [
        ("amplifier bun", "1", {"plain,bundle"}),
        (
            "amplifier run --",
            "2",
            {
                "plain,--bundle",
                "plain,--help",
                "plain,--max-tokens",
                "plain,--mode",
                "plain,--model",
                "plain,--output-format",
                "plain,--provider",
                "plain,--resume",
                "plain,--verbose",
            },
        ),
        ("amplifier run --mode ", "3", {"plain,chat", "plain,single"}),
    ],
)
def test_bash_protocol_retains_static_command_flag_and_choice_completion(
    words, cword, expected_records
):
    result = CliRunner().invoke(
        main_module.cli,
        [],
        prog_name="amplifier",
        env={
            "_AMPLIFIER_COMPLETE": "bash_complete",
            "COMP_WORDS": words,
            "COMP_CWORD": cword,
        },
    )

    assert result.exit_code == 0
    assert set(result.output.splitlines()) == expected_records
    assert len(result.output.splitlines()) == len(expected_records)


def test_wiring_uses_custom_callbacks_for_all_semantic_parameters():
    cli = main_module.cli
    assert (
        _custom_callback(cli.commands["run"], "bundle")
        is completion.complete_bundle_names
    )
    assert (
        _custom_callback(cli.commands["run"], "provider")
        is completion.complete_configured_provider_names
    )
    assert (
        _custom_callback(cli.commands["run"], "resume")
        is completion.complete_current_project_session_ids
    )

    bundle = cli.commands["bundle"]
    for name in ("show", "use", "update"):
        assert (
            _custom_callback(bundle.commands[name], "name")
            is completion.complete_bundle_names
        )
    assert (
        _custom_callback(bundle.commands["remove"], "name")
        is completion.complete_removable_bundle_names
    )

    for name in ("list", "info", "invoke"):
        assert (
            _custom_callback(cli.commands["tool"].commands[name], "bundle")
            is completion.complete_bundle_names
        )
    assert (
        _custom_callback(cli.commands["agents"].commands["list"], "bundle")
        is completion.complete_bundle_names
    )

    session = cli.commands["session"]
    for name in ("show", "fork", "delete", "resume"):
        assert (
            _custom_callback(session.commands[name], "session_id")
            is completion.complete_current_project_session_ids
        )
    assert (
        _custom_callback(session.commands["list"], "tree_session")
        is completion.complete_current_project_session_ids
    )
    for command in (
        cli.commands["continue"],
        session.commands["resume"],
        cli.commands["resume"],
    ):
        assert (
            _custom_callback(command, "force_bundle")
            is completion.complete_bundle_names
        )
    assert (
        _custom_callback(cli.commands["resume"], "session_id")
        is completion.complete_current_project_session_ids
    )

    providers = cli.commands["provider"]
    for name, parameter_name in (
        ("remove", "name"),
        ("edit", "name"),
        ("test", "name"),
        ("models", "provider_id"),
    ):
        assert (
            _custom_callback(providers.commands[name], parameter_name)
            is completion.complete_configured_provider_names
        )
    for name, parameter_name in (
        ("add", "provider_type"),
        ("install", "provider_ids"),
        ("login", "provider_id"),
    ):
        assert _custom_callback(providers.commands[name], parameter_name) is None


def test_freeform_dynamic_values_remain_unconstrained_by_resilient_parse():
    context = main_module.cli.make_context(
        "amplifier",
        ["run", "--bundle", "manual-bundle", "--provider", "manual-provider"],
        resilient_parsing=True,
    )
    command = main_module.cli.commands["run"]
    run_context = command.make_context(
        "run",
        ["--bundle", "manual-bundle", "--provider", "manual-provider"],
        parent=context,
        resilient_parsing=True,
    )
    assert run_context.params["bundle"] == "manual-bundle"
    assert run_context.params["provider"] == "manual-provider"


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_installer_writes_real_click_source_once_and_preserves_rc_content(
    tmp_path, monkeypatch, shell
):
    config = tmp_path / ".config" / shell / "amplifier.rc"
    if shell != "fish":
        config.parent.mkdir(parents=True)
        config.write_text("# keep this\n", encoding="utf-8")
    monkeypatch.setattr(
        click.shell_completion.BashComplete,
        "_check_version",
        staticmethod(lambda: None),
    )

    assert completion.install_completion_to_config(config, shell, main_module.cli)
    first = config.read_text(encoding="utf-8")
    assert completion.install_completion_to_config(config, shell, main_module.cli)
    assert config.read_text(encoding="utf-8") == first
    assert completion.MANAGED_MARKER in first
    instruction = "complete" if shell == "fish" else "source"
    assert f"_AMPLIFIER_COMPLETE={shell}_{instruction}" in first
    if shell != "fish":
        assert "# keep this" in first


def test_installer_recognizes_legacy_and_preserves_protected_fish(tmp_path):
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'eval "$(_AMPLIFIER_COMPLETE=zsh_source amplifier)"\n', encoding="utf-8"
    )
    fish = tmp_path / "amplifier.fish"
    fish.write_text(
        'function _amplifier_completion\nend\ncomplete --no-files --command amplifier --arguments "(_amplifier_completion)"\n',
        encoding="utf-8",
    )
    protected = tmp_path / "protected.fish"
    protected.write_text("complete --command other\n", encoding="utf-8")

    assert completion.is_completion_installed(zshrc, "zsh")
    assert completion.is_completion_installed(fish, "fish")
    assert not completion.install_completion_to_config(
        protected, "fish", main_module.cli
    )
    assert protected.read_text(encoding="utf-8") == "complete --command other\n"
    assert not completion.install_completion_to_config(
        tmp_path / "unsupported", "powershell", main_module.cli
    )


def test_fish_manual_fallback_is_non_overwriting_source_pipeline(tmp_path):
    assert (
        completion.manual_installation_instruction(
            "fish", tmp_path / "a path" / "amplifier.fish"
        )
        == "_AMPLIFIER_COMPLETE=fish_source amplifier | source"
    )


def test_main_completion_instruction_skips_normal_startup(monkeypatch):
    calls = []
    monkeypatch.setenv("_AMPLIFIER_COMPLETE", "bash_source")
    monkeypatch.setattr(main_module, "cli", lambda: calls.append("cli"))
    monkeypatch.setattr(
        main_module, "_guard_shared_venv_home", lambda: calls.append("guard")
    )
    monkeypatch.setattr(
        main_module, "_activate_home_env", lambda: calls.append("activate")
    )

    main_module.main()

    assert calls == ["cli"]


def test_invalid_completion_instruction_does_not_execute_cli_or_bypass_guard(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv("_AMPLIFIER_COMPLETE", "invalid_complete")
    monkeypatch.setattr(main_module, "cli", lambda: calls.append("cli"))
    monkeypatch.setattr(
        main_module, "_guard_shared_venv_home", lambda: calls.append("guard")
    )

    with pytest.raises(click.exceptions.Exit):
        main_module.main()

    assert calls == []


@pytest.mark.parametrize(
    ("shell", "original"),
    [
        ("bash", b"# preserve user configuration\n"),
        ("bash", b"# preserve user configuration\r\n"),
        ("zsh", b"# preserve user configuration\n"),
        ("zsh", b"# preserve user configuration\r\n"),
        ("fish", b""),
    ],
)
def test_install_flag_repeats_without_modifying_other_content(
    shell, original, isolated_completion_state, monkeypatch
):
    home, _ = isolated_completion_state
    monkeypatch.setenv("SHELL", f"/bin/{shell}")
    config = completion.get_shell_config_file(shell)
    if shell != "fish":
        config.write_bytes(original)
    runner = CliRunner()
    first = runner.invoke(main_module.cli, ["--install-completion"])
    assert first.exit_code == 0, first.output
    assert config.is_file()
    first_bytes = config.read_bytes()
    second = runner.invoke(main_module.cli, ["--install-completion"])
    assert second.exit_code == 0, second.output
    assert "already configured" in second.output
    assert config.read_bytes() == first_bytes
    if shell != "fish":
        assert first_bytes.startswith(original)
    assert home.is_dir()


def test_install_flag_preserves_custom_fish_and_prints_safe_fallback(
    isolated_completion_state, monkeypatch
):
    monkeypatch.setenv("SHELL", "/bin/fish")
    config = completion.get_shell_config_file("fish")
    config.parent.mkdir(parents=True)
    original = b"# my custom completions\ncomplete --command amplifier -a custom\n"
    config.write_bytes(original)
    result = CliRunner().invoke(main_module.cli, ["--install-completion"])
    assert result.exit_code == 1
    assert "| source" in result.output
    assert "current shell" in result.output
    assert " > " not in result.output
    assert config.read_bytes() == original


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_marker_alone_is_not_installed(tmp_path, shell):
    config = tmp_path / "rc"
    config.write_text(
        f"{completion.MANAGED_MARKER}\n# _AMPLIFIER_COMPLETE={shell}_source\n",
        encoding="utf-8",
    )
    assert not completion.is_completion_installed(config, shell)


@pytest.mark.parametrize(
    "unsafe",
    [
        "bad,name",
        "bad\nname",
        "bad\tname",
        "bad name",
        "*",
        "foo?",
        "[a-z]",
        "foo;bar",
        "$(command)",
        "foo`bar",
        "foo#secret",
        "foo%3Fsecret",
        "../name",
        "name/child",
        "name\\child",
        "hidden\u202ename",
    ],
)
def test_unsafe_candidate_values_are_never_emitted(unsafe):
    assert (
        completion._completion_items({unsafe, "safe-name"}, "", "bundle")[0].value
        == "safe-name"
    )
    assert len(completion._completion_items({unsafe, "safe-name"}, "", "bundle")) == 1


def test_malformed_records_and_unresolvable_yml_are_not_candidates(
    isolated_completion_state, monkeypatch
):
    home, project = isolated_completion_state
    monkeypatch.setattr(
        completion,
        "AppSettings",
        lambda: _Settings(
            {
                "bundle": {
                    "added": {"broken": None, "blank": "", "valid": "file:///valid"}
                }
            },
            providers=[
                {"id": "broken"},
                {"id": "wrong", "module": "tool-bash"},
                {"id": "blank", "module": "provider-"},
                {"id": "valid-provider", "module": "provider-openai"},
            ],
        ),
    )
    (home / "registry.json").write_text(
        json.dumps(
            {
                "bundles": {
                    "bad-cache": {"explicitly_requested": True},
                    "valid-cache": {
                        "uri": "file:///valid-cache",
                        "explicitly_requested": True,
                    },
                }
            }
        )
    )
    bundles = project / ".amplifier" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "unresolvable.yml").write_text("bundle: {}")
    assert _values(completion.complete_bundle_names(_context(), None, "valid")) == [
        "valid",
        "valid-cache",
    ]
    for prefix in ("broken", "blank", "bad-cache", "unresolvable"):
        assert completion.complete_bundle_names(_context(), None, prefix) == []
    assert _values(
        completion.complete_configured_provider_names(_context(), None, "")
    ) == ["valid-provider"]


@pytest.mark.parametrize(
    "uri",
    [
        "https://user:password@example.test/repo",
        "https://example.test/repo?token=synthetic-secret",
        "file:///repo#synthetic-secret",
        "ssh://user@example.test/repo",
        "https://[malformed/repo",
    ],
)
def test_app_bundle_uri_details_are_not_exposed(uri):
    assert completion._extract_app_bundle_name(uri) is None


def test_session_prefix_filter_precedes_limit_and_excludes_other_project(
    isolated_completion_state,
):
    import os

    home, _ = isolated_completion_state
    sessions = home / "projects" / completion.get_project_slug() / "sessions"
    for index in range(105):
        entry = sessions / f"new-{index:03}"
        entry.mkdir(parents=True)
        os.utime(entry, (2000 + index, 2000 + index))
    old = sessions / "old-matching-session"
    old.mkdir()
    os.utime(old, (1000, 1000))
    other_project = (
        home / "projects" / "different-project" / "sessions" / "old-foreign-session"
    )
    other_project.mkdir(parents=True)
    assert _values(
        completion.complete_current_project_session_ids(_context(), None, "old-")
    ) == ["old-matching-session"]
    assert (
        _values(completion.complete_current_project_session_ids(_context(), None, ""))[
            0
        ]
        == "new-104"
    )


def test_absent_home_stays_absent(tmp_path, monkeypatch):
    absent = tmp_path / "never-created"
    monkeypatch.setenv("AMPLIFIER_HOME", str(absent))
    monkeypatch.setattr(completion, "get_amplifier_home", lambda: absent)
    completion.complete_bundle_names(_context(), None, "")
    completion.complete_configured_provider_names(_context(), None, "")
    completion.complete_current_project_session_ids(_context(), None, "")
    assert not absent.exists()


def test_normal_main_keeps_ownership_guard_before_activation_and_dispatch(monkeypatch):
    calls = []
    monkeypatch.delenv("_AMPLIFIER_COMPLETE", raising=False)
    for name in (
        "_ensure_utf8_output",
        "_configure_console_logging",
        "_attach_llm_error_filter",
    ):
        monkeypatch.setattr(main_module, name, lambda: None)
    monkeypatch.setattr(
        main_module, "_guard_shared_venv_home", lambda: calls.append("guard")
    )
    monkeypatch.setattr(
        main_module, "_activate_home_env", lambda: calls.append("activate")
    )
    monkeypatch.setattr(main_module, "cli", lambda: calls.append("cli"))
    main_module.main()
    assert calls == ["guard", "activate", "cli"]


def test_bash_fresh_home_uses_bashrc_and_keeps_existing_profile_only_setup(
    isolated_completion_state,
):
    home, _ = isolated_completion_state
    assert completion.get_shell_config_file("bash") == home / ".bashrc"
    (home / ".bash_profile").write_text("# existing login setup\n")
    assert completion.get_shell_config_file("bash") == home / ".bash_profile"
    (home / ".bashrc").write_text("# existing interactive setup\n")
    assert completion.get_shell_config_file("bash") == home / ".bashrc"


@pytest.mark.parametrize(
    "module_name",
    [
        "amplifier_app_cli.key_manager",
        "amplifier_app_cli.lib.settings",
        "amplifier_app_cli.utils.settings_manager",
    ],
)
def test_read_only_module_import_does_not_load_locking_dependency(
    module_name, monkeypatch
):
    import builtins
    import sys
    import types

    module = importlib.import_module(module_name)
    code = compile(
        Path(module.__file__).read_text(encoding="utf-8"), module.__file__, "exec"
    )
    probe = types.ModuleType(module_name + "_completion_probe")
    probe.__package__ = module_name.rpartition(".")[0]
    monkeypatch.setitem(sys.modules, probe.__name__, probe)
    imports = []
    real_import = builtins.__import__

    def forbid_filelock(name, *args, **kwargs):
        if name == "filelock" or name.startswith("filelock."):
            imports.append(name)
            raise AssertionError(
                "Read-only import loaded a filesystem-probing dependency"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid_filelock)
    exec(code, probe.__dict__)
    assert imports == []
