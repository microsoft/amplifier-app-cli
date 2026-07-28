"""Regression test: check_first_run() must repair ALL missing provider modules.

PR #238 narrowed the missing-module repair branch in ``check_first_run()`` from
``install_known_providers()`` (attempts every known provider, skip-if-installed)
to ``ensure_provider_installed()`` (installs only the currently *active*
provider module). The stated reason was to avoid overwriting providers the
user may have pinned to a specific build.

That concern is already handled by the skip-if-installed guard that the same
PR added inside ``install_known_providers()`` (see
``TestInstallKnownProvidersIdempotency`` in test_provider_source_precedence.py):
with ``force=False`` (the default), already-installed providers are left
untouched. The call-site narrowing was therefore redundant defense -- and it
introduced a real regression.

The failure mode this branch exists to repair -- `amplifier update` or
`amplifier reset` wiping the tool venv -- removes ALL provider modules, not
just the active one. A user with multiple provider instances configured
(e.g. anthropic + openai + chat-completions) only got their active provider
reinstalled; every other declared provider silently failed to mount on next
boot, producing errors like:

    Partial provider failure: 4/10 loaded. Missing: {'openai': 1, ...}

This test pins the correct behavior: the repair path must attempt every
known/declared provider module, not just the active one.
"""

from unittest.mock import MagicMock, patch


