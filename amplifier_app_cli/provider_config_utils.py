"""Shared provider configuration gathering functions.

Provides generic configuration based on provider-declared config_fields.
Queries provider modules dynamically for model lists and config fields.
"""

import asyncio
import logging
import os
import re
import unicodedata
from typing import Any

import yaml
from rich.markup import escape

from rich.console import Console
from rich.prompt import Confirm
from rich.prompt import Prompt

from .key_manager import KeyManager
from .lib.settings import AppSettings
from .lib.settings import Scope
from .provider_loader import _try_instantiate_provider
from .provider_loader import get_provider_info
from .provider_loader import get_provider_models
from .provider_loader import list_models_for_instance
from .provider_loader import load_provider_class

console = Console()
logger = logging.getLogger(__name__)

# Reserved, user-owned provider-config keys. Provider modules are adopting
# ``extra_request_params`` as an owner-beware dict merged verbatim into every
# API request, for parameters the module itself doesn't wrap -- users
# maintain it by hand in settings.yaml. It is deliberately never declared as
# a ConfigField (never a wizard prompt, never displayed, never validated),
# so every config-rewrite path that rebuilds a provider's config purely from
# wizard-collected fields must round-trip it verbatim via
# ``_preserve_reserved_keys()`` below rather than silently dropping it. Keep
# this tuple as the single, deliberately narrow allow-list for that
# passthrough convention -- do not widen it to cover arbitrary unknown keys.
RESERVED_PROVIDER_CONFIG_KEYS: tuple[str, ...] = ("extra_request_params",)


def _preserve_reserved_keys(
    old_config: dict[str, Any] | None, new_config: dict[str, Any]
) -> dict[str, Any]:
    """Carry forward reserved, user-owned config keys from ``old_config``
    into ``new_config`` verbatim, if present.

    Called wherever a config-rewrite path replaces an EXISTING provider
    instance's config with a freshly wizard-collected one. Only keys in
    ``RESERVED_PROVIDER_CONFIG_KEYS`` (today, just ``extra_request_params``)
    are preserved -- any other non-schema key in ``old_config`` is dropped,
    exactly as before this function existed. If ``new_config`` already
    carries the key (e.g. a module surfaces it deliberately in the future),
    the old value is not used -- the freshly collected value wins.

    Args:
        old_config: The provider instance's config before this reconfigure,
            or None if this is a fresh instance with no prior config.
        new_config: The config just rebuilt from wizard/schema answers.

    Returns:
        ``new_config``, with any missing reserved keys copied in from
        ``old_config``. Never mutates either input in place.
    """
    if not old_config:
        return new_config
    result = new_config
    for key in RESERVED_PROVIDER_CONFIG_KEYS:
        if key in old_config and key not in result:
            result = {**result, key: old_config[key]}
    return result


def _prompt_model_selection(
    provider_id: str,
    default_model: str | None = None,
    collected_config: dict[str, Any] | None = None,
    models: list | None = None,
) -> str | None:
    """Prompt user to select a model from provider's available models.

    Queries the provider module for available models and presents a selection menu.
    Falls back to custom input if no models available.

    Args:
        provider_id: Provider ID (e.g., "anthropic", "openai")
        default_model: Optional default model from existing config (NOT hard-coded provider default)
        collected_config: Optional config values collected from user (base_url, host, etc.)
            Passed to provider for dynamic model discovery from real servers.
        models: Optional pre-fetched list of ModelInfo objects. When provided, skips the
            fetch step and uses these models directly.

    Returns:
        Selected model name, or None if interrupted (Ctrl-C / EOF).
    """
    try:
        if models is None:
            with console.status(
                "[dim]Fetching available models...[/dim]", spinner="dots"
            ):
                try:
                    models = get_provider_models(
                        provider_id, collected_config=collected_config
                    )
                except (ConnectionError, OSError) as e:
                    logger.debug(f"Could not connect to provider '{provider_id}': {e}")
                    models = []
                except Exception as e:
                    console.print(
                        f"\n  [yellow]⚠  Could not fetch models for '{escape(str(provider_id))}':[/yellow]"
                        f"\n\n  {escape(str(e))}\n"
                    )
                    models = []
        # else: use the pre-fetched models passed in

        if not models:
            # No models available - show helpful message and prompt for custom input
            # Provider-specific hints for common local providers
            if provider_id in ("ollama", "provider-ollama"):
                console.print(
                    "  [dim](No models found on Ollama server. Run 'ollama pull <model>' to install models.)[/dim]"
                )
            elif provider_id in ("vllm", "provider-vllm"):
                console.print(
                    "  [dim](Could not connect to vLLM server or no models available.)[/dim]"
                )
            else:
                console.print("  [dim](No models discovered from server.)[/dim]")
            model = Prompt.ask("Model name", default=default_model or "")
            return model

        # Check if default_model is in the provider's model list
        model_ids = [m.id for m in models]
        default_in_list = default_model and default_model in model_ids

        # Build selection menu from available models
        model_map: dict[str, str] = {}

        for idx, model_info in enumerate(models, 1):
            model_map[str(idx)] = model_info.id
            # Show display name and capabilities if available
            caps = ""
            if hasattr(model_info, "capabilities") and model_info.capabilities:
                key_caps = [
                    c
                    for c in model_info.capabilities
                    if c in ("fast", "thinking", "vision")
                ]
                if key_caps:
                    caps = f" ({', '.join(key_caps)})"
            console.print(f"  [{idx}] {model_info.display_name}{caps}")

        next_idx = len(models) + 1

        # If default_model exists but not in list, add it as "keep current" option
        if default_model and not default_in_list:
            model_map[str(next_idx)] = default_model
            console.print(f"  [{next_idx}] {default_model} [dim](current)[/dim]")
            next_idx += 1

        # Add "custom" option for entering a different model
        model_map[str(next_idx)] = "__custom__"
        console.print(f"  [{next_idx}] custom")

        # Determine default choice
        # Only use a default if there's an existing model from config
        # No hard-coded defaults - user must choose for new configs
        default_choice: str | None = None
        if default_model:
            for idx, model_id in model_map.items():
                if model_id == default_model:
                    default_choice = idx
                    break

        if default_choice:
            choice = Prompt.ask(
                "Choice", choices=list(model_map.keys()), default=default_choice
            )
        else:
            choice = Prompt.ask("Choice", choices=list(model_map.keys()))

        if model_map[choice] == "__custom__":
            return Prompt.ask("Model name", default=default_model or "")

        return model_map[choice]

    except (KeyboardInterrupt, EOFError):
        return None


