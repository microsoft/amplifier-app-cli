"""Provider detection from environment variables."""

import os
from importlib.metadata import entry_points

from .provider_sources import is_provider_module_installed

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

# Providers whose credential env vars are typically injected by the *platform*
# rather than deliberately exported by a user.
#
# Every other entry above is a variable somebody consciously set. Exporting
# ANTHROPIC_API_KEY is an explicit statement of intent, and that intent is
# exactly what makes silently overriding it (GAP-003) worth raising on.
# GITHUB_TOKEN is not that: GitHub Actions injects it into *every* job
# automatically, so its presence carries no user intent whatsoever.
#
# Treating it as intent is actively harmful. The GAP-003 raise conditions
# (credential present + module absent) are satisfied by default in any GitHub
# Actions run that doesn't happen to have provider-github-copilot installed --
# which is most of them. Without this carve-out, the fix converts a working
# Ollama fallback into a hard failure across workflows nobody touched, and the
# error tells them to install a provider they never asked for.
#
# These providers are still selected normally when their module IS installed.
# An ambient credential simply cannot escalate a *missing* module into an
# error, because there is no user decision being overridden.
AMBIENT_CREDENTIAL_PROVIDERS: frozenset[str] = frozenset(
    {
        "provider-github-copilot",
    }
)


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
    # Get installed provider modules.
    #
    # A registered entry point is necessary but NOT sufficient evidence that a
    # provider is usable. Providers are installed editable, so anything that
    # removes the module cache while leaving site-packages intact (notably
    # `amplifier reset --remove cache` on a non-`uv tool` install) strands the
    # `.dist-info` -- and therefore the entry point -- pointing at a directory
    # that no longer exists. Such a provider still advertises itself but fails
    # to import.
    #
    # That stranded state is exactly the one this fix exists to diagnose, and
    # reading raw entry-point names would miss it: the provider would look
    # installed, get selected, and fail later at import time with an error that
    # says nothing about credentials. Worse, the message this module emits
    # ("the module is not installed or could not be imported") would be a claim
    # it never actually verified. `is_provider_module_installed()` resolves the
    # entry point's module, so both the selection and the diagnostic are true.
    eps = entry_points(group="amplifier.modules")
    installed_providers = {
        ep.name
        for ep in eps
        if ep.name.startswith("provider-") and is_provider_module_installed(ep.name)
    }

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
        #
        # Exception: providers whose credentials are ambient (see
        # AMBIENT_CREDENTIAL_PROVIDERS). A platform-injected token is not a
        # user decision, so a missing module for one of them overrides
        # nothing and must not be escalated. Skip it the way a provider with
        # no credentials at all is skipped -- silently, leaving the Ollama
        # fallback reachable.
        if provider_id not in installed_providers:
            if provider_id not in AMBIENT_CREDENTIAL_PROVIDERS:
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
