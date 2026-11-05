"""Amplifier CLI - Command-line interface for the Amplifier platform."""

import sys

# Only the exact public doctor route bypasses normal application startup.
# Python may already have cached this first __init__; suppress later bytecode.
if len(sys.argv) > 1 and sys.argv[1] == "doctor":
    sys.dont_write_bytecode = True

import importlib
from types import ModuleType as _ModuleType

__version__ = "0.1.0"
__all__ = ["cli", "main", "__version__"]

# TEMPORARY, remove when truststore ships the fix. This must run before any
# TLS happens in this process, and nothing in the amplifier tree owns the
# truststore call site (httpx2/httpcore2 construct the context), so CLI
# package import is the earliest amplifier-owned point available. It is a
# no-op unless the installed truststore's wrap_bio is genuinely unlocked.
# See truststore_shim.py for the full story.
def _ensure_tls_shim():
    from . import truststore_shim as _truststore_shim

    _truststore_shim.apply()


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(".main", __name__)
    if name == "cli":
        module._initialize_cli()
        globals()["cli"] = module.cli
    # Importing the submodule sets package.main to that module. Restore the
    # public dispatcher, including when the submodule was imported first.
    globals()["main"] = module.main
    return globals()[name]


if not (len(sys.argv) > 1 and sys.argv[1] == "doctor"):
    _ensure_tls_shim()
    __getattr__("cli")


class _PackageModule(_ModuleType):
    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        # Python publishes imported children on their parent, bypassing
        # __getattr__. Resolve only the collision with our public dispatcher.
        if (
            name == "main"
            and isinstance(value, _ModuleType)
            and value is sys.modules.get(f"{__name__}.main")
        ):
            return __getattr__("main")
        return value


sys.modules[__name__].__class__ = _PackageModule
