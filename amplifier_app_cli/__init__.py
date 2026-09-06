"""Amplifier CLI - Command-line interface for the Amplifier platform."""

__version__ = "0.1.0"

# TEMPORARY, remove when truststore ships the fix. This must run before any
# TLS happens in this process, and nothing in the amplifier tree owns the
# truststore call site (httpx2/httpcore2 construct the context), so CLI
# package import is the earliest amplifier-owned point available. It is a
# no-op unless the installed truststore's wrap_bio is genuinely unlocked.
# See truststore_shim.py for the full story.
from . import truststore_shim as _truststore_shim

_truststore_shim.apply()

from .main import cli
from .main import main

__all__ = ["cli", "main", "__version__"]
