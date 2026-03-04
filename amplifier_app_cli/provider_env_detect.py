"""Provider detection from environment variables."""

import os
from importlib.metadata import entry_points


# Known credential env vars for each provider
# Module name -> list of env vars that indicate the provider is configured
PROVIDER_CREDENTIAL_VARS: dict[str, list[str]] = {
    "provider-anthropic": ["ANTHROPIC_API_KEY"],
    "provider-openai": ["OPENAI_API_KEY"],
    "provider-azure-openai": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
    "provider-gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "provider-ollama": [],  # Ollama doesn't require credentials
}


def detect_provider_from_env() -> str | None:
    """Detect configured provider from environment variables.

    Checks installed provider modules against known credential env vars.
    Returns the first provider that has credentials configured.

    Returns:
        module_id if a provider's credentials are found, None otherwise.
    """
    # Get installed provider modules
    eps = entry_points(group="amplifier.modules")
    installed_providers = {ep.name for ep in eps if ep.name.startswith("provider-")}

    # Check each known provider (in priority order) for credentials
    for provider_id, env_vars in PROVIDER_CREDENTIAL_VARS.items():
        # Skip if provider not installed
        if provider_id not in installed_providers:
            continue

        # Skip providers with no required credentials (like ollama)
        if not env_vars:
            continue

        # Check if ALL required env vars are set
        if all(os.environ.get(var) for var in env_vars):
            return provider_id

    # Check for ollama last (since it doesn't require credentials)
    if "provider-ollama" in installed_providers:
        return "provider-ollama"

    return None
