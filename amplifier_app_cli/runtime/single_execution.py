"""Single-shot session execution.

The CLI entrypoint injects application-owned rendering and persistence services so
this runtime stays independent from ``main`` while preserving its test seams.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_core import ModuleValidationError  # pyright: ignore[reportAttributeAccessIssue]
from amplifier_core.llm_errors import LLMError

from amplifier_app_cli.runtime.cleanup_events import CLEANUP_FINALLY_BEGIN
from amplifier_app_cli.runtime.cleanup_events import CLEANUP_FINALLY_END
from amplifier_app_cli.runtime.cleanup_events import CLEANUP_RENDER_BEGIN
from amplifier_app_cli.runtime.cleanup_events import CLEANUP_RENDER_END
from amplifier_app_cli.runtime.cleanup_events import CLEANUP_STORE_BEGIN
from amplifier_app_cli.runtime.cleanup_events import CLEANUP_STORE_END
from amplifier_app_cli.runtime.session_events import PROMPT_COMPLETE
from amplifier_app_cli.session_runner import SessionConfig

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle


@dataclass(frozen=True, slots=True)
class SingleExecutionRequest:
    """Inputs for one non-interactive Amplifier turn."""

    prompt: str
    config: dict[str, Any]
    search_paths: list[Path]
    verbose: bool
    session_id: str | None = None
    bundle_name: str = "unknown"
    output_format: str = "text"
    prepared_bundle: PreparedBundle | None = None
    initial_transcript: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class SingleExecutionDependencies:
    """Application services used by single-shot execution.

    Callables intentionally accept dynamic session/coordinator objects. Their
    concrete interfaces are supplied by Amplifier modules at runtime.
    """

    console: Any
    create_initialized_session: Callable[[SessionConfig, Any], Awaitable[Any]]
    process_runtime_mentions: Callable[[Any, str], Awaitable[str]]
    session_store_factory: Callable[[], Any]
    markdown_factory: Callable[[str], Any]
    display_validation_error: Callable[..., bool]
    display_llm_error: Callable[..., bool]
    escape_markup: Callable[[Any], str]
    trace_collector_factory: Callable[[], Any]


def _model_name(session: Any) -> str:
    providers = session.coordinator.get("providers") or {}
    for provider_name, provider in providers.items():
        if hasattr(provider, "model"):
            return f"{provider_name}/{provider.model}"
        if hasattr(provider, "default_model"):
            return f"{provider_name}/{provider.default_model}"
    return "unknown"


def _write_json_error(
    error: BaseException,
    *,
    session_id: str,
    original_stdout: Any,
    error_type: str | None = None,
) -> None:
    if original_stdout is not None:
        sys.stdout = original_stdout
    output: dict[str, Any] = {
        "status": "error",
        "error": str(error),
        "session_id": session_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if error_type is not None:
        output["error_type"] = error_type
    print(json.dumps(output, indent=2, default=str))


async def _persist_session(
    session: Any,
    *,
    request: SingleExecutionRequest,
    dependencies: SingleExecutionDependencies,
    session_id: str,
    model_name: str,
) -> int:
    context = session.coordinator.get("context")
    messages = await context.get_messages() if context else []
    if messages:
        store = dependencies.session_store_factory()
        try:
            existing_metadata = store.get_metadata(session_id) or {}
        except FileNotFoundError:
            existing_metadata = {}
        metadata = {
            **existing_metadata,
            "session_id": session_id,
            "created": existing_metadata.get("created", datetime.now(UTC).isoformat()),
            "bundle": request.bundle_name,
            "model": model_name,
            "turn_count": len(
                [message for message in messages if message.get("role") == "user"]
            ),
            "working_dir": str(Path.cwd().resolve()),
        }
        store.save(session_id, messages, metadata)
        if request.verbose and request.output_format == "text":
            dependencies.console.print(f"[dim]Session {session_id[:8]}... saved[/dim]")
    return len(messages)


async def run_single_execution(
    request: SingleExecutionRequest,
    dependencies: SingleExecutionDependencies,
) -> None:
    """Create a session, execute one prompt, render it, and persist the turn."""
    json_mode = request.output_format in {"json", "json-trace"}
    if json_mode:
        original_stdout = sys.stdout
        original_console_file = dependencies.console.file
        sys.stdout = sys.stderr
        dependencies.console.file = sys.stderr
    else:
        original_stdout = None
        original_console_file = None

    json_output_data: dict[str, Any] | None = None
    trace_collector = (
        dependencies.trace_collector_factory()
        if request.output_format == "json-trace"
        else None
    )
    session_config = SessionConfig(
        config=request.config,
        search_paths=request.search_paths,
        verbose=request.verbose,
        session_id=request.session_id,
        bundle_name=request.bundle_name,
        initial_transcript=request.initial_transcript,
        prepared_bundle=request.prepared_bundle,
        output_format=request.output_format,
    )
    initialized = await dependencies.create_initialized_session(
        session_config, dependencies.console
    )
    session = initialized.session
    actual_session_id = initialized.session_id

    try:
        if trace_collector:
            hooks = session.coordinator.get("hooks")
            if hooks:
                hooks.register(
                    "tool:pre",
                    trace_collector.on_tool_pre,
                    priority=1000,
                    name="trace_collector_pre",
                )
                hooks.register(
                    "tool:post",
                    trace_collector.on_tool_post,
                    priority=1000,
                    name="trace_collector_post",
                )

        prompt = await dependencies.process_runtime_mentions(session, request.prompt)
        if request.verbose:
            dependencies.console.print(f"[dim]Executing: {prompt}[/dim]")

        response = await session.execute(prompt)
        actual_session_id = session.session_id
        model_name = _model_name(session)
        hooks = session.coordinator.get("hooks")
        if hooks:
            await hooks.emit(
                PROMPT_COMPLETE,
                {
                    "prompt": prompt,
                    "response": response,
                    "session_id": actual_session_id,
                },
            )
            await hooks.emit(CLEANUP_RENDER_BEGIN, {"session_id": actual_session_id})

        if json_mode:
            json_output_data = {
                "status": "success",
                "response": response,
                "session_id": actual_session_id,
                "bundle": request.bundle_name,
                "model": model_name,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if trace_collector:
                json_output_data["execution_trace"] = trace_collector.get_trace()
                json_output_data["metadata"] = trace_collector.get_metadata()
        else:
            if request.verbose:
                dependencies.console.print(
                    f"[dim]Response type: {type(response)}, "
                    f"length: {len(response) if response else 0}[/dim]"
                )
            dependencies.console.print(dependencies.markdown_factory(response))
            dependencies.console.print()

        if hooks:
            await hooks.emit(CLEANUP_RENDER_END, {"session_id": actual_session_id})
            await hooks.emit(CLEANUP_STORE_BEGIN, {"session_id": actual_session_id})

        message_count = await _persist_session(
            session,
            request=request,
            dependencies=dependencies,
            session_id=actual_session_id,
            model_name=model_name,
        )
        if hooks:
            await hooks.emit(
                CLEANUP_STORE_END,
                {"session_id": actual_session_id, "message_count": message_count},
            )

    except ModuleValidationError as error:
        if json_mode:
            _write_json_error(
                error,
                session_id=session.session_id,
                original_stdout=original_stdout,
                error_type="ModuleValidationError",
            )
        else:
            if not dependencies.display_validation_error(
                dependencies.console, error, verbose=request.verbose
            ):
                dependencies.console.print(
                    f"[red]Error:[/red] {dependencies.escape_markup(error)}"
                )
                if request.verbose:
                    dependencies.console.print_exception()
        sys.exit(1)

    except LLMError as error:
        if json_mode:
            _write_json_error(
                error,
                session_id=session.session_id,
                original_stdout=original_stdout,
                error_type=type(error).__name__,
            )
        else:
            dependencies.display_llm_error(
                dependencies.console, error, verbose=request.verbose
            )
        sys.exit(1)

    except Exception as error:
        if json_mode:
            _write_json_error(
                error,
                session_id=session.session_id,
                original_stdout=original_stdout,
            )
        else:
            if not dependencies.display_validation_error(
                dependencies.console, error, verbose=request.verbose
            ):
                dependencies.console.print(
                    f"[red]Error:[/red] {dependencies.escape_markup(error)}"
                )
                if request.verbose:
                    dependencies.console.print_exception()
        sys.exit(1)

    finally:
        hooks = session.coordinator.get("hooks")
        if hooks:
            await hooks.emit(CLEANUP_FINALLY_BEGIN, {"session_id": actual_session_id})
        await initialized.cleanup()
        if hooks:
            await hooks.emit(CLEANUP_FINALLY_END, {"session_id": actual_session_id})
        if json_mode:
            await asyncio.sleep(0.1)
        sys.stderr.flush()
        if json_output_data is not None and original_stdout is not None:
            sys.stdout = original_stdout
            print(json.dumps(json_output_data, indent=2, default=str))
            sys.stdout.flush()
        elif original_stdout is not None:
            sys.stdout = original_stdout
        if original_console_file is not None:
            dependencies.console.file = original_console_file


__all__ = [
    "SingleExecutionDependencies",
    "SingleExecutionRequest",
    "run_single_execution",
]