def _run_provider_login(provider: Any) -> bool:
    """Drive an already-instantiated provider's login() flow.

    Renders whatever the provider's login() prints (device-code URL,
    instructions, etc.) through this module's rich console via a
    ``print_fn`` callback -- the provider itself has no console of its
    own. Shared between the configuration wizard's login step (see
    ``_maybe_login_provider()``) and the standalone
    ``amplifier provider login <id>`` command, so the two call sites can
    never diverge on how a provider's ``login()`` is invoked.

    Args:
        provider: An already-instantiated provider with a ``login``
            attribute (sync or async), taking an optional ``print_fn``
            keyword argument -- see the duck-typed auth contract in
            ``_maybe_login_provider()``'s docstring.

    Returns:
        True if ``login()`` reported success, False on a graceful
        failure. Never raises for a login-specific error -- prints one
        yellow message and returns False instead of a raw traceback.

    Note:
        Ctrl-C / EOFError during login are deliberately NOT caught here;
        they propagate to the caller so the same strict abort semantics
        from the model-selection fix apply uniformly (see
        configure_provider()'s outer ``except (KeyboardInterrupt,
        EOFError)`` handler).
    """

    def _print_fn(message: str) -> None:
        console.print(message)

    login_fn = provider.login
    try:
        if asyncio.iscoroutinefunction(login_fn):
            result = asyncio.run(login_fn(print_fn=_print_fn))
        else:
            result = login_fn(print_fn=_print_fn)
    except (KeyboardInterrupt, EOFError):
        raise
    except Exception as e:
        console.print(f"[yellow]Login failed: {escape(str(e))}[/yellow]")
        return False
    return bool(result)


def _safely_fetch_models_from_instance(provider_id: str, provider: Any) -> list:
    """Fetch models from an already-instantiated provider, with the exact
    same connectivity/generic-exception safety net
    ``_prompt_model_selection()`` uses for its own (self-instantiating)
    fetch path -- so a login-time prefetch can never leak a raw
    traceback (e.g. an AuthenticationError) the way the pre-fix
    onboarding flow did.
    """
    try:
        return list_models_for_instance(provider)
    except (ConnectionError, OSError) as e:
        logger.debug(f"Could not connect to provider '{provider_id}': {e}")
        return []
    except Exception as e:
        console.print(
            f"\n  [yellow]⚠  Could not fetch models for '{escape(str(provider_id))}':[/yellow]"
            f"\n\n  {escape(str(e))}\n"
        )
        return []


def _maybe_login_provider(
    provider_id: str,
    info: dict[str, Any],
    collected_config: dict[str, Any],
) -> Any | None:
    """Offer a one-time interactive login for a provider that declares an
    ``"auth:*"`` capability (e.g. ``"auth:oauth-device-code"``), then
    return the instantiated provider instance so the caller can reuse it
    for model fetching -- avoiding a second, separate instantiation.

    Duck-types ``auth_status()``/``login()`` via ``hasattr`` so this
    function -- and configure_provider()'s wizard step that calls it --
    merges and runs safely independent of whichever provider module PR
    actually adds those methods and the capability string. A provider
    that declares the capability but doesn't (yet) implement the methods
    is treated exactly like one with no login flow at all (the instance
    is still returned for model-fetch reuse; no login prompt is shown).

    Args:
        provider_id: Provider ID (e.g. "openai-chatgpt").
        info: The provider's get_info() dict (from get_provider_info()).
        collected_config: Config values collected so far in Phase 1
            (base_url, host, etc.) -- passed through to instantiation so
            the same connection values are used as the eventual model
            fetch would use.

    Returns:
        The instantiated provider instance if one could be created
        (regardless of whether login ran, was declined, or failed), or
        None if this provider declares no ``"auth:*"`` capability, or if
        it could not be instantiated at all. In the None case the caller
        must fall back to its normal (already-safe) model-fetch path.

    Note:
        Ctrl-C / EOFError raised from the confirmation prompt are
        deliberately NOT caught here -- they propagate up to
        configure_provider()'s own outer except handler, landing the same
        clean "Cancelled." abort as any other prompt in the wizard (see
        the model-selection Ctrl-C fix).
    """
    capabilities = info.get("capabilities") or []
    if not any(str(c).startswith("auth:") for c in capabilities):
        return None

    provider_class = load_provider_class(provider_id)
    if provider_class is None:
        return None
    provider = _try_instantiate_provider(provider_class, collected_config)
    if provider is None:
        return None

    if not (hasattr(provider, "auth_status") and hasattr(provider, "login")):
        return provider

    try:
        status = provider.auth_status()
    except Exception:
        # A broken auth_status() must never crash the wizard -- treat it
        # like "no login capability" and let the caller proceed with
        # whatever model list it can otherwise get.
        return provider

    if status == "authenticated":
        return provider

    display_name = info.get("display_name", provider_id)
    console.print()
    proceed = Confirm.ask(
        f"[bold]{escape(str(display_name))}[/bold] requires a one-time "
        "browser login. Start it now?",
        default=True,
    )
    if not proceed:
        console.print(
            "[yellow]Skipping login -- model list may be limited; run "
            f"`amplifier provider login {escape(str(provider_id))}` later[/yellow]"
        )
        return provider

    if not _run_provider_login(provider):
        console.print(
            "[yellow]Skipping login -- model list may be limited; run "
            f"`amplifier provider login {escape(str(provider_id))}` later[/yellow]"
        )
    return provider


