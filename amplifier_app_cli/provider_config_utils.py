"""Shared provider configuration gathering functions.

Provides generic configuration based on provider-declared config_fields.
Queries provider modules dynamically for model lists and config fields.
"""

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
from .provider_loader import get_provider_info
from .provider_loader import get_provider_models

console = Console()
logger = logging.getLogger(__name__)


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


def _secret_config_field(module_id: str) -> dict[str, Any] | None:
    """Return the provider type's secret ConfigField dict
    (``field_type == "secret"``), if any."""
    info = get_provider_info(module_id)
    if not info:
        return None
    for field in info.get("config_fields", []):
        if isinstance(field, dict) and field.get("field_type") == "secret":
            return field
    return None


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


def _claimed_env_vars(settings: AppSettings) -> set[str]:
    """Env-var names already spoken for, by ANY means, across ALL scopes
    (global, project, local, session): either referenced by a ``${VAR}``
    placeholder in some scope's provider config, OR already backed by a
    real, saved secret in ``~/.amplifier/keys.env``.

    Mirrors ``AppSettings.get_provider_overrides()``'s scope iteration
    order, but deliberately does NOT mirror its silent
    ``except Exception: pass`` error handling: a corrupt scope file here
    must be surfaced loudly, since silently under-counting claimed names
    would let a new instance claim an already-used env var and reintroduce
    Bug 3 through a different door.

    A literal (non-placeholder) config value claims nothing BY ITSELF --
    it's the presence of an actual saved key in keys.env (checked below,
    once per call) that claims a name, not the shape of the config value
    referencing it. This matters for the race where one instance's literal
    secret has just been normalized and saved to keys.env, but another
    entry's still-unprocessed literal in the same write batch hasn't been
    touched yet: without this, the second entry's default name would look
    unclaimed and clobber the first instance's freshly-saved secret in
    keys.env. See docs/designs/provider-instance-credentials.md §5.4.1.
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

    # Also claim any name already backed by a real, saved secret in
    # keys.env, even if no scope's config currently references it via a
    # placeholder yet (e.g. it was just saved moments ago by another
    # entry's normalization/configure_provider call within the same
    # command, before this scope's write has landed). Single read, reused
    # by the caller's loop -- not re-read per provider entry.
    claimed |= KeyManager().stored_keys()
    return claimed


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

        field_id = _secret_field_id_for(module_id)
        if field_id is None:
            unresolved_label = raw_entry_id or module_id
            console.print(
                f"[yellow]\u26a0 Could not resolve provider module "
                f"'{escape(module_id)}' for entry "
                f"'{escape(str(unresolved_label))}' -- skipping "
                "plaintext-secret scan for this entry.[/yellow]"
            )
            continue
        entry_label: str = str(raw_entry_id) if raw_entry_id else module_id

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
        else:
            default_bool = default and default.lower() in ("true", "1", "yes")

        value = Confirm.ask(prompt_text, default=default_bool)
        return field_id, str(value).lower()

    if field_type == "choice":
        choices = field.get("choices", [])
        if choices:
            console.print(f"{prompt_text}")
            for idx, choice in enumerate(choices, 1):
                console.print(f"  [{idx}] {choice}")

            # Use existing value first, then field default
            effective_value = existing_value or default
            default_choice = "1"
            if effective_value and effective_value in choices:
                default_choice = str(choices.index(effective_value) + 1)

            choice_map = {str(i): c for i, c in enumerate(choices, 1)}
            selected = Prompt.ask(
                "Choice", choices=list(choice_map.keys()), default=default_choice
            )
            return field_id, choice_map[selected]
        # No choices defined, fall through to text

    if field_type == "secret":
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


def configure_provider(
    provider_id: str,
    key_manager: KeyManager,
    model: str | None = None,
    endpoint: str | None = None,
    deployment: str | None = None,
    use_azure_cli: bool | None = None,
    existing_config: dict[str, Any] | None = None,
    non_interactive: bool = False,
    env_var_overrides: dict[str, str] | None = None,
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
        model: Optional model name (for CLI flag override)
        endpoint: Optional endpoint URL (for CLI flag override)
        deployment: Optional deployment name (Azure OpenAI only)
        use_azure_cli: Optional Azure CLI auth flag (Azure OpenAI only)
        existing_config: Optional existing config for defaults when re-configuring
        non_interactive: If True, skip all prompts and use CLI values/env vars/defaults only
        env_var_overrides: Optional map of ``{type_default_env_var:
            instance_env_var}``. Resolved by the caller (design §5.2/§5.3)
            when a same-type instance needs a distinct credential name.
            Mechanism only -- this function does not compute collisions,
            it just uses whatever name it is handed.
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

        # Build CLI overrides dict
        cli_overrides: dict[str, Any] = {}
        if model:
            cli_overrides["default_model"] = model
        if endpoint:
            cli_overrides["azure_endpoint"] = endpoint
            cli_overrides["base_url"] = endpoint
            cli_overrides["host"] = endpoint
        if deployment:
            cli_overrides["deployment_name"] = deployment
        if use_azure_cli is not None:
            cli_overrides["use_default_credential"] = str(use_azure_cli).lower()
            cli_overrides["use_managed_identity"] = str(use_azure_cli).lower()

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

            # Check if value provided via CLI override
            if field_id in cli_overrides and cli_overrides[field_id] is not None:
                collected_config[field_id] = cli_overrides[field_id]
                if not non_interactive:
                    console.print(
                        f"\n[bold]{field['display_name']}[/bold]: {cli_overrides[field_id]}"
                    )
                continue

            # In non-interactive mode, use env var or existing config value
            if non_interactive:
                declared = field.get("env_var")
                env_var = (
                    (env_var_overrides or {}).get(declared, declared)
                    if declared
                    else declared
                )
                # Fail loud instead of silently reusing the type default when
                # it's already claimed by another instance (design §5.4.5).
                if (
                    settings is not None
                    and declared
                    and env_var == declared
                    and declared not in (env_var_overrides or {})
                    and declared in _claimed_env_vars(settings)
                ):
                    raise ValueError(
                        f"Non-interactive configuration would reuse the same "
                        f"credential env var ({declared}) as another "
                        f"configured instance. Pass an explicit "
                        f"env_var_overrides mapping for this instance "
                        f"instead of relying on the type default."
                    )
                if env_var and os.environ.get(env_var):
                    collected_config[field_id] = f"${{{env_var}}}"
                elif existing_config and field_id in existing_config:
                    collected_config[field_id] = existing_config[field_id]
                elif field.get("default"):
                    collected_config[field_id] = field["default"]
                continue

            # Prompt for the field (pass existing_config for defaults)
            field_id, value = _prompt_for_field(
                field, key_manager, collected_config, existing_config, env_var_overrides
            )
            if value is not None:
                collected_config[field_id] = value

        # Phase 2: Model selection step
        # Check if model was provided via CLI override
        if "default_model" in cli_overrides:
            collected_config["default_model"] = cli_overrides["default_model"]
            if not non_interactive:
                console.print(
                    f"\n[bold]Default Model[/bold]: {cli_overrides['default_model']}"
                )
        elif "deployment_name" in collected_config:
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

            # Prompt for model selection
            # Pass collected_config so providers can connect to real servers for dynamic discovery
            console.print()
            console.print("[bold]Default Model[/bold]")
            selected_model = _prompt_model_selection(
                provider_id, default_model, collected_config
            )
            if selected_model:
                collected_config["default_model"] = selected_model

        # Phase 3: Process post-model config_fields (model-dependent options)
        # These fields can use show_when to reference the selected model
        for field in post_model_fields:
            field_id = field["id"]

            # Check show_when conditions (now model is in collected_config)
            if not _should_show_field(field, collected_config):
                continue

            # Check if value provided via CLI override
            if field_id in cli_overrides and cli_overrides[field_id] is not None:
                collected_config[field_id] = cli_overrides[field_id]
                if not non_interactive:
                    console.print(
                        f"\n[bold]{field['display_name']}[/bold]: {cli_overrides[field_id]}"
                    )
                continue

            # In non-interactive mode, use env var or existing config value
            if non_interactive:
                declared = field.get("env_var")
                env_var = (
                    (env_var_overrides or {}).get(declared, declared)
                    if declared
                    else declared
                )
                # Fail loud instead of silently reusing the type default when
                # it's already claimed by another instance (design §5.4.5).
                if (
                    settings is not None
                    and declared
                    and env_var == declared
                    and declared not in (env_var_overrides or {})
                    and declared in _claimed_env_vars(settings)
                ):
                    raise ValueError(
                        f"Non-interactive configuration would reuse the same "
                        f"credential env var ({declared}) as another "
                        f"configured instance. Pass an explicit "
                        f"env_var_overrides mapping for this instance "
                        f"instead of relying on the type default."
                    )
                if env_var and os.environ.get(env_var):
                    collected_config[field_id] = f"${{{env_var}}}"
                elif existing_config and field_id in existing_config:
                    collected_config[field_id] = existing_config[field_id]
                elif field.get("default"):
                    collected_config[field_id] = field["default"]
                continue

            # Prompt for the field (pass existing_config for defaults)
            field_id, value = _prompt_for_field(
                field, key_manager, collected_config, existing_config, env_var_overrides
            )
            if value is not None:
                collected_config[field_id] = value

        if not non_interactive:
            console.print(f"\n[green]✓ {display_name} configured[/green]")

        return collected_config
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        return None
