"""Tests verifying dead code removal (Tasks 3-6).

These tests assert that removed code no longer exists in the codebase.
They should FAIL before the cleanup and PASS after.
"""

import inspect


# === Task 3: Trivial dead code ===


class TestTask3TrivialDeadCode:
    def test_no_cancel_requested_global_variable(self):
        """_cancel_requested should not exist as a module-level variable."""
        from pathlib import Path

        source = Path(__file__).parent.parent / "amplifier_app_cli" / "main.py"
        content = source.read_text()
        assert "_cancel_requested" not in content, (
            "_cancel_requested is dead code (CancellationToken is used instead)"
        )

    def test_key_manager_not_stored_as_variable(self):
        """_key_manager should not exist as a module-level variable.

        KeyManager() is called for its side-effect only (loads keys.env).
        """
        from pathlib import Path

        source = Path(__file__).parent.parent / "amplifier_app_cli" / "main.py"
        content = source.read_text()
        assert "_key_manager" not in content, (
            "_key_manager is never referenced; only the constructor side-effect matters"
        )

    def test_no_double_return_in_find_in_path(self):
        """_find_in_path should not have consecutive 'return None' statements."""
        from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery

        source = inspect.getsource(AppBundleDiscovery._find_in_path)
        lines = [line.strip() for line in source.splitlines()]
        for i in range(len(lines) - 1):
            if lines[i] == "return None" and lines[i + 1] == "return None":
                raise AssertionError("Found consecutive 'return None' in _find_in_path")


# === Task 4: Remove resolve_app_config() ===


class TestTask4ResolveAppConfig:
    def test_resolve_app_config_removed_from_module(self):
        """resolve_app_config() should not exist in runtime.config."""
        from amplifier_app_cli.runtime import config as config_mod

        assert not hasattr(config_mod, "resolve_app_config"), (
            "resolve_app_config is dead code (replaced by resolve_bundle_config)"
        )

    def test_resolve_app_config_not_in_all(self):
        """resolve_app_config should not appear in __all__."""
        from amplifier_app_cli.runtime import config as config_mod

        assert "resolve_app_config" not in config_mod.__all__


# === Task 5: Remove provider_override/model_override ===


class TestTask5ProviderOverrideLegacy:
    def test_apply_provider_override_removed(self):
        """_apply_provider_override() should not exist in session_spawner."""
        import amplifier_app_cli.session_spawner as spawner_mod

        assert not hasattr(spawner_mod, "_apply_provider_override"), (
            "_apply_provider_override is dead code"
        )

    def test_spawn_sub_session_no_provider_override_param(self):
        """spawn_sub_session should not accept provider_override parameter."""
        from amplifier_app_cli.session_spawner import spawn_sub_session

        sig = inspect.signature(spawn_sub_session)
        assert "provider_override" not in sig.parameters, (
            "provider_override is a legacy param that should be removed"
        )
        assert "model_override" not in sig.parameters, (
            "model_override is a legacy param that should be removed"
        )

    def test_session_runner_spawn_capability_no_legacy_params(self):
        """session_runner's spawn capability should not have legacy params."""
        source = inspect.getsource(
            __import__(
                "amplifier_app_cli.session_runner",
                fromlist=["register_session_spawning"],
            ).register_session_spawning
        )
        assert "provider_override" not in source
        assert "model_override" not in source


# === Task 6: Remove ConfigManager compat layer ===


class TestTask6ConfigManagerRemoval:
    def test_config_compat_module_removed(self):
        """lib/config_compat.py should not be importable."""
        import importlib

        try:
            importlib.import_module("amplifier_app_cli.lib.config_compat")
            raise AssertionError("config_compat module should have been removed")
        except ImportError:
            pass  # Expected

    def test_provider_manager_uses_app_settings(self):
        """ProviderManager should accept AppSettings, not ConfigManager."""
        # Just verify it doesn't crash on import (no ConfigManager import)
        source = inspect.getsource(
            __import__(
                "amplifier_app_cli.provider_manager",
                fromlist=["ProviderManager"],
            )
        )
        assert "ConfigManager" not in source, (
            "ProviderManager should use AppSettings, not ConfigManager"
        )

    def test_no_config_compat_references_in_production_code(self):
        """No production code should reference config_compat."""
        import amplifier_app_cli.paths as paths_mod

        source = inspect.getsource(paths_mod)
        assert "config_compat" not in source, (
            "paths.py should not import from config_compat"
        )


# === Task 7: Remove the unused /goal app-layer duplicate loop ===
#
# `_evaluate_goal` / `_execute_with_interrupt_and_goal` were a spike-era,
# never-called duplicate of the goal auto-continue loop that now lives in
# the orchestrator (loop-streaming's execute()). Left in place they are
# context poison: dead code that silently drifts from the real
# implementation and can mislead future readers/AI into thinking it's live.


class TestTask7GoalDuplicateLoopRemoval:
    def test_evaluate_goal_removed(self):
        """_evaluate_goal (the app-layer evaluator duplicate) should be gone."""
        from pathlib import Path

        source = (
            Path(__file__).parent.parent / "amplifier_app_cli" / "main.py"
        ).read_text(encoding="utf-8")
        assert "_evaluate_goal" not in source, (
            "_evaluate_goal is dead code -- the goal loop lives in the "
            "orchestrator (loop-streaming's execute()), not the app layer"
        )

    def test_execute_with_interrupt_and_goal_removed(self):
        """_execute_with_interrupt_and_goal (the app-layer loop duplicate) should be gone."""
        from pathlib import Path

        source = (
            Path(__file__).parent.parent / "amplifier_app_cli" / "main.py"
        ).read_text(encoding="utf-8")
        assert "_execute_with_interrupt_and_goal" not in source, (
            "_execute_with_interrupt_and_goal is dead code -- never called; "
            "the REPL and initial-prompt paths call _execute_with_interrupt directly"
        )

    def test_flatten_message_for_evaluator_removed(self):
        """The evaluator-only transcript helper should be gone with its caller."""
        from pathlib import Path

        source = (
            Path(__file__).parent.parent / "amplifier_app_cli" / "main.py"
        ).read_text(encoding="utf-8")
        assert "_flatten_message_for_evaluator" not in source

    def test_no_stale_design_doc_reference(self):
        """docs/designs/goal-command.md does not exist; references must point
        at the real doc, docs/GOAL_COMMAND.md."""
        from pathlib import Path

        repo_root = Path(__file__).parent.parent
        assert not (repo_root / "docs" / "designs" / "goal-command.md").exists()

        main_source = (repo_root / "amplifier_app_cli" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "docs/designs/goal-command.md" not in main_source

        hook_source = (
            repo_root / "amplifier_app_cli" / "goal_progress_hook.py"
        ).read_text(encoding="utf-8")
        assert "docs/designs/goal-command.md" not in hook_source

        assert (repo_root / "docs" / "GOAL_COMMAND.md").exists()