def _should_show_field(field: dict[str, Any], collected_config: dict[str, Any]) -> bool:
    """Check if a field should be shown based on show_when conditions.

    Args:
        field: ConfigField as dict
        collected_config: Config values collected so far

    Returns:
        True if field should be shown

    Supported patterns for expected_value:
        - "exact-value" - Exact match (case-insensitive)
        - "contains:substring" - Match if actual value contains substring
        - "not_contains:substring" - Match if actual value does NOT contain substring
        - "startswith:prefix" - Match if actual value starts with prefix
        - "not_startswith:prefix" - Match if actual value does NOT start with prefix
    """
    show_when = field.get("show_when")
    if not show_when:
        return True

    # show_when is a dict like {"model": "claude-sonnet-4-5-20250929"}
    # or with patterns like {"model": "contains:sonnet"}
    for key, expected_value in show_when.items():
        actual_value = str(collected_config.get(key, "")).lower()
        expected_str = str(expected_value).lower()

        # Check for pattern matching prefixes
        if expected_str.startswith("not_contains:"):
            pattern = expected_str[13:]  # Remove "not_contains:" prefix
            if pattern in actual_value:
                return False
        elif expected_str.startswith("contains:"):
            pattern = expected_str[9:]  # Remove "contains:" prefix
            if pattern not in actual_value:
                return False
        elif expected_str.startswith("not_startswith:"):
            pattern = expected_str[15:]  # Remove "not_startswith:" prefix
            if actual_value.startswith(pattern):
                return False
        elif expected_str.startswith("startswith:"):
            pattern = expected_str[11:]  # Remove "startswith:" prefix
            if not actual_value.startswith(pattern):
                return False
        else:
            # Default: exact match (case-insensitive)
            if actual_value != expected_str:
                return False
    return True


def _resolve_config_value(value: Any) -> Any:
    """Resolve ${VAR} references in config values to actual environment values.

    Config values like "${OPENAI_BASE_URL}" are placeholders stored in config files.
    For prompting with existing values as defaults, we need the actual values.

    Args:
        value: Value that may contain ${VAR} placeholder

    Returns:
        Resolved value from environment, or original value if not a placeholder
    """
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var)
    return value


def _normalize_id(value: str) -> str:
    """NFC-normalize so visually-identical ids differing only in Unicode
    composition (e.g. precomposed 'é' U+00E9 vs. combining 'e' + U+0301)
    compare equal.

    Without this, id-uniqueness (Bug 1) and credential-name-collision
    (§5.4.2) checks are defeated by construction: two ids that render
    identically in a terminal, and that a user would reasonably believe are
    'the same id', are treated as distinct byte strings. Copy-paste from a
    document or a different OS's clipboard is a realistic path to a
    decomposed form, not a contrived edge case.

    See docs/designs/provider-instance-credentials.md §6, §5.4.2.
    """
    return unicodedata.normalize("NFC", value)


def _sanitize_env_token(value: str) -> str:
    """Uppercase and collapse to a token matching ``[A-Z0-9_]*``.

    Any run of characters outside ``[A-Za-z0-9]`` becomes a single
    underscore; leading/trailing underscores are stripped.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def _secret_config_field_from_info(
    info: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the secret ConfigField dict (``field_type == "secret"``) from
    an already-resolved provider info dict, if any.

    Split out from ``_secret_config_field()`` so callers that must
    distinguish "module unresolvable" (``info is None``) from "resolved,
    but this provider has no secret field" (e.g. OAuth-based providers) can
    call ``get_provider_info()`` once themselves and reuse the result --
    see ``normalize_provider_secrets()``.
    """
    if not info:
        return None
    for field in info.get("config_fields", []):
        if isinstance(field, dict) and field.get("field_type") == "secret":
            return field
    return None


def _secret_config_field(module_id: str) -> dict[str, Any] | None:
    """Return the provider type's secret ConfigField dict
    (``field_type == "secret"``), if any."""
    return _secret_config_field_from_info(get_provider_info(module_id))


def _secret_env_var_for(module_id: str) -> str | None:
    """Default env var of the provider type's secret ConfigField
    (field_type == 'secret'), i.e. the collision-prone name."""
    field = _secret_config_field(module_id)
    return field.get("env_var") if field else None


def _secret_field_id_for(module_id: str) -> str | None:
    """Config field id of the provider type's secret ConfigField (e.g.
    'api_key'). Used to locate an instance's stored placeholder value on
    edit -- see docs/designs/provider-instance-credentials.md §5.3."""
    field = _secret_config_field(module_id)
    return field.get("id") if field else None


