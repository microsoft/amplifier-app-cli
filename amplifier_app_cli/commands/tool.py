"""Tool management commands for the Amplifier CLI.

Generic mechanism to list, inspect, and invoke any mounted tool.
This provides CLI access to tools from any bundle without the CLI
needing to know about specific tools or bundles.

Philosophy: Mechanism, not policy. CLI provides capability to invoke tools;
which tools exist is determined by the active bundle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from ..console import console
from ..paths import create_config_manager
from ..ui.item_renderer import ItemRenderer
from ..ui.view_policy import resolve_view
from ..ui.view_policy import view_flags
from ..utils.error_format import escape_markup
from ..runtime.config import inject_user_providers

logger = logging.getLogger(__name__)


# ============================================================================
# Post-invocation cleanup: bounded, and never ahead of the result
# ============================================================================
#
# `session.cleanup()` used to run in a `finally:` between `return result` and
# the caller that prints it, unbounded. A module whose cleanup blocks therefore
# did not merely delay teardown -- it destroyed the run's outcome: the work
# succeeded, the outputs were on disk, and no caller could ever learn it
# (measured: a completed recipe run followed by 28 minutes of silence, exiting
# instantly on SIGTERM).
#
# Two independent remedies, both applied here:
#   1. EMIT FIRST. The result (and, in json mode, the result JSON) is written
#      and flushed *before* cleanup is entered, so a wedged teardown costs a
#      delayed exit rather than the answer.
#   2. BOUND THE CLEANUP. See `_cleanup_session_bounded` below.

CLEANUP_TIMEOUT_ENV = "AMPLIFIER_TOOL_CLEANUP_TIMEOUT"
DEFAULT_CLEANUP_TIMEOUT_SECONDS = 30.0
CLEANUP_CANCEL_GRACE_SECONDS = 5.0

CLEANUP_COMPLETED = "completed"
CLEANUP_ABANDONED = "abandoned"
CLEANUP_UNCANCELLABLE = "uncancellable"


def _resolve_cleanup_timeout(app_settings: Any | None = None) -> float:
    """Resolve the wall-clock bound on post-invocation session cleanup.

    Precedence: ``AMPLIFIER_TOOL_CLEANUP_TIMEOUT`` env var, then
    ``tool.cleanup_timeout_seconds`` in settings.yaml, then
    ``DEFAULT_CLEANUP_TIMEOUT_SECONDS``.

    A non-numeric or non-positive value is refused with a WARNING and the
    default is used -- "0 means wait forever" is exactly the behaviour this
    bound exists to remove, so it is not offered.
    """
    raw: Any = os.environ.get(CLEANUP_TIMEOUT_ENV)
    source = CLEANUP_TIMEOUT_ENV

    if raw is None and app_settings is not None:
        try:
            tool_cfg = (app_settings.get_merged_settings() or {}).get("tool") or {}
            if isinstance(tool_cfg, dict):
                raw = tool_cfg.get("cleanup_timeout_seconds")
                source = "settings tool.cleanup_timeout_seconds"
        except Exception:  # malformed settings must not break an invocation
            raw = None

    if raw is None:
        return DEFAULT_CLEANUP_TIMEOUT_SECONDS

    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring non-numeric %s=%r; bounding session cleanup at %gs.",
            source,
            raw,
            DEFAULT_CLEANUP_TIMEOUT_SECONDS,
        )
        return DEFAULT_CLEANUP_TIMEOUT_SECONDS

    if value <= 0:
        logger.warning(
            "Ignoring non-positive %s=%r; bounding session cleanup at %gs.",
            source,
            raw,
            DEFAULT_CLEANUP_TIMEOUT_SECONDS,
        )
        return DEFAULT_CLEANUP_TIMEOUT_SECONDS

    return value


def _flush_streams() -> None:
    """Flush stdout/stderr. Called before entering cleanup and before a hard exit.

    ``print()`` to a pipe is block-buffered, so an emitted result that is never
    flushed is, to the caller reading that pipe, no result at all.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass


def _print_result(tool_name: str, result: Any, output: str) -> None:
    """Emit a successful invocation's result, then flush.

    Called before session cleanup is entered, so the flush is load-bearing:
    an unflushed write into a pipe is not an answer the caller can read.
    """
    if output == "json":
        print(
            json.dumps(
                {"status": "success", "tool": tool_name, "result": result},
                indent=2,
                default=str,
            )
        )
    else:
        console.print(f"[bold green]Result from {tool_name}:[/bold green]")
        if isinstance(result, dict):
            for key, value in result.items():
                console.print(f"  {key}: {value}")
        elif isinstance(result, list):
            for item in result:
                console.print(f"  - {item}")
        else:
            console.print(f"  {result}")
    _flush_streams()


