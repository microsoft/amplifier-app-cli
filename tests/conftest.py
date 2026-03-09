"""Pytest configuration and shared fixtures.

Stubs out amplifier_core.llm_errors, which is not present in the pinned
amplifier-core==1.0.0 dev dependency.  The module was added to the main
codebase after the venv snapshot was taken; without the stub every test
that imports any amplifier_app_cli submodule fails at collection time.
"""

import sys
import types


def _install_llm_errors_stub() -> None:
    """Insert a minimal stub for amplifier_core.llm_errors into sys.modules."""
    if "amplifier_core.llm_errors" in sys.modules:
        return  # already present (real package or previous stub)

    stub = types.ModuleType("amplifier_core.llm_errors")

    class LLMError(Exception):
        pass

    class AuthenticationError(LLMError):
        pass

    class RateLimitError(LLMError):
        pass

    class ContextLengthError(LLMError):
        pass

    class ContentFilterError(LLMError):
        pass

    stub.LLMError = LLMError  # type: ignore[attr-defined]
    stub.AuthenticationError = AuthenticationError  # type: ignore[attr-defined]
    stub.RateLimitError = RateLimitError  # type: ignore[attr-defined]
    stub.ContextLengthError = ContextLengthError  # type: ignore[attr-defined]
    stub.ContentFilterError = ContentFilterError  # type: ignore[attr-defined]

    sys.modules["amplifier_core.llm_errors"] = stub


_install_llm_errors_stub()