def _secret_field_id_for_info(info: dict[str, Any] | None) -> str | None:
    """Same as ``_secret_field_id_for()``, but from an already-resolved
    provider info dict -- see ``_secret_config_field_from_info()``."""
    field = _secret_config_field_from_info(info)
    return field.get("id") if field else None


def _config_claimed_env_vars(settings: AppSettings) -> set[str]:
    """Env-var names claimed by an existing *configured instance*: names
    referenced by a ``${VAR}`` placeholder in some scope's provider config
    (global, project, local, session).

    This is the design's notion of "claimed" for the add paths'
    collision detection (§5.2 steps 3-4): a name is spoken for when a
    provider entry actually points at it. A name that exists only as a
    leftover secret in ``keys.env`` -- with no entry referencing it -- is
    NOT claimed here: no instance owns it, so a first instance of that
    type may legitimately (re)use it. That case is the stale-credential
    warn-and-reuse path (§5.4.4), not a collision.

    Mirrors ``AppSettings.get_provider_overrides()``'s scope iteration
    order, but deliberately does NOT mirror its silent
    ``except Exception: pass`` error handling: a corrupt scope file here
    must be surfaced loudly, since silently under-counting claimed names
    would let a new instance claim an already-used env var and reintroduce
    Bug 3 through a different door.

    A literal (non-placeholder) config value claims nothing.
    """
    claimed: set[str] = set()
    for scope in ("global", "project", "local", "session"):
        try:
            path = settings._get_scope_path(scope)  # type: ignore[arg-type]
        except ValueError:
            continue  # e.g. session scope with no session_id set
        if not path.exists():
            continue
        if path.stat().st_size == 0:
            continue
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception as e:
            console.print(
                f"[red]⚠ {scope} settings file exists but failed to parse "
                f"({e}). Skipping it would under-count in-use credential "
                f"names and risk a silent collision -- please fix or remove "
                f"{path} before adding another same-type provider "
                f"instance.[/red]"
            )
            raise
        providers = (parsed.get("config") or {}).get("providers", [])
        for p in providers if isinstance(providers, list) else []:
            if not isinstance(p, dict):
                continue
            for v in (p.get("config") or {}).values():
                if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    claimed.add(v[2:-1])

    return claimed


def _claimed_env_vars(settings: AppSettings) -> set[str]:
    """Config claims (see ``_config_claimed_env_vars``) PLUS every name
    already backed by a real, saved secret in ``~/.amplifier/keys.env``.

    This is the WRITE-side notion, used where reusing a name would
    *overwrite an actual stored secret*: a name already backed by a saved
    key is spoken for even when no scope's config references it via a
    placeholder yet (e.g. it was saved moments ago by another entry's
    normalization/configure_provider call within the same command, before
    this scope's write has landed). Without it, the second entry's default
    name would look unclaimed and clobber the first instance's
    freshly-saved secret in keys.env. Single read, reused by the caller's
    loop -- not re-read per provider entry.
    See docs/designs/provider-instance-credentials.md §5.4.1.

    Do NOT use this for the add paths' collision detection: a keys.env-only
    leftover means no instance owns the name (§5.4.4 warn-and-reuse), not
    that a collision exists -- use ``_config_claimed_env_vars`` there.
    """
    return _config_claimed_env_vars(settings) | KeyManager().stored_keys()


def _suggest_instance_env_var(
    module_id: str, instance_id: str, claimed: set[str]
) -> str:
    """``<TYPE_PREFIX>_<ID-SUFFIX>_API_KEY``, NFC-normalized then sanitized
    to ``^[A-Z_][A-Z0-9_]*$``, de-duplicated against ``claimed``. E.g.
    ``(anthropic, anthropic-fable) -> ANTHROPIC_FABLE_API_KEY``.

    Raises ``ValueError`` if the sanitized ID-SUFFIX is empty, or if the
    resulting suggestion already collides with a claimed name after
    normalization -- e.g. two ids differing only in separator style
    (``anthropic-fable`` vs ``anthropic_fable``) would otherwise sanitize to
    the identical suggestion and silently re-create the exact collision
    this fix exists to prevent. Must fail loudly here, never emit an
    invalid or re-colliding name.

    See docs/designs/provider-instance-credentials.md §5.4.2.
    """
    display = module_id[9:] if module_id.startswith("provider-") else module_id
    type_prefix = _sanitize_env_token(display)

    norm_instance = _normalize_id(instance_id)
    # Strip a leading "<display>[-_ ]" prefix so (anthropic, anthropic-fable)
    # yields suffix "fable" rather than duplicating the type name. If the id
    # IS (only) the display name -- with or without trailing separators --
    # this consumes it entirely, correctly producing an empty suffix below:
    # that id carries no distinguishing information and must raise, not
    # fall back to re-using the whole (undistinguishing) original string.
    suffix_source = re.sub(
        rf"^{re.escape(display)}[-_\s]*", "", norm_instance, flags=re.IGNORECASE
    )
    id_suffix = _sanitize_env_token(suffix_source)

    if not id_suffix:
        raise ValueError(
            f"Instance id {instance_id!r} doesn't produce a usable "
            "credential variable name (it sanitizes to an empty suffix). "
            "Please choose a more distinct id."
        )

    suggested = f"{type_prefix}_{id_suffix}_API_KEY"
    if suggested in claimed:
        raise ValueError(
            f"Instance id {instance_id!r} doesn't produce a usable "
            f"credential variable name (it sanitizes to {suggested}, which "
            "is already in use by another instance). Please choose a more "
            "distinct id."
        )
    return suggested