def _print_error(tool_name: str, error: BaseException, output: str) -> None:
    """Emit a failed invocation's error, then flush. See `_print_result`."""
    if output == "json":
        print(
            json.dumps(
                {"status": "error", "error": str(error), "tool": tool_name}, indent=2
            )
        )
    else:
        console.print(f"[red]Error:[/red] {escape_markup(error)}")
    _flush_streams()


async def _cleanup_session_bounded(
    session: Any,
    *,
    timeout: float,
    grace: float = CLEANUP_CANCEL_GRACE_SECONDS,
    exit_code: int = 0,
    hard_exit: Callable[[int], Any] = os._exit,
) -> str:
    """Run ``session.cleanup()`` under a wall-clock bound.

    Ladder, in order:

    1. ``asyncio.wait({task}, timeout=...)`` -- deliberately NOT
       ``asyncio.wait_for``. ``wait_for`` cancels the inner task and then
       *waits for that cancellation to complete*, so a cleanup that ignores
       cancellation reintroduces the very unbounded hang this bound exists
       to remove. ``wait`` returns on the deadline regardless.
    2. On timeout: WARNING naming that cleanup was abandoned, then
       ``task.cancel()`` and a short grace window. A cleanup that honours
       cancellation finishes here and the process exits normally.
    3. Still not finished: the task would wedge ``asyncio.run()``'s own
       shutdown (which cancel-and-gathers every remaining task), so there is
       no non-blocking way back to the caller. WARNING, flush, ``os._exit``.
       This is the documented last resort, and it says so in the log.

    Returns one of ``CLEANUP_COMPLETED`` / ``CLEANUP_ABANDONED`` /
    ``CLEANUP_UNCANCELLABLE``. Never raises: by the time this runs the
    outcome has already been emitted, so a teardown failure is reported,
    not promoted into the run's result.
    """
    try:
        task = asyncio.ensure_future(session.cleanup())
    except Exception as exc:  # cleanup() raised synchronously
        logger.warning("Session cleanup could not be started: %s", exc)
        return CLEANUP_COMPLETED

    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    "Session cleanup raised %s: %s", type(exc).__name__, exc
                )
        return CLEANUP_COMPLETED

    logger.warning(
        "Session cleanup exceeded %gs and was ABANDONED -- the tool's result "
        "was already emitted, so the run's outcome is intact. Raise the bound "
        "with %s if a slow teardown is expected.",
        timeout,
        CLEANUP_TIMEOUT_ENV,
    )

    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=grace)
    if task in done:
        return CLEANUP_ABANDONED

    logger.warning(
        "Abandoned session cleanup also ignored cancellation for %gs; exiting "
        "hard via os._exit(%d) because a wedged teardown would otherwise hold "
        "asyncio.run() shutdown open indefinitely.",
        grace,
        exit_code,
    )
    _flush_streams()
    hard_exit(exit_code)
    return CLEANUP_UNCANCELLABLE


# ============================================================================
# First-run provider guard (mirrors run.py:142-153)
# ============================================================================


def _ensure_provider_configured() -> None:
    """Check if first run init is needed and handle it.

    This runs unconditionally - --provider just selects from configured providers,
    it doesn't bypass the need for configuration.
    """
    from .init import check_first_run

    if check_first_run():
        if sys.stdin.isatty():
            from .init import prompt_first_run_init

            prompt_first_run_init(console)
        else:
            # Non-interactive context (CI, Docker, shadow env)
            # Auto-init from environment variables
            from .init import auto_init_from_env

            auto_init_from_env(console)


# ============================================================================
# Bundle Detection (mirrors run.py pattern)
# ============================================================================


def _get_active_bundle_name() -> str | None:
    """Get the active bundle name from settings (if any).

    Checks for bundle configured via 'amplifier bundle use'.
    Returns None if no bundle is explicitly configured.
    """
    config_manager = create_config_manager()
    bundle_settings = config_manager.get_merged_settings().get("bundle", {})
    if isinstance(bundle_settings, dict):
        return bundle_settings.get("active")
    return None


