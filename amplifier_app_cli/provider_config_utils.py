"""Shared provider configuration collection functions.

Provides generic configuration based on provider-declared config_fields.
Queries provider modules dynamically for model lists and config fields.
"""

import logging
import os
from typing import Any

from rich.console import Console
from rich.prompt import Confirm
from rich.prompt import Prompt

from .key_manager import KeyManager
from .provider_loader import get_provider_info
from .provider_loader import get_provider_models

console = Console()
logger = logging.getLogger(__name__)


def _prompt_model_selection(
    provider_id: str,
    default_model: str | None = None,
    collected_config: dict[str, Any] | None = None,
) -> str:
    """Prompt user to select a model from provider's available models.

    Queries the provider module for available models and presents a selection menu.
    Falls back to custom input if no models available.

    Args:
        provider_id: Provider ID (e.g., "anthropic", "openai")
        default_model: Optional default model from existing config (NOT hard-coded provider default)
        collected_config: Optional config values collected from user (base_url, host, etc.)
            Passed to provider for dynamic model discovery from real servers.

    Returns:
        Selected model name
    """
    models = get_provider_models(provider_id, collected_config=collected_config)

    if not models:
        # No models available - show helpful message and prompt for custom input
        # Provider-specific hints for common local providers
        if provider_id in ("ollama", "provider-ollama"):
            console.print(
                "  [dim](No models found on Ollama server. Run 'ollama pull <model>' to install models.)[/dim]"
            )
        elif provider_id in ("vllm", "provider-vllm"):
            console.print("  [dim](Could not connect to vLLM server or no models available.)[/dim]")
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
            key_caps = [c for c in model_info.capabilities if c in ("fast", "thinking", "vision")]
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
        choice = Prompt.ask("Choice", choices=list(model_map.keys()), default=default_choice)
    else:
        choice = Prompt.ask("Choice", choices=list(model_map.keys()))

    if model_map[choice] == "__custom__":
        return Prompt.ask("Model name", default=default_model or "")

    return model_map[choice]


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
        - "startswith:prefix" - Match if actual value starts with prefix
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
        if expected_str.startswith("contains:"):
            pattern = expected_str[9:]  # Remove "contains:" prefix
            if pattern not in actual_value:
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


def _prompt_for_field(
    field: dict[str, Any],
    key_manager: KeyManager,
    collected_config: dict[str, Any],
    existing_config: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """Prompt user for a single config field value.

    Args:
        field: ConfigField as dict
        key_manager: Key manager for secrets
        collected_config: Config values collected so far
        existing_config: Optional existing config for defaults when re-configuring

    Returns:
        Tuple of (field_id, value)
    """
    field_id = field["id"]
    field_type = field.get("field_type", "text")
    prompt_text = field["prompt"]
    env_var = field.get("env_var")
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
            console.print("  [dim](Found in environment/keyring - will use if you don't configure)[/dim]")
        else:
            console.print(f"  [dim](Found: {existing_value})[/dim]")

    # Handle different field types
    if field_type == "boolean":
        if existing_value:
            default_bool = existing_value.lower() in ("true", "1", "yes")
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
            selected = Prompt.ask("Choice", choices=list(choice_map.keys()), default=default_choice)
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

    Returns:
        Provider configuration dict, or None if configuration failed
    """
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
    console.print(f"\n[bold]Configuring {display_name}[/bold]")

    collected_config: dict[str, Any] = {}

    # Split config_fields into pre-model and post-model phases
    # Pre-model fields are processed first (credentials, endpoints, etc.)
    # Post-model fields are processed after model selection (model-dependent options)
    config_fields = info.get("config_fields", [])
    pre_model_fields = [f for f in config_fields if not f.get("requires_model", False)]
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
            console.print(f"\n[bold]{field['display_name']}[/bold]: {cli_overrides[field_id]}")
            continue

        # Prompt for the field (pass existing_config for defaults)
        field_id, value = _prompt_for_field(field, key_manager, collected_config, existing_config)
        if value is not None:
            collected_config[field_id] = value

    # Phase 2: Model selection step
    # Check if model was provided via CLI override
    if "default_model" in cli_overrides:
        collected_config["default_model"] = cli_overrides["default_model"]
        console.print(f"\n[bold]Default Model[/bold]: {cli_overrides['default_model']}")
    elif "deployment_name" in collected_config:
        # Azure OpenAI: deployment_name IS the model
        collected_config["default_model"] = collected_config["deployment_name"]
        console.print(f"\n[bold]Default Model[/bold]: {collected_config['default_model']} (from deployment)")
    else:
        # Get default model from existing config ONLY (no hard-coded provider defaults)
        # This ensures fresh configs require user to choose, while re-configs default to previous choice
        default_model = existing_config.get("default_model") if existing_config else None

        # Prompt for model selection
        # Pass collected_config so providers can connect to real servers for dynamic discovery
        console.print()
        console.print("[bold]Default Model[/bold]")
        selected_model = _prompt_model_selection(provider_id, default_model, collected_config)
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
            console.print(f"\n[bold]{field['display_name']}[/bold]: {cli_overrides[field_id]}")
            continue

        # Prompt for the field (pass existing_config for defaults)
        field_id, value = _prompt_for_field(field, key_manager, collected_config, existing_config)
        if value is not None:
            collected_config[field_id] = value

    console.print(f"\n[green]✓ {display_name} configured[/green]")

    return collected_config