def normalize_provider_secrets(
    settings_obj: AppSettings, scope_settings: dict[str, Any], scope: Scope
) -> None:
    """Rewrite any literal plaintext secret in ``scope_settings``'s provider
    entries into a ``${VAR}`` placeholder backed by ``~/.amplifier/keys.env``.

    Called synchronously from ``AppSettings._write_scope``, before its own
    atomic write proceeds -- this is the single bypass-proof funnel used by
    all provider write call sites, so no scope's settings.yaml can ever end
    up holding a literal secret value. Applies universally to every scope
    (global, project, local): this is a deliberate, confirmed decision, not
    scope-conditional.

    Mutates ``scope_settings`` in place. If a literal is found but no
    usable env var name can be derived for it, lets the underlying
    ``ValueError`` from ``_suggest_instance_env_var`` propagate so the
    caller's write aborts loudly rather than proceeding with an
    undecided/possibly-colliding name -- ``_write_scope``'s atomic write
    hasn't run yet at that point, so the old scope file is untouched.

    See docs/designs/provider-instance-credentials.md.
    """
    providers = (scope_settings.get("config") or {}).get("providers")
    if not isinstance(providers, list) or not providers:
        return

    # Computed lazily, at most once per call, on the FIRST literal secret
    # actually found -- entries normalized earlier in this same batch must
    # also be visible to entries normalized later in it, so two literals
    # sharing a default name in one write don't clobber each other. See
    # _claimed_env_vars' own cross-scope aggregation. Deferred (rather than
    # eager) so a batch containing no literals at all -- the common case --
    # never touches keys.env or constructs a KeyManager.
    claimed: set[str] | None = None
    batch_claimed: set[str] = set()
    key_manager: KeyManager | None = None

    for entry in providers:
        if not isinstance(entry, dict):
            continue

        raw_module_id = entry.get("module")
        raw_entry_id = entry.get("id")

        if not isinstance(raw_module_id, str):
            unresolved_label = raw_entry_id or raw_module_id
            console.print(
                f"[yellow]\u26a0 Could not resolve provider module "
                f"'{escape(str(raw_module_id))}' for entry "
                f"'{escape(str(unresolved_label))}' -- skipping "
                "plaintext-secret scan for this entry.[/yellow]"
            )
            continue
        module_id = raw_module_id

        # Hoisted once so a genuine resolution failure (module can't be
        # loaded/instantiated -- e.g. a stale/broken install) and the
        # normal case of a provider with no secret ConfigField at all
        # (OAuth-based providers) don't both call get_provider_info() and
        # don't get conflated into the same loud warning.
        provider_info = get_provider_info(module_id)
        if provider_info is None:
            unresolved_label = raw_entry_id or module_id
            console.print(
                f"[yellow]\u26a0 Could not resolve provider module "
                f"'{escape(module_id)}' for entry "
                f"'{escape(str(unresolved_label))}' -- skipping "
                "plaintext-secret scan for this entry.[/yellow]"
            )
            continue
        entry_label: str = str(raw_entry_id) if raw_entry_id else module_id

        field_id = _secret_field_id_for_info(provider_info)
        if field_id is None:
            # Not a failure -- this provider type simply has no secret
            # ConfigField to scan (e.g. an OAuth-based provider with no
            # api_key field at all). Debug-only; never a user-facing
            # warning for the normal case.
            logger.debug(
                "normalize_provider_secrets: provider module '%s' (entry "
                "'%s') has no secret ConfigField -- skipping "
                "plaintext-secret scan for this entry.",
                module_id,
                entry_label,
            )
            continue

        entry_config = entry.get("config") or {}
        value = entry_config.get(field_id)
        if not value:
            continue
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            continue

        # A literal secret was found -- move it to keys.env. Compute
        # `claimed` now, on first use (see comment above `claimed`'s
        # declaration).
        if claimed is None:
            claimed = _claimed_env_vars(settings_obj)
        already_claimed = claimed | batch_claimed
        default_name = _secret_env_var_for(module_id)
        if default_name and default_name not in already_claimed:
            chosen = default_name
        else:
            chosen = _suggest_instance_env_var(module_id, entry_label, already_claimed)

        if key_manager is None:
            key_manager = KeyManager()
        key_manager.save_key(chosen, value)

        entry["config"][field_id] = f"${{{chosen}}}"
        batch_claimed.add(chosen)

        message = (
            f"Note: found a plaintext credential for instance "
            f"'{entry_label}' \u2014 moved it to keys.env as {chosen}; "
            f"settings now reference it by ${{{chosen}}}."
        )
        if scope == "project":
            message += (
                "  (project settings are team-shared/committed to git "
                "\u2014 the secret is now only in keys.env, not in the "
                "committed file.)"
            )
        console.print(message)