def _should_use_bundle() -> tuple[bool, str | None, str | None]:
    """Determine which bundle to use.

    Returns:
        Tuple of (use_bundle: bool, bundle_name: str | None, _unused)

    Logic (mirrors run.py):
    1. If active bundle is set → use bundle
    2. Always use bundle system
    3. Default to 'anchors' bundle
    """
    # Check for active bundle
    bundle_name = _get_active_bundle_name()
    if bundle_name:
        return (True, bundle_name, None)

    # Default to anchors bundle
    return (True, "anchors", None)


# ============================================================================
# Bundle-based Tool Loading (primary path)
# ============================================================================


async def _get_mounted_tools_from_bundle_async(
    bundle_name: str,
) -> list[dict[str, Any]]:
    """Get actual mounted tool names from a bundle.

    Uses PreparedBundle to create a session and extract mounted tools.

    Args:
        bundle_name: Name of bundle to load

    Returns:
        List of tool dicts with name, description, and callable status
    """
    from ..lib.settings import AppSettings
    from ..runtime.config import resolve_config_async

    # Load bundle via unified resolve_config_async (single source of truth)

    app_settings = AppSettings()

    try:
        _config, prepared_bundle = await resolve_config_async(
            bundle_name=bundle_name,
            app_settings=app_settings,
            console=console,
        )
    except Exception as e:
        raise ValueError(f"Failed to load bundle '{bundle_name}': {e}") from e

    if prepared_bundle is None:
        raise ValueError(f"Bundle '{bundle_name}' did not produce a PreparedBundle")

    inject_user_providers(_config, prepared_bundle)

    # Create session from prepared bundle
    session = await prepared_bundle.create_session(session_cwd=Path.cwd())
    await session.initialize()

    try:
        # Get mounted tools
        tools = session.coordinator.get("tools")
        if not tools:
            return []

        result = []
        for tool_name, tool_instance in tools.items():
            # Get description from tool if available
            description = "No description"
            if hasattr(tool_instance, "description"):
                description = tool_instance.description
            elif hasattr(tool_instance, "__doc__") and tool_instance.__doc__:
                description = tool_instance.__doc__.strip().split("\n")[0]

            result.append(
                {
                    "name": tool_name,
                    "description": description,
                    "has_execute": hasattr(tool_instance, "execute"),
                }
            )

        return sorted(result, key=lambda t: t["name"])

    finally:
        await _cleanup_session_bounded(
            session, timeout=_resolve_cleanup_timeout(app_settings)
        )


async def _invoke_tool_from_bundle_async(
    bundle_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    emit: Callable[[bool, Any], None] | None = None,
    hard_exit: Callable[[int], Any] = os._exit,
) -> Any:
    """Invoke a tool within a bundle session context.

    Args:
        bundle_name: Bundle determining which tools are available
        tool_name: Name of tool to invoke
        tool_args: Arguments to pass to the tool
        emit: Optional ``(ok, payload)`` callback invoked with the outcome
            BEFORE session cleanup is entered. ``payload`` is the tool result
            when ``ok`` is True and the raised exception when it is False.
            This is the fix for the swallowed-result defect: the caller cannot
            print what it has not been given, and ``return`` does not reach the
            caller until the ``finally`` block completes.
        hard_exit: Injection seam for the last-resort exit (tests only).

    Returns:
        Tool execution result

    Raises:
        ValueError: If tool not found
        Exception: If tool execution fails
    """
    from ..lib.settings import AppSettings
    from ..lib.bundle_loader import AppModuleResolver
    from ..paths import create_foundation_resolver
    from ..session_runner import register_session_spawning
    from ..runtime.config import resolve_config_async

    # Load bundle via unified resolve_config_async (single source of truth)

    app_settings = AppSettings()

    _config, prepared_bundle = await resolve_config_async(
        bundle_name=bundle_name,
        app_settings=app_settings,
        console=console,
    )

    if prepared_bundle is None:
        raise ValueError(f"Bundle '{bundle_name}' did not produce a PreparedBundle")

    # CRITICAL: Wrap bundle resolver with app-layer fallback (mirrors session_runner.py)
    # This enables fallback to installed providers when they're not in the bundle.
    # Without this wrapper, provider modules fail to load even after `amplifier provider install`.
    fallback_resolver = create_foundation_resolver()
    prepared_bundle.resolver = AppModuleResolver(  # type: ignore[assignment]
        bundle_resolver=prepared_bundle.resolver,
        settings_resolver=fallback_resolver,
    )

    inject_user_providers(_config, prepared_bundle)

    # Create session from prepared bundle
    session = await prepared_bundle.create_session(session_cwd=Path.cwd())
    await session.initialize()

    # Register session spawning (enables tools like recipes to spawn sub-sessions)
    register_session_spawning(session)

    failed = False
    try:
        # Get mounted tools
        tools = session.coordinator.get("tools")
        if not tools:
            raise ValueError("No tools mounted in session")

        # Find the tool
        if tool_name not in tools:
            available = ", ".join(tools.keys())
            raise ValueError(f"Tool '{tool_name}' not found. Available: {available}")

        tool_instance = tools[tool_name]

        # Invoke the tool
        if hasattr(tool_instance, "execute"):
            result = await tool_instance.execute(tool_args)  # type: ignore[union-attr]
        else:
            raise ValueError(f"Tool '{tool_name}' does not have execute method")

    except BaseException as exc:
        # Emit the failure here, for the same reason the success path emits
        # here: the caller's `except` clause does not run until this
        # function's `finally` has completed.
        failed = True
        if emit is not None:
            emit(False, exc)
        raise

    else:
        # THE RUN IS DONE AND ITS OUTCOME IS KNOWN. Hand it to the caller now,
        # while nothing can delay it -- cleanup is entered below.
        if emit is not None:
            emit(True, result)
        return result

    finally:
        await _cleanup_session_bounded(
            session,
            timeout=_resolve_cleanup_timeout(app_settings),
            exit_code=1 if failed else 0,
            hard_exit=hard_exit,
        )


