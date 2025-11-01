"""Canonical sources for provider modules."""

DEFAULT_PROVIDER_SOURCES = {
    "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "provider-azure-openai": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
}

__all__ = ["DEFAULT_PROVIDER_SOURCES"]