def _prompt_for_field(
    field: dict[str, Any],
    key_manager: KeyManager,
    collected_config: dict[str, Any],
    existing_config: dict[str, Any] | None = None,
    env_var_overrides: dict[str, str] | None = None,
    credential_binding_modes: dict[str, str] | None = None,
) -> tuple[str, Any]:
    """Prompt user for a single config field value.

    Args:
        field: ConfigField as dict
        key_manager: Key manager for secrets
        collected_config: Config values collected so far
        existing_config: Optional existing config for defaults when re-configuring
        env_var_overrides: Optional map of ``{type_default_env_var:
            instance_env_var}``, resolved by the caller (design §5.2/§5.3)
            when a same-type instance needs a distinct credential name from
            the provider type's declared default.
        credential_binding_modes: Optional map of ``{resolved_env_var: mode}``
            where ``mode`` is ``"shared"`` or ``"separate"``. Resolved by the
            caller when adding/editing a same-type instance:

            * ``"shared"`` -- this instance reuses an existing instance's
              credential env var. The secret field is NEVER prompted for or
              overwritten; only its ``${VAR}`` placeholder is persisted, so
              both instances resolve the same secret at runtime.
            * ``"separate"`` -- this instance owns a distinct credential env
              var. It is prompted as usual, but may be left blank to persist
              the placeholder UNSET for runtime injection (shell / CI / DTU
              passthrough) instead of hard-failing on a required secret.

            Mechanism only: this function does not decide the mode, it just
            honors whatever it is handed. Fields with no matching entry keep
            today's behavior exactly (fully backward compatible).

    Returns:
        Tuple of (field_id, value)
    """
    field_id = field["id"]
    field_type = field.get("field_type", "text")
    prompt_text = field["prompt"]
    declared_env_var = field.get("env_var")
    env_var = (
        (env_var_overrides or {}).get(declared_env_var, declared_env_var)
        if declared_env_var
        else declared_env_var
    )
    binding_mode = (credential_binding_modes or {}).get(env_var) if env_var else None
    default = field.get("default")
    required = field.get("required", True)

    # Check for existing value in environment (KeyManager loads keys into env)
    existing_env_value = None
    if env_var:
        existing_env_value = os.environ.get(env_var)

    # Check for value from existing config (for re-configuration)
    # Resolve ${VAR} references to actual values
    existing_config_value = None
    if existing_config and field_type != "secret":
        raw_value = existing_config.get(field_id)
        if raw_value:
            existing_config_value = _resolve_config_value(raw_value)

    # Combined existing value: env var takes precedence over config value
    existing_value = existing_env_value or existing_config_value

    # Show field info
    console.print()
    console.print(f"[bold]{field['display_name']}[/bold]")
    if existing_value:
        if field_type == "secret":
            console.print(
                "  [dim](Found in environment/keyring - will use if you don't configure)[/dim]"
            )
        else:
            console.print(f"  [dim](Found: {existing_value})[/dim]")

    # Handle different field types
    if field_type == "boolean":
        if existing_value:
            default_bool = str(existing_value).lower() in ("true", "1", "yes")
            value = Confirm.ask(prompt_text, default=default_bool)
            return field_id, str(value).lower()

        if default is None:
            # No declared default and no existing value: don't force a
            # True/False choice on the user. Passing default=None to
            # Confirm.ask lets Enter mean "leave unset" (returns None here,
            # so the caller omits the key) -- matching the text-field "may
            # be absent" contract. Previously this branch computed
            # `default_bool = None and ...` -> None, which Confirm.ask still
            # accepted as a default, and the string "none" got written to
            # config on Enter.
            value = Confirm.ask(
                f"{prompt_text} [dim](leave blank to use provider/model default)[/dim]",
                default=None,
            )
            if value is None:
                return field_id, None
            return field_id, str(value).lower()

        default_bool = default.lower() in ("true", "1", "yes")
        value = Confirm.ask(prompt_text, default=default_bool)
        return field_id, str(value).lower()

    if field_type == "choice":
        choices = field.get("choices", [])
        if choices:
            # A field with no declared default and not required signals
            # "leave unset -- use provider/model default" (e.g. provider-openai's
            # prompt_cache_retention/text_verbosity). Previously this collapsed
            # to `default_choice = "1"` and force-wrote choices[0] on every
            # Enter, with no way to actually leave the key unset.
            allow_unset = default is None and not required

            display_choices = list(choices)
            unset_choice = "(leave unset - use provider/model default)"
            if allow_unset:
                display_choices = [unset_choice] + display_choices

            console.print(f"{prompt_text}")
            for idx, choice_label in enumerate(display_choices, 1):
                console.print(f"  [{idx}] {choice_label}")

            choice_map = {str(i): c for i, c in enumerate(display_choices, 1)}
            offset = 1 if allow_unset else 0

            # Default selection: the "unset" option if allowed (position 1),
            # else choices[0] (original hard-coded behavior for
            # required/defaulted fields) -- both are position "1".
            default_choice = "1"

            # Existing value or a real field default takes priority as the
            # pre-selected option, positioned after the prepended unset entry.
            effective_value = existing_value or default
            if effective_value and effective_value in choices:
                default_choice = str(choices.index(effective_value) + 1 + offset)

            selected = Prompt.ask(
                "Choice", choices=list(choice_map.keys()), default=default_choice
            )
            if allow_unset and selected == "1" and choice_map[selected] == unset_choice:
                return field_id, None
            return field_id, choice_map[selected]
        # No choices defined, fall through to text

    if field_type == "secret":
        # Shared credential binding: this instance reuses another instance's
        # credential env var. Never prompt for or overwrite its stored value
        # -- just persist the placeholder so both instances resolve the same
        # secret at runtime.
        if env_var and binding_mode == "shared":
            console.print(
                f"  [dim]Reusing existing credential {env_var}; its stored "
                f"value is left untouched.[/dim]"
            )
            return field_id, f"${{{env_var}}}"

        prompt_suffix = " (press Enter to keep existing)" if existing_value else ""
        value = Prompt.ask(f"{prompt_text}{prompt_suffix}", password=True, default="")

        if value:
            # User provided new value - save it
            if env_var:
                key_manager.save_key(env_var, value)
                # Also set env var so it's immediately available for model discovery
                os.environ[env_var] = value
                console.print("[green]✓ Saved[/green]")
            return field_id, f"${{{env_var}}}" if env_var else value
        if existing_value:
            console.print("[green]✓ Using existing[/green]")
            return field_id, f"${{{env_var}}}" if env_var else existing_value
        # Separate credential binding left blank: persist the placeholder
        # UNSET so the value can be injected from the environment at runtime
        # (shell / CI / DTU passthrough) instead of hard-failing. Scoped to an
        # explicit per-instance binding -- a first-instance/normal required
        # secret still errors, preserving today's setup guardrail.
        if env_var and binding_mode == "separate":
            console.print(
                f"  [yellow]No value entered; {env_var} will be resolved from "
                f"the environment at runtime.[/yellow]"
            )
            return field_id, f"${{{env_var}}}"
        if required:
            console.print("[red]Error: Required field[/red]")
            raise ValueError(f"{field['display_name']} is required")
        return field_id, None

    # Default: text field
    effective_default = existing_value or default or ""
    value = Prompt.ask(prompt_text, default=effective_default)

    if not value and required:
        console.print("[red]Error: Required field[/red]")
        raise ValueError(f"{field['display_name']} is required")

    # Save to keyring if it has an env_var
    if value and env_var:
        key_manager.save_key(env_var, value)
        # Also set env var so it's immediately available for model discovery
        os.environ[env_var] = value
        console.print("[green]✓ Saved[/green]")
        return field_id, f"${{{env_var}}}"

    return field_id, value if value else None