@click.group(invoke_without_command=True)
@click.pass_context
def tool(ctx: click.Context):
    """Invoke tools from a bundle.

    Generic mechanism to list, inspect, and invoke any mounted tool.
    Tools are determined by the active bundle's mount plan.

    Examples:
        amplifier tool list                    List available tools
        amplifier tool info filesystem_read    Show tool schema
        amplifier tool invoke filesystem_read path=/tmp/test.txt
    """
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@tool.command(name="list")
@click.option("--bundle", "-b", help="Bundle to use (default: active bundle)")
@click.option(
    "--modules", "-m", is_flag=True, help="Show module names instead of mounted tools"
)
@view_flags
def tool_list(
    bundle: str | None, modules: bool, compact: bool, detailed: bool, fmt: str
):
    """List available tools from the active bundle.

    By default, shows the actual tool names that can be invoked (e.g., read_file,
    write_file). Use --modules to see tool module names instead (e.g., tool-filesystem).
    """
    _ensure_provider_configured()

    # Determine bundle to use
    use_bundle, default_bundle, _unused = _should_use_bundle()

    # Explicit flags override auto-detection
    if bundle:
        use_bundle = True
        default_bundle = bundle

    if use_bundle:
        bundle_name = default_bundle or "anchors"

        if modules:
            console.print(
                "[yellow]--modules flag not supported with bundles. Showing mounted tools.[/yellow]"
            )

        console.print(f"[dim]Mounting tools from bundle '{bundle_name}'...[/dim]")

        try:
            tools = asyncio.run(_get_mounted_tools_from_bundle_async(bundle_name))
        except Exception as e:
            console.print(f"[red]Error mounting tools:[/red] {escape_markup(e)}")
            sys.exit(1)

        if not tools:
            console.print(
                f"[yellow]No tools mounted from bundle '{bundle_name}'[/yellow]"
            )
            return

        view = resolve_view(
            ("tool", "list"), compact_flag=compact, detailed_flag=detailed
        )
        renderer = ItemRenderer(console)

        items: list[dict[str, Any]] = [
            {
                "name": t["name"],
                "enabled": True,
                "behaviors": [bundle_name],
                "config_summary": {"description": t["description"]},
            }
            for t in tools
        ]

        if fmt == "json":
            renderer.render_json(items)
            return

        renderer.render(
            items,
            view=view,
            category="tool",
            section_title=f"tools from '{bundle_name}'",
        )
        console.print(
            "[dim]Use 'amplifier tool invoke <name> key=value ...' to invoke a tool[/dim]"
        )


