"""Discovery and runtime must import one provider source, not mix wheel children."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _package(root: Path, name: str, marker: str) -> None:
    package = root / name
    package.mkdir(parents=True)
    (package / "_cost.py").write_text(f"ORIGIN = {marker!r}\n", encoding="utf-8")
    (package / "__init__.py").write_text(
        textwrap.dedent(
            """
            from types import SimpleNamespace
            from . import _cost
            __all__ = ["FixtureProvider"]
            class FixtureProvider:
                def get_info(self):
                    return SimpleNamespace(
                        id="origin-fixture", display_name=_cost.ORIGIN,
                        description="fixture provider", config_fields=[])
            def mount(*args, **kwargs):
                return None
            """
        ),
        encoding="utf-8",
    )
    (package / "entry.py").write_text("from . import mount\n", encoding="utf-8")


def _run(tmp_path: Path, body: str, *, configured: bool = True) -> dict:
    """Use fresh real imports; mock packages, not import machinery or guards."""
    wheel = tmp_path / "site-packages"
    selected = tmp_path / "selected-source"
    name = "amplifier_module_provider_origin_fixture"
    _package(wheel, name, "wheel")
    _package(selected, name, "selected")
    dist = wheel / "origin_fixture-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: origin-fixture\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist / "entry_points.txt").write_text(
        f"[amplifier.modules]\nprovider-origin-fixture = {name}.entry:mount\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    config = home / ".amplifier" / "settings.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "config:\n  providers:\n    - module: provider-origin-fixture\n"
        f"      source: {json.dumps(str(selected))}\n"
        if configured
        else "{}\n",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AMPLIFIER_", "PYTHON"))
        and "API_KEY" not in key
        and not key.endswith("_TOKEN")
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        AMPLIFIER_SKIP_STARTUP_UPDATE_CHECK="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    script = (
        "import importlib, json, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(wheel)!r})\n"
        f"name = {name!r}\n"
        f"selected = Path({str(selected)!r})\n"
        "def deny_network(event, args):\n"
        "    if event in {'socket.connect', 'socket.getaddrinfo'}:\n"
        "        raise PermissionError('No network in provider-origin fixture')\n"
        "sys.addaudithook(deny_network)\n" + textwrap.dedent(body)
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_installed_check_does_not_import_entry_point_parent(tmp_path):
    result = _run(
        tmp_path,
        """
        from amplifier_app_cli.provider_sources import is_provider_module_installed
        assert is_provider_module_installed("provider-origin-fixture")
        assert not any(n == name or n.startswith(name + ".") for n in sys.modules)
        print(json.dumps({"imported": False}))
        """,
    )
    assert result == {"imported": False}


def test_metadata_and_later_source_load_share_package_and_children(tmp_path):
    result = _run(
        tmp_path,
        """
        from amplifier_app_cli.provider_loader import get_provider_info
        from amplifier_app_cli.provider_loader import _load_provider_module_from_source_path
        info = get_provider_info("origin-fixture")
        assert info["display_name"] == "selected", info
        module = _load_provider_module_from_source_path("origin-fixture", selected)
        assert module._cost.ORIGIN == "selected"
        for n, m in tuple(sys.modules.items()):
            if n == name or n.startswith(name + "."):
                assert Path(m.__file__).resolve().is_relative_to(selected.resolve())
        assert get_provider_info("origin-fixture") == info
        print(json.dumps({"origin": module._cost.ORIGIN}))
        """,
    )
    assert result == {"origin": "selected"}


def test_unknown_provider_source_modules_is_used_before_installed_wheel(tmp_path):
    result = _run(
        tmp_path,
        """
        config = Path.home() / ".amplifier/settings.yaml"
        config.write_text("sources:\\n  modules:\\n    provider-origin-fixture: "
                          + json.dumps(str(selected)) + "\\n")
        from amplifier_app_cli.provider_loader import get_provider_info
        assert get_provider_info("origin-fixture")["display_name"] == "selected"
        print(json.dumps({"origin": "selected"}))
        """,
    )
    assert result == {"origin": "selected"}


@pytest.mark.parametrize("preload", ["package", "child"])
def test_wrong_preloaded_origin_is_rejected_not_evicted(tmp_path, preload):
    result = _run(
        tmp_path,
        f"""
        from amplifier_app_cli.provider_loader import get_provider_info
        if {preload!r} == "package":
            original = importlib.import_module(name)
        else:
            import importlib.util
            spec = importlib.util.spec_from_file_location(name + "._cost",
                Path(sys.path[0]) / name / "_cost.py")
            original = importlib.util.module_from_spec(spec)
            sys.modules[name + "._cost"] = original
            spec.loader.exec_module(original)
        before = original.__file__
        try:
            get_provider_info("origin-fixture")
        except ImportError:
            pass
        else:
            raise AssertionError("Wrong origin was silently accepted or hidden")
        assert original.__file__ == before
        assert original in sys.modules.values()
        print(json.dumps({{"rejected": True}}))
        """,
    )
    assert result == {"rejected": True}


def test_unavailable_selected_source_does_not_fall_back_to_wheel(tmp_path):
    result = _run(
        tmp_path,
        """
        import shutil
        shutil.rmtree(selected)
        from amplifier_app_cli.provider_loader import get_provider_info
        try:
            get_provider_info("origin-fixture")
        except ImportError:
            pass
        else:
            raise AssertionError("Unavailable selected source silently used a wheel")
        assert name not in sys.modules
        print(json.dumps({"rejected": True}))
        """,
    )
    assert result == {"rejected": True}


def test_installed_only_unknown_provider_remains_supported(tmp_path):
    result = _run(
        tmp_path,
        """
        from amplifier_app_cli.provider_loader import get_provider_info
        assert get_provider_info("origin-fixture")["display_name"] == "wheel"
        print(json.dumps({"origin": "wheel"}))
        """,
        configured=False,
    )
    assert result == {"origin": "wheel"}


def test_explicit_activated_path_wins_over_saved_source(tmp_path):
    result = _run(
        tmp_path,
        """
        from amplifier_app_cli.provider_loader import get_provider_info
        explicit = Path(sys.path[0])
        info = get_provider_info("origin-fixture", source_path=explicit)
        assert info["display_name"] == "wheel"
        print(json.dumps({"origin": info["display_name"]}))
        """,
    )
    assert result == {"origin": "wheel"}


def test_installed_check_without_entry_point_does_not_import(tmp_path):
    result = _run(
        tmp_path,
        """
        from unittest.mock import patch
        from amplifier_app_cli.provider_sources import is_provider_module_installed
        with patch("importlib.metadata.entry_points", return_value=[]):
            assert is_provider_module_installed("provider-origin-fixture")
        assert name not in sys.modules
        print(json.dumps({"imported": False}))
        """,
    )
    assert result == {"imported": False}


def test_picker_uses_selected_source_before_entry_point_import(tmp_path):
    result = _run(
        tmp_path,
        """
        from unittest.mock import patch
        from amplifier_app_cli.provider_manager import ProviderManager
        sources = {"provider-origin-fixture": str(selected)}
        with patch("amplifier_app_cli.provider_manager.get_effective_provider_sources",
                   return_value=sources):
            providers = ProviderManager().list_providers()
        assert ("provider-origin-fixture", "selected", "fixture provider") in providers
        assert sys.modules[name]._cost.ORIGIN == "selected"
        print(json.dumps({"origin": "selected"}))
        """,
    )
    assert result == {"origin": "selected"}


def test_provider_dependency_is_selected_before_dependent_import(tmp_path):
    result = _run(
        tmp_path,
        """
        from unittest.mock import patch
        dependency = "amplifier_module_provider_origin_base"
        for root, marker in [(Path(sys.path[0]), "wheel"), (selected, "selected")]:
            package = root / dependency
            package.mkdir()
            (package / "__init__.py").write_text("ORIGIN = " + repr(marker) + "\\n")
        path = selected / name / "__init__.py"
        path.write_text("from " + dependency + " import ORIGIN as DEPENDENCY\\n"
                        + path.read_text())
        config = Path.home() / ".amplifier/settings.yaml"
        config.write_text("sources:\\n  modules:\\n    provider-origin-fixture: "
                          + json.dumps(str(selected))
                          + "\\n    provider-origin-base: " + json.dumps(str(selected)) + "\\n")
        from amplifier_app_cli.provider_loader import get_provider_info
        with patch("amplifier_app_cli.provider_sources.PROVIDER_DEPENDENCIES",
                   {"provider-origin-fixture": ["provider-origin-base"]}):
            assert get_provider_info("origin-fixture")["display_name"] == "selected"
        assert sys.modules[name].DEPENDENCY == "selected"
        assert Path(sys.modules[dependency].__file__).resolve().is_relative_to(selected)
        print(json.dumps({"dependency_origin": "selected"}))
        """,
    )
    assert result == {"dependency_origin": "selected"}


def test_picker_dependency_resolution_failure_does_not_import_or_repair(tmp_path):
    result = _run(
        tmp_path,
        """
        from unittest.mock import patch
        from amplifier_app_cli.lib.settings import AppSettings
        from amplifier_app_cli.provider_loader import ProviderSourceError, get_provider_info
        from amplifier_app_cli.provider_manager import ProviderManager
        from amplifier_app_cli.provider_sources import is_provider_module_installed

        dependency = "amplifier_module_provider_origin_base"
        wheel = Path(sys.path[0])
        package = wheel / dependency
        package.mkdir()
        (package / "__init__.py").write_text("ORIGIN = 'wheel'\\n")
        dist = wheel / "origin_base-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\\nName: origin-base\\nVersion: 1.0\\n")
        (dist / "entry_points.txt").write_text(
            "[amplifier.modules]\\nprovider-origin-base = " + dependency + ":mount\\n")
        path = selected / name / "__init__.py"
        path.write_text("from " + dependency + " import ORIGIN as DEPENDENCY\\n"
                        + path.read_text())
        missing = selected.parent / "missing-base-source"
        assert not missing.exists()
        assert path.is_file()
        config = Path.home() / ".amplifier/settings.yaml"
        config.write_text(json.dumps({"sources": {"modules": {
            "provider-origin-base": str(missing),
            "provider-origin-fixture": str(selected),
        }}}))
        assert is_provider_module_installed("provider-origin-base")
        assert is_provider_module_installed("provider-origin-fixture")
        with (
            patch("amplifier_app_cli.provider_sources.DEFAULT_PROVIDER_SOURCES", {}),
            patch("amplifier_app_cli.provider_sources.PROVIDER_DEPENDENCIES",
                  {"provider-origin-fixture": ["provider-origin-base"]}),
            patch("amplifier_app_cli.provider_manager.ensure_provider_installed") as ensure,
            patch("subprocess.run") as install,
        ):
            try:
                ProviderManager(AppSettings()).list_providers()
            except ProviderSourceError as error:
                assert isinstance(error, ImportError)
                assert error.__cause__ is not None
                assert "provider-origin-base" in str(error)
            else:
                raise AssertionError("Picker hid the selected dependency source failure")
            # Also exercise the central loader's dependency-resolution boundary.
            try:
                get_provider_info("origin-fixture")
            except ProviderSourceError as error:
                assert error.__cause__ is not None
                assert "provider-origin-base" in str(error)
            else:
                raise AssertionError("Loader used the installed dependency wheel")
            ensure.assert_not_called()
            install.assert_not_called()
        assert not any(n == name or n.startswith(name + ".")
                       or n == dependency or n.startswith(dependency + ".")
                       for n in sys.modules)
        print(json.dumps({"rejected": True, "imported": False, "repaired": False}))
        """,
    )
    assert result == {"rejected": True, "imported": False, "repaired": False}


@pytest.mark.parametrize("source_kind", ["local", "git"])
def test_sdk_repair_retry_retains_custom_settings_source_path(tmp_path, source_kind):
    result = _run(
        tmp_path,
        """
        from types import SimpleNamespace
        from unittest.mock import patch
        from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
        from amplifier_app_cli.provider_loader import get_provider_info as real_get_info
        from amplifier_app_cli.provider_manager import ProviderManager
        from amplifier_app_cli.provider_sources import get_effective_provider_sources

        source_kind = SOURCE_KIND
        module_id = "provider-origin-fixture"
        wheel = Path(sys.path[0])
        ambient = Path.home() / ".amplifier/settings.yaml"
        ambient.write_text(json.dumps({"config": {"providers": [
            {"module": module_id, "source": str(wheel)}
        ]}}))
        source_uri = (str(selected) if source_kind == "local" else
                      "git+https://example.invalid/provider-origin-fixture@selected")
        custom = selected.parent / "manager-settings"
        custom.mkdir()
        paths = SettingsPaths(custom / "global.yaml", custom / "project.yaml",
                              custom / "local.yaml")
        paths.global_settings.write_text(json.dumps({"config": {"providers": [
            {"module": module_id, "source": source_uri}
        ]}}))
        settings = AppSettings(paths)
        attempts = []
        repaired = False

        def resolve_source(uri):
            root = {source_uri: selected, str(wheel): wheel}[uri]
            return SimpleNamespace(resolve=lambda: root)

        def get_info(provider_id, *, source_path=None):
            attempts.append(source_path)
            assert provider_id == module_id
            if len(attempts) == 1:
                raise ModuleNotFoundError("No module named 'fixture_sdk'",
                                          name="fixture_sdk")
            assert repaired
            return real_get_info(provider_id, source_path=source_path)

        def install_local(command, **kwargs):
            global repaired
            assert command == ["uv", "pip", "install", "-e", str(selected)]
            repaired = True
            return SimpleNamespace(returncode=0, stderr="")

        def install_git(provider_id, *, config_manager):
            global repaired
            assert provider_id == module_id
            assert config_manager is settings
            repaired = True
            return True

        with (
            patch("amplifier_app_cli.provider_sources.DEFAULT_PROVIDER_SOURCES", {}),
            patch("amplifier_app_cli.provider_manager.source_from_uri",
                  side_effect=resolve_source),
            patch("amplifier_app_cli.provider_sources.source_from_uri",
                  side_effect=resolve_source),
            patch("amplifier_app_cli.provider_manager.get_provider_info",
                  side_effect=get_info),
            patch("amplifier_app_cli.provider_manager.uv_target_args", return_value=[]),
            patch("amplifier_app_cli.provider_manager.subprocess.run",
                  side_effect=install_local) as local_install,
            patch("amplifier_app_cli.provider_manager.ensure_provider_installed",
                  side_effect=install_git) as git_install,
        ):
            assert get_effective_provider_sources(settings) == {module_id: source_uri}
            assert get_effective_provider_sources(AppSettings()) == {module_id: str(wheel)}
            providers = ProviderManager(settings)._discover_providers_from_sources()
            if source_kind == "local":
                local_install.assert_called_once()
                git_install.assert_not_called()
            else:
                git_install.assert_called_once_with(module_id, config_manager=settings)
                local_install.assert_not_called()
        assert attempts == [selected, selected]
        assert providers[module_id] == (module_id, "selected", "fixture provider")
        assert sys.modules[name]._cost.ORIGIN == "selected"
        assert Path(sys.modules[name].__file__).resolve().is_relative_to(selected)
        print(json.dumps({"origin": "selected", "attempts": len(attempts)}))
        """.replace("SOURCE_KIND", repr(source_kind)),
    )
    assert result == {"origin": "selected", "attempts": 2}


def test_local_retry_origin_error_propagates_without_purging_imports(tmp_path):
    result = _run(
        tmp_path,
        """
        from types import SimpleNamespace
        from unittest.mock import patch
        from amplifier_app_cli.provider_loader import ProviderSourceError
        from amplifier_app_cli.provider_loader import get_provider_info as real_get_info
        from amplifier_app_cli.provider_manager import ProviderManager

        original = importlib.import_module(name)
        before = {n: (m, m.__file__) for n, m in tuple(sys.modules.items())
                  if n == name or n.startswith(name + ".")}
        attempts = []

        def get_info(provider_id, *, source_path=None):
            attempts.append(source_path)
            if len(attempts) == 1:
                raise ModuleNotFoundError("No module named 'fixture_sdk'",
                                          name="fixture_sdk")
            return real_get_info(provider_id, source_path=source_path)

        with (
            patch("amplifier_app_cli.provider_manager.get_effective_provider_sources",
                  return_value={"provider-origin-fixture": str(selected)}),
            patch("amplifier_app_cli.provider_manager.get_provider_info",
                  side_effect=get_info),
            patch("amplifier_app_cli.provider_manager.uv_target_args", return_value=[]),
            patch("amplifier_app_cli.provider_manager.subprocess.run",
                  return_value=SimpleNamespace(returncode=0, stderr="")) as install,
            patch("amplifier_app_cli.provider_manager.ensure_provider_installed") as ensure,
        ):
            try:
                ProviderManager().list_providers()
            except ProviderSourceError as error:
                assert "different source" in str(error)
            else:
                raise AssertionError("Local retry hid an origin conflict as fallback metadata")
            install.assert_called_once()
            ensure.assert_not_called()
        assert attempts == [selected, selected]
        assert sys.modules[name] is original
        assert original._cost.ORIGIN == "wheel"
        for n, (module, filename) in before.items():
            assert sys.modules[n] is module
            assert module.__file__ == filename
        print(json.dumps({"rejected": True, "preserved": True}))
        """,
    )
    assert result == {"rejected": True, "preserved": True}