def _apply_non_interactive_field(
    field: dict[str, Any],
    field_id: str,
    collected_config: dict[str, Any],
    existing_config: dict[str, Any] | None,
    env_var_overrides: dict[str, str] | None,
    credential_binding_modes: dict[str, str] | None,
    settings: AppSettings | None,
) -> None:
    """Resolve one config_field's value in ``non_interactive`` mode and set it
    into ``collected_config`` in place (env var > existing config > declared
    default, honoring an explicit credential binding mode).

    Extracted from ``configure_provider()``, where Phase 1 (pre-model) and
    Phase 3 (post-model) fields previously duplicated this exact 42-line
    block verbatim.

    Raises:
        ValueError: Non-interactive configuration would reuse a credential
            env var already claimed by another configured instance (design
            §5.4.5), unless an explicit per-instance binding is in play.
    """
    declared = field.get("env_var")
    env_var = (
        (env_var_overrides or {}).get(declared, declared) if declared else declared
    )
    mode = (credential_binding_modes or {}).get(env_var) if env_var else None
    # Fail loud instead of silently reusing the type default when it's
    # already claimed by another instance (design §5.4.5). An explicit
    # per-instance binding (shared reuse or separate) is intentional, so
    # it is exempt from this guard.
    if (
        settings is not None
        and declared
        and env_var == declared
        and declared not in (env_var_overrides or {})
        and mode is None
        and declared in _claimed_env_vars(settings)
    ):
        raise ValueError(
            f"Non-interactive configuration would reuse the same "
            f"credential env var ({declared}) as another "
            f"configured instance. Pass an explicit "
            f"env_var_overrides mapping for this instance "
            f"instead of relying on the type default."
        )
    if mode is not None:
        # Explicit per-instance binding: persist the placeholder whether or
        # not the value is set in the environment. A separate binding may be
        # intentionally unset for runtime injection; a shared one reuses an
        # existing instance's key.
        collected_config[field_id] = f"${{{env_var}}}"
    elif env_var and os.environ.get(env_var):
        collected_config[field_id] = f"${{{env_var}}}"
    elif existing_config and field_id in existing_config:
        collected_config[field_id] = existing_config[field_id]
    elif field.get("default"):
        collected_config[field_id] = field["default"]