@tool.command(name="info")
@click.argument("tool_name")
@click.option("--bundle", "-b", help="Bundle to use (default: active bundle)")
@click.option(
    "--module",
    "-m",
    is_flag=True,
    help="Look up by module name instead of mounted tool name",
)
@view_flags
def tool_info(
    tool_name: str,
    bundle: str | None,
    module: bool,
    compact: bool,
    detailed: bool,
    fmt: str,
):
    """Show detailed information about a tool.

    By default, looks up the actual mounted tool by name (e.g., read_file).
    Use --module to look up by module name instead (e.g., tool-filesystem).
    """
    _ensure_provider_configured()

    # Determine bundle to use
    use_bundle, default_bundle, _unused = _should_use_bundle()

    # Explicit flags override auto-detection
    if bundle:
        use_bundle = True
        default_bundle = bundle

    if use_bundle:
        # Bundle path (primary)
        bundle_name = default_bundle or "anchors"

        if module:
            # For bundles, --module is not supported
            console.print(
                "[yellow]--module flag not supported with bundles. Looking up mounted tool.[/yellow]"
            )

        # Look up actual mounted tool
        console.print(f"[dim]Mounting tools to get info for '{tool_name}'...[/dim]")

        try:
            tools = asyncio.run(_get_mounted_tools_from_bundle_async(bundle_name))
        except Exception as e:
            console.print(f"[red]Error mounting tools:[/red] {escape_markup(e)}")
            sys.exit(1)

        found_tool = next((t for t in tools if t["name"] == tool_name), None)

        if not found_tool:
            console.print(
                f"[red]Error:[/red] Tool '{tool_name}' not found in bundle '{bundle_name}'"
            )
            console.print("\nAvailable tools:")
            for t in tools:
                console.print(f"  - {t['name']}")
            sys.exit(1)

        view = resolve_view(
            ("tool", "info"), compact_flag=compact, detailed_flag=detailed
        )
        renderer = ItemRenderer(console)

        item = {
            "name": found_tool["name"],
            "enabled": True,
            "behaviors": [bundle_name],
            "config_summary": {
                "description": found_tool.get("description", "No description"),
                "invokable": "yes" if found_tool.get("has_execute") else "no",
            },
        }

        if fmt == "json":
            renderer.render_json(item)
            return

        renderer.render_one(item, view=view)
        console.print(
            "[dim]Usage: amplifier tool invoke " + tool_name + " key=value ...[/dim]"
        )


@tool.command(name="invoke")
@click.argument("tool_name")
@click.argument("args", nargs=-1)
@click.option("--bundle", "-b", help="Bundle to use (default: auto-detect)")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
def tool_invoke(tool_name: str, args: tuple[str, ...], bundle: str | None, output: str):
    """Invoke a tool directly with provided arguments.

    Arguments are provided as key=value pairs:

        amplifier tool invoke filesystem_read path=/tmp/test.txt

    For complex values, use JSON:

        amplifier tool invoke some_tool data='{"key": "value"}'
    """
    _ensure_provider_configured()

    # Parse key=value arguments first (before session creation)
    tool_args: dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            console.print(f"[red]Error:[/red] Invalid argument format: '{arg}'")
            console.print("Arguments must be in key=value format")
            sys.exit(1)

        key, value = arg.split("=", 1)

        # Try to parse as JSON for complex values
        try:
            tool_args[key] = json.loads(value)
        except json.JSONDecodeError:
            # Use as plain string
            tool_args[key] = value

    # Determine bundle
    if bundle:
        bundle_name = bundle
    else:
        _, bundle_name, _ = _should_use_bundle()

    # Run the invocation.
    #
    # The outcome is emitted from INSIDE the async call, before session
    # cleanup runs -- see `_invoke_tool_from_bundle_async`. `emitted` guards
    # against printing it twice, and preserves the old print-here behaviour
    # for any path that somehow returns without emitting.
    emitted = False

    def _emit(ok: bool, payload: Any) -> None:
        nonlocal emitted
        if emitted:
            return
        emitted = True
        if ok:
            _print_result(tool_name, payload, output)
        else:
            _print_error(tool_name, payload, output)

    try:
        result = asyncio.run(
            _invoke_tool_from_bundle_async(
                bundle_name, tool_name, tool_args, emit=_emit
            )
        )  # type: ignore[arg-type]
    except Exception as e:
        if not emitted:
            _print_error(tool_name, e, output)
        sys.exit(1)

    if not emitted:
        _print_result(tool_name, result, output)


__all__ = ["tool"]
