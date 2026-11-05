"""CLI command exports for amplifier-app-cli."""

import sys
from types import ModuleType as _ModuleType

__all__ = [
    "auto_init_from_env",
    "check_first_run",
    "init_cmd",
    "module",
    "prompt_first_run_init",
    "provider",
    "register_run_command",
    "register_session_commands",
    "source",
    "tool",
]
_exports_loaded = False


def _load_exports():
    global _exports_loaded
    global auto_init_from_env, check_first_run, init_cmd, prompt_first_run_init
    global module, provider, register_run_command, register_session_commands, source, tool
    if _exports_loaded:
        return
    from .. import _ensure_tls_shim

    _ensure_tls_shim()
    # Bind the complete fixed public surface together: importing a submodule
    # otherwise leaves same-named attributes such as provider as module objects.
    from .init import auto_init_from_env
    from .init import check_first_run
    from .init import init_cmd
    from .init import prompt_first_run_init
    from .module import module
    from .provider import provider
    from .run import register_run_command
    from .session import register_session_commands
    from .source import source
    from .tool import tool

    _exports_loaded = True


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    _load_exports()
    return globals()[name]


if not (len(sys.argv) > 1 and sys.argv[1] == "doctor"):
    _load_exports()


class _PackageModule(_ModuleType):
    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        # A directly imported child can mask its same-named public command;
        # __getattr__ alone cannot see an attribute that Python already set.
        if (
            name in ("module", "provider", "source", "tool")
            and isinstance(value, _ModuleType)
            and value is sys.modules.get(f"{__name__}.{name}")
        ):
            _load_exports()
            return getattr(value, name)
        return value


sys.modules[__name__].__class__ = _PackageModule