def configure_provider(
    provider_id: str,
    key_manager: KeyManager,
    existing_config: dict[str, Any] | None = None,
    non_interactive: bool = False,
    env_var_overrides: dict[str, str] | None = None,
    credential_binding_modes: dict[str, str] | None = None,
    settings: AppSettings | None = None,
) -> dict[str, Any] | None:
    """Configure a provider using its self-declared config_fields.

    Reads config_fields from the provider's get_info() method and prompts accordingly.
    Also prompts for model selection using the provider's list_models().

    When existing_config is provided (re-configuring), existing values are used as
    defaults so users can press Enter to keep their previous choices.

    Args:
        provider_id: Provider identifier (e.g., "anthropic", "openai", "azure-openai")
        key_manager: Key manager instance for API key storage
        existing_config: Optional existing config for defaults when re-configuring
        non_interactive: If True, skip all prompts and use CLI values/env vars/defaults only
        env_var_overrides: Optional map of ``{type_default_env_var:
            instance_env_var}``. Resolved by the caller (design §5.2/§5.3)
            when a same-type instance needs a distinct credential name.
            Mechanism only -- this function does not compute collisions,
            it just uses whatever name it is handed.
        credential_binding_modes: Optional map of ``{resolved_env_var: mode}``
            (``"shared"`` or ``"separate"``) threaded into ``_prompt_for_field``
            so a reused credential env var is never re-prompted/overwritten and
            a separate one may be left unset for runtime injection. Mechanism
            only -- the caller decides the mode.
        settings: Optional AppSettings, used only to detect a same-type
            credential collision in ``non_interactive`` mode and fail loudly
            instead of silently reusing the type default (design §5.4.5).
            When omitted, the non-interactive fail-loud check is skipped
            (fully backward compatible with existing callers).

    Returns:
        Provider configuration dict, or None if configuration failed
    """
    try:
        # Remove "provider-" prefix if present
        if provider_id.startswith("provider-"):
            provider_id = provider_id[9:]

        # Get provider info with config_fields
        info = get_provider_info(provider_id)
        if not info:
            console.print(f"[red]Error: Could not load provider '{provider_id}'[/red]")
            return None

        display_name = info.get("display_name", provider_id)
        if not non_interactive:
            console.print(f"\n[bold]Configuring {display_name}[/bold]")

        collected_config: dict[str, Any] = {}

        # Split config_fields into pre-model and post-model phases
        # Pre-model fields are processed first (credentials, endpoints, etc.)
        # Post-model fields are processed after model selection (model-dependent options)
        config_fields = info.get("config_fields", [])
        pre_model_fields = [
            f for f in config_fields if not f.get("requires_model", False)
        ]
        post_model_fields = [f for f in config_fields if f.get("requires_model", False)]

        # Phase 1: Process pre-model config_fields (credentials, base_url, etc.)
        for field in pre_model_fields:
            field_id = field["id"]

            # Check show_when conditions
            if not _should_show_field(field, collected_config):
                continue

            # In non-interactive mode, use env var or existing config value
            if non_interactive:
                _apply_non_interactive_field(
                    field,
                    field_id,
                    collected_config,
                    existing_config,
                    env_var_overrides,
                    credential_binding_modes,
                    settings,
                )
                continue

            # Prompt for the field (pass existing_config for defaults)
            field_id, value = _prompt_for_field(
                field,
                key_manager,
                collected_config,
                existing_config,
                env_var_overrides,
                credential_binding_modes,
            )
            if value is not None:
                collected_config[field_id] = value

        # Phase 2: Model selection step
        if "deployment_name" in collected_config:
            # Azure OpenAI: deployment_name IS the model
            collected_config["default_model"] = collected_config["deployment_name"]
            if not non_interactive:
                console.print(
                    f"\n[bold]Default Model[/bold]: {collected_config['default_model']} (from deployment)"
                )
        elif non_interactive:
            # In non-interactive mode, use existing config or skip
            if existing_config and "default_model" in existing_config:
                collected_config["default_model"] = existing_config["default_model"]
            # If no model available, continue without one (provider may have a default)
        else:
            # Get default model from existing config ONLY (no hard-coded provider defaults)
            # This ensures fresh configs require user to choose, while re-configs default to previous choice
            default_model = (
                existing_config.get("default_model") if existing_config else None
            )

            # Wizard login step: for a provider that declares an "auth:*"
            # capability (e.g. "auth:oauth-device-code"), offer a
            # one-time browser login BEFORE fetching models, so a
            # first-time user gets the live model catalog instead of a
            # stale/empty fallback list. Reuses ONE provider instance for
            # both the login check and the model fetch below -- see
            # _maybe_login_provider()'s docstring. A provider with no
            # "auth:*" capability (the common case today) is unaffected:
            # login_provider is None and prefetched_models stays None, so
            # _prompt_model_selection() below does its own (already-safe)
            # fetch exactly as before.
            prefetched_models = None
            login_provider = _maybe_login_provider(provider_id, info, collected_config)
            if login_provider is not None:
                with console.status(
                    "[dim]Fetching available models...[/dim]", spinner="dots"
                ):
                    prefetched_models = _safely_fetch_models_from_instance(
                        provider_id, login_provider
                    )

            # Prompt for model selection
            # Pass collected_config so providers can connect to real servers for dynamic discovery
            console.print()
            console.print("[bold]Default Model[/bold]")
            selected_model = _prompt_model_selection(
                provider_id,
                default_model,
                collected_config,
                models=prefetched_models,
            )
            # None is the strict abort sentinel (Ctrl-C / EOF during the
            # Choice prompt -- see _prompt_model_selection's docstring). It
            # is NOT the same as "" (user declined to enter a custom model
            # name, which is a valid "continue without a model" outcome).
            # Without this check, an interrupted model prompt fell through
            # to Phase 3, printed "configured", and saved an empty/partial
            # config -- matching the outer handler at the bottom of this
            # function and the precedent in commands/routing.py.
            if selected_model is None:
                console.print("\n[dim]Cancelled.[/dim]")
                return None
            if selected_model:
                collected_config["default_model"] = selected_model

        # Phase 3: Process post-model config_fields (model-dependent options)
        # These fields can use show_when to reference the selected model
        for field in post_model_fields:
            field_id = field["id"]

            # Check show_when conditions (now model is in collected_config)
            if not _should_show_field(field, collected_config):
                continue

            # In non-interactive mode, use env var or existing config value
            if non_interactive:
                _apply_non_interactive_field(
                    field,
                    field_id,
                    collected_config,
                    existing_config,
                    env_var_overrides,
                    credential_binding_modes,
                    settings,
                )
                continue

            # Prompt for the field (pass existing_config for defaults)
            field_id, value = _prompt_for_field(
                field,
                key_manager,
                collected_config,
                existing_config,
                env_var_overrides,
                credential_binding_modes,
            )
            if value is not None:
                collected_config[field_id] = value

        if not non_interactive:
            console.print(f"\n[green]✓ {display_name} configured[/green]")

        return collected_config
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        return None