class TestCheckFirstRunRepairsAllKnownProviders:
    def test_check_first_run_calls_install_known_providers_not_single(self):
        """The repair branch must drive install_known_providers() (all known
        providers, skip-if-installed) rather than a single-module install.

        `init.py` must not even import `ensure_provider_installed` for this
        call site any more -- asserting that import is gone is itself part
        of the regression guard, since its presence is what let the narrowed
        call site regress silently in the first place.
        """
        from amplifier_app_cli.commands import init as init_cmd

        assert not hasattr(init_cmd, "ensure_provider_installed"), (
            "check_first_run()'s repair path must use install_known_providers() "
            "exclusively; a lingering ensure_provider_installed import is how "
            "the single-module regression crept back in"
        )

        provider = MagicMock()
        provider.module_id = "provider-anthropic"
        provider_mgr = MagicMock()
        provider_mgr.get_current_provider.return_value = provider

        present: set[str] = set()  # wiped venv; repair makes these importable

        with (
            patch.object(init_cmd, "create_config_manager"),
            patch.object(init_cmd, "ProviderManager", return_value=provider_mgr),
            patch.object(
                init_cmd,
                "_is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            patch.object(
                init_cmd,
                "install_known_providers",
                side_effect=_stateful_install(
                    present,
                    [
                        "provider-anthropic",
                        "provider-openai",
                        "provider-chat-completions",
                    ],
                ),
            ) as mock_install_all,
        ):
            needs_init = init_cmd.check_first_run()

        assert needs_init is False
        mock_install_all.assert_called_once()

    def test_end_to_end_every_declared_provider_module_is_attempted(self):
        """Pin the actual outcome, not just the call: with settings declaring
        THREE distinct provider modules and a wiped venv (nothing installed),
        every one of them must be attempted for install -- not just the
        currently active provider.
        """
        from amplifier_app_cli.commands import init as init_cmd

        provider = MagicMock()
        provider.module_id = "provider-anthropic"
        provider_mgr = MagicMock()
        provider_mgr.get_current_provider.return_value = provider

        sources = {
            "provider-anthropic": "git+https://example.com/anthropic@main",
            "provider-openai": "git+https://example.com/openai@main",
            "provider-chat-completions": "git+https://example.com/chat-completions@main",
        }

        attempted: list[str] = []
        present: set[str] = set()  # wiped venv; a successful install fills this
        module_for_uri = {uri: module for module, uri in sources.items()}

        def fake_source_from_uri(uri: str):
            src = MagicMock()
            src.resolve.return_value = uri
            return src

        def fake_run(cmd, *args, **kwargs):
            uri = cmd[4]  # ["uv", "pip", "install", "-e", <path>, ...]
            attempted.append(uri)
            # Installing a module makes it importable -- model that, so the
            # post-repair check sees the world the install actually produced.
            present.add(module_for_uri[uri])
            return MagicMock(returncode=0, stderr="")

        with (
            patch.object(init_cmd, "create_config_manager"),
            patch.object(init_cmd, "ProviderManager", return_value=provider_mgr),
            patch.object(
                init_cmd,
                "_is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            patch(
                "amplifier_app_cli.provider_sources.get_effective_provider_sources",
                return_value=sources,
            ),
            patch(
                "amplifier_app_cli.provider_sources.is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            patch(
                "amplifier_app_cli.provider_sources.source_from_uri",
                side_effect=fake_source_from_uri,
            ),
            patch(
                "amplifier_app_cli.provider_sources.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            needs_init = init_cmd.check_first_run()

        assert needs_init is False
        assert set(attempted) == set(sources.values()), (
            "every declared provider module must be attempted for install, "
            "not just the currently active one"
        )

    def test_already_installed_providers_are_still_not_overwritten(self):
        """The revert must not reintroduce the OTHER regression #238 fixed:
        install_known_providers()'s skip-if-installed guard (force=False) is
        what makes it safe to call unconditionally from check_first_run().
        A provider that is already installed must not be reinstalled/overwritten.
        """
        from amplifier_app_cli.commands import init as init_cmd

        provider = MagicMock()
        provider.module_id = "provider-anthropic"
        provider_mgr = MagicMock()
        provider_mgr.get_current_provider.return_value = provider

        sources = {
            "provider-anthropic": "git+https://example.com/anthropic@main",
            "provider-openai": "git+https://example.com/openai@main",
        }

        attempted: list[str] = []

        def fake_source_from_uri(uri: str):
            src = MagicMock()
            src.resolve.return_value = uri
            return src

        def fake_run(cmd, *args, **kwargs):
            attempted.append(cmd[4])
            return MagicMock(returncode=0, stderr="")

        with (
            patch.object(init_cmd, "create_config_manager"),
            patch.object(init_cmd, "ProviderManager", return_value=provider_mgr),
            # The active provider's module is missing (triggers the repair path)...
            patch.object(init_cmd, "_is_provider_module_installed", return_value=False),
            patch(
                "amplifier_app_cli.provider_sources.get_effective_provider_sources",
                return_value=sources,
            ),
            # ...but provider-openai is already installed and must be left alone.
            patch(
                "amplifier_app_cli.provider_sources.is_provider_module_installed",
                side_effect=lambda mid: mid == "provider-openai",
            ),
            patch(
                "amplifier_app_cli.provider_sources.source_from_uri",
                side_effect=fake_source_from_uri,
            ),
            patch(
                "amplifier_app_cli.provider_sources.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            init_cmd.check_first_run()

        assert attempted == [sources["provider-anthropic"]], (
            "already-installed provider-openai must not be reinstalled/overwritten"
        )


def _stateful_install(present: set, installs: list) -> "object":
    """install_known_providers() stub that makes what it installs importable.

    Installing a provider changes its importability, so a repair stub must
    update the state the installed-check reads. Pairing a constant
    `_is_provider_module_installed -> False` mock with a repair that reports
    success encodes a world where installation never actually works -- the
    repair path could never be observed to succeed, and the test would only
    pass while check_first_run() trusted the repair's self-report instead of
    verifying importability.
    """

    def _install(*args, **kwargs):
        present.update(installs)
        return list(installs)

    return _install


def _config_with_providers(entries: list) -> MagicMock:
    """Stub config manager exposing `config.providers` entries."""
    config = MagicMock()
    config.get_provider_overrides.return_value = entries
    return config


def _provider_mgr(active_module_id: str | None) -> MagicMock:
    """Stub ProviderManager whose active provider is `active_module_id`."""
    mgr = MagicMock()
    if active_module_id is None:
        mgr.get_current_provider.return_value = None
    else:
        provider = MagicMock()
        provider.module_id = active_module_id
        mgr.get_current_provider.return_value = provider
    return mgr


class TestCheckFirstRunGatesOnAllConfiguredModules:
    """The repair gate must consider EVERY configured provider module.

    Gating the repair on the ACTIVE provider alone leaves a hole: if the active
    provider happens to be installed but another configured provider is missing,
    check_first_run() returns early and repairs nothing. That state is reachable
    in practice --

    * install_known_providers() catches per-provider exceptions and continues,
      so one transient git/network failure leaves that provider missing forever
      (the active one succeeded, so no later boot ever retries),
    * the user hand-edits settings.yaml to add a provider,
    * `amplifier provider install <name>` installs a single provider.

    `amplifier update` and `amplifier reset` install no providers themselves --
    both rely on the next boot's check_first_run() -- so this single gate is the
    funnel for update, reset, and fresh install alike.

    Return semantics stay driven by the ACTIVE provider only: "needs init" (True)
    means "push the user into the add-a-provider prompt", which a non-active
    provider failing to install must never trigger.
    """

    def test_missing_non_active_module_triggers_repair(self):
        """The hole: active installed, another configured module missing."""
        from amplifier_app_cli.commands import init as init_cmd

        config = _config_with_providers(
            [
                {"module": "provider-anthropic", "id": "anthropic"},
                {"module": "provider-openai", "id": "openai"},
                {"module": "provider-gemini", "id": "gemini"},
            ]
        )
        # Active (anthropic) is fine; openai is the gap.
        present = {"provider-anthropic", "provider-gemini"}

        with (
            patch.object(init_cmd, "create_config_manager", return_value=config),
            patch.object(
                init_cmd, "ProviderManager", return_value=_provider_mgr("provider-anthropic")
            ),
            patch.object(
                init_cmd,
                "_is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            patch.object(
                init_cmd,
                "install_known_providers",
                return_value=[
                    "provider-anthropic",
                    "provider-openai",
                    "provider-gemini",
                ],
            ) as mock_install,
        ):
            needs_init = init_cmd.check_first_run()

        mock_install.assert_called_once()
        assert needs_init is False

    def test_all_configured_modules_installed_skips_repair(self):
        """Happy path: nothing missing -> no repair, no reinstall churn."""
        from amplifier_app_cli.commands import init as init_cmd

        config = _config_with_providers(
            [
                {"module": "provider-anthropic"},
                {"module": "provider-openai"},
                {"module": "provider-chat-completions"},
            ]
        )

        with (
            patch.object(init_cmd, "create_config_manager", return_value=config),
            patch.object(
                init_cmd, "ProviderManager", return_value=_provider_mgr("provider-anthropic")
            ),
            patch.object(
                init_cmd, "_is_provider_module_installed", return_value=True
            ) as mock_check,
            patch.object(init_cmd, "install_known_providers") as mock_install,
        ):
            needs_init = init_cmd.check_first_run()

        mock_install.assert_not_called()
        assert needs_init is False
        # Every configured module is checked -- and nothing more expensive runs.
        assert {c.args[0] for c in mock_check.call_args_list} == {
            "provider-anthropic",
            "provider-openai",
            "provider-chat-completions",
        }

    def test_full_wipe_triggers_repair(self):
        """The original bug: venv wiped, nothing installed at all."""
        from amplifier_app_cli.commands import init as init_cmd

        config = _config_with_providers(
            [
                {"module": "provider-anthropic"},
                {"module": "provider-openai"},
            ]
        )

        present: set[str] = set()  # wiped venv; repair makes these importable

        with (
            patch.object(init_cmd, "create_config_manager", return_value=config),
            patch.object(
                init_cmd, "ProviderManager", return_value=_provider_mgr("provider-anthropic")
            ),
            patch.object(
                init_cmd,
                "_is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            patch.object(
                init_cmd,
                "install_known_providers",
                side_effect=_stateful_install(
                    present, ["provider-anthropic", "provider-openai"]
                ),
            ) as mock_install,
        ):
            needs_init = init_cmd.check_first_run()

        mock_install.assert_called_once()
        assert needs_init is False

    def test_unfixable_leftover_warns_but_does_not_block(self, caplog):
        """A module repair cannot install must warn -- not force init.

        A provider whose module is neither in DEFAULT_PROVIDER_SOURCES nor
        carries a `source:` is unfixable here. Returning True for that would
        shove the user into the add-a-provider prompt on every single boot,
        forever, even though their active provider works fine.
        """
        import logging

        from amplifier_app_cli.commands import init as init_cmd

        config = _config_with_providers(
            [
                {"module": "provider-anthropic"},
                {"module": "provider-acme-custom"},
            ]
        )
        present = {"provider-anthropic"}

        with (
            patch.object(init_cmd, "create_config_manager", return_value=config),
            patch.object(
                init_cmd, "ProviderManager", return_value=_provider_mgr("provider-anthropic")
            ),
            patch.object(
                init_cmd,
                "_is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            # Repair cannot supply provider-acme-custom.
            patch.object(
                init_cmd,
                "install_known_providers",
                return_value=["provider-anthropic"],
            ) as mock_install,
            caplog.at_level(logging.WARNING, logger="amplifier_app_cli.commands.init"),
        ):
            needs_init = init_cmd.check_first_run()

        mock_install.assert_called_once()
        assert needs_init is False, (
            "an unfixable non-active provider must not force the init prompt"
        )
        assert "provider-acme-custom" in caplog.text, (
            "the warning must name the module that could not be installed"
        )

    def test_locally_developed_active_provider_is_not_forced_into_init(self):
        """A locally editable-installed active provider must not force init.

        `install_known_providers()` only ever iterates
        `get_effective_provider_sources()` -- the known providers plus anything
        carrying an explicit `source:`. A provider module installed locally for
        development (`uv pip install -e ...`, no `source:` in settings) is
        importable and perfectly usable, but never appears in the returned
        `installed` list.

        So membership in `installed` is NOT the same question as "is the active
        provider usable". Gating solely on `in installed` means: active provider
        is local-dev AND some other configured provider is missing -> the repair
        block runs -> the active module is absent from `installed` -> we return
        True and tell the user "No provider configured!" about a provider that
        is configured and working. Non-interactively, auto_init_from_env() then
        writes provider config they never asked for.
        """
        from amplifier_app_cli.commands import init as init_cmd

        config = _config_with_providers(
            [
                {"module": "provider-localdev"},  # active, installed, NOT in sources
                {"module": "provider-openai"},  # missing, IS in sources
            ]
        )
        present = {"provider-localdev"}

        with (
            patch.object(init_cmd, "create_config_manager", return_value=config),
            patch.object(
                init_cmd, "ProviderManager", return_value=_provider_mgr("provider-localdev")
            ),
            patch.object(
                init_cmd, "_is_provider_module_installed", side_effect=lambda m: m in present
            ),
            patch.object(
                init_cmd, "install_known_providers", return_value=["provider-openai"]
            ),
        ):
            needs_init = init_cmd.check_first_run()

        assert needs_init is False, (
            "a locally installed active provider is usable even though it never "
            "appears in install_known_providers()'s return list"
        )

    def test_active_reported_installed_but_not_importable_must_not_start_session(self):
        """`installed` is a self-report, not evidence the module works.

        install_known_providers() appends a module to its return list on
        subprocess `rc == 0` alone -- it never confirms importability. An
        install can exit 0 and still yield an unusable module: a broken
        dependency, an entry point naming a module absent from the built
        package, a stranded `.dist-info`. Such a module lands in `installed`
        while is_provider_module_installed() correctly reports False.

        Trusting the self-report would boot a session with a dead active
        provider. This is the same principle is_provider_module_installed()'s
        own docstring states: a registered entry point is NOT sufficient
        evidence that a provider is usable.
        """
        from amplifier_app_cli.commands import init as init_cmd

        config = _config_with_providers(
            [{"module": "provider-anthropic"}, {"module": "provider-openai"}]
        )

        with (
            patch.object(init_cmd, "create_config_manager", return_value=config),
            patch.object(
                init_cmd,
                "ProviderManager",
                return_value=_provider_mgr("provider-anthropic"),
            ),
            # Repair claims success for both, but the module is still not importable.
            patch.object(init_cmd, "_is_provider_module_installed", return_value=False),
            patch.object(
                init_cmd,
                "install_known_providers",
                return_value=["provider-anthropic", "provider-openai"],
            ),
        ):
            needs_init = init_cmd.check_first_run()

        assert needs_init is True, (
            "a provider that reports installed but cannot be imported must not "
            "be treated as usable"
        )

    def test_no_provider_configured_still_needs_init(self):
        """Unchanged existing behavior -- must not regress."""
        from amplifier_app_cli.commands import init as init_cmd

        with (
            patch.object(init_cmd, "create_config_manager", return_value=MagicMock()),
            patch.object(init_cmd, "ProviderManager", return_value=_provider_mgr(None)),
            patch.object(init_cmd, "install_known_providers") as mock_install,
        ):
            needs_init = init_cmd.check_first_run()

        assert needs_init is True
        mock_install.assert_not_called()
