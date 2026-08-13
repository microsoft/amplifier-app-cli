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
    "provider-github-copilot": ["GITHUB_TOKEN"],
    "provider-ollama": [],  # Ollama doesn't require credentials
}


class CredentialedProviderModuleMissingError(RuntimeError):
    """Raised by detect_provider_from_env() when environment credentials
    point at a provider whose module is not installed/importable.

    GAP-003: previously, "module not installed" and "no credentials set"
    were treated identically -- both simply `continue`d past the provider
    in the priority loop, falling through to the Ollama fallback (or to
    None) with no distinction and no diagnostic. That silently discarded a
    real, valid API key: a user with `ANTHROPIC_API_KEY` set but a
    not-yet-installed (or install-failed) `provider-anthropic` module got
    auto-configured onto Ollama instead, then hit a misleading
    `ConnectionError` against a local server that was never running, with
    nothing telling them their real key was ever seen.

    These are very different situations and must not be handled the same
    way. "No credentials for this provider" is silence-worthy -- there is
    nothing to report. "Credentials are present but the module can't be
    used" is a loud, actionable condition: the user has a working key for
    a provider that Amplifier chose not to use, for a reason it can name.

    This exception is that loud condition. Catching it and reporting it
    (see `auto_init_from_env`) replaces the silent fall-through -- it does
    NOT replace the legitimate case of a user with genuinely no cloud
    credentials landing on Ollama, which still happens quietly and
    correctly when this exception is never raised.
    """

    def __init__(self, provider_id: str, env_vars: list[str]):
        self.provider_id = provider_id
        self.env_vars = env_vars
        display = provider_id.removeprefix("provider-")
        vars_str = " and ".join(env_vars)
        super().__init__(
            f"Found credentials for {display} ({vars_str}) but the "
            f"'{provider_id}' module is not installed or could not be "
            f"imported. Run 'amplifier provider install {display}' (or "
            f"'amplifier provider add') to fix this. Refusing to silently "
            f"fall back to a different provider you didn't configure."
        )


def detect_provider_from_env() -> str | None:
    """Detect configured provider from environment variables.

    Checks installed provider modules against known credential env vars.
    Returns the first provider that has credentials configured AND whose
    module is actually installed.

    Raises:
        CredentialedProviderModuleMissingError: if a provider has all of
            its credential env vars set but its module is not installed.
            This must never be treated the same as "no credentials" --
            see the exception's docstring for why (GAP-003).

    Returns:
        module_id if a provider's credentials are found and its module is
        installed; "provider-ollama" if nothing else matched and Ollama's
        module is installed (the genuinely-no-cloud-credentials case);
        None otherwise.
    """
    # Get installed provider modules
    eps = entry_points(group="amplifier.modules")
    installed_providers = {ep.name for ep in eps if ep.name.startswith("provider-")}

    # Providers whose credentials ARE fully present in the environment but
    # whose module is NOT installed. Recorded rather than silently skipped
    # (GAP-003) -- these must block the Ollama fallback, not fall through
    # to it, because falling through would discard a real, valid key with
    # no indication it was ever seen.
    missing_but_credentialed: list[tuple[str, list[str]]] = []

    # Check each known provider (in priority order) for credentials
    for provider_id, env_vars in PROVIDER_CREDENTIAL_VARS.items():
        # Providers with no required credentials (like ollama) are handled
        # by the dedicated check below, not by this credential loop.
        if not env_vars:
            continue

        # No credentials set for this provider at all -- genuinely nothing
        # to report, move on to the next candidate.
        if not all(os.environ.get(var) for var in env_vars):
            continue

        # Credentials ARE present. If the module isn't installed, this is
        # the GAP-003 condition: record it and keep checking lower-priority
        # providers (one of them may be both credentialed and installed),
        # but never silently fall through to Ollama once anything has been
        # recorded here.
        if provider_id not in installed_providers:
            missing_but_credentialed.append((provider_id, env_vars))
            continue

        return provider_id

    if missing_but_credentialed:
        provider_id, env_vars = missing_but_credentialed[0]
        raise CredentialedProviderModuleMissingError(provider_id, env_vars)

    # Check for ollama last (since it doesn't require credentials) -- only
    # reached when no provider anywhere in PROVIDER_CREDENTIAL_VARS had
    # credentials set. This is the genuinely-no-cloud-credentials case and
    # must stay quiet and correct.
    if "provider-ollama" in installed_providers:
        return "provider-ollama"

    return None
