"""CLI policy for Foundation's cooperative session ownership mechanism."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from contextlib import nullcontext

import click
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text


def _app_label(value):
    from .shared_root_state import _bounded_value

    label = _bounded_value(value)
    return {
        "amplifier-cli": "Amplifier CLI",
        "amplifier-unified": "Amplifier Unified",
    }.get(label, label or "another app")


def _notice(console, title, content, style="cyan"):
    banner = Text()
    banner.append(title, style=f"bold {style}")
    banner.append("\n")
    banner.append_text(content)
    console.print()
    console.print(Panel.fit(banner, border_style="cyan"))
    console.print()


def _show_owner(console, owner):
    from .shared_root_state import _bounded_value

    owner = owner or {}
    content = Text("App: ", style="dim")
    content.append(_app_label(owner.get("app")), style="dim bright_yellow")
    host = _bounded_value(owner.get("hostname"))
    if host:
        content.append(f" | Host: {host}", style="dim")
    content.append("\nRequest takeover to save and close it there,\n", style="not dim")
    content.append("then continue here in the CLI.", style="not dim")
    _notice(console, "Session already open", content)


def _show_takeover_failure(console, status, same_owner):
    message = {
        "unsupported": "That app does not support takeover requests. Close the session there, then try again.",
        "timed_out": "The other app has not finished releasing the session. It may still finish; try again shortly.",
        "cannot_release": "The other app could not finish saving and closing this session. Check that app, then try again.",
    }.get(status, "The session is still in use. Check the other app, then try again.")
    if not same_owner:
        message = "The session owner changed while waiting. Try again to request takeover from the current app."
    _notice(console, "Could not take over", Text(message), "yellow")


def _takeover_option(ctx, param, value):
    if not ctx.resilient_parsing:
        ctx.meta[param.name] = value


def takeover_options(command):
    command = click.option(
        "--takeover",
        is_flag=True,
        expose_value=False,
        callback=_takeover_option,
        help="Request that the current session owner save and release it.",
    )(command)
    return click.option(
        "--handoff-timeout",
        type=click.FloatRange(min=0, max=300, min_open=True),
        default=30,
        expose_value=False,
        callback=_takeover_option,
        help="Seconds to wait for cooperative takeover (default: 30).",
    )(command)


async def acquire_root(config, console):
    """Only deliberate CLI intent requests release; acquire still grants ownership."""
    from .shared_root_state import SharedRootSession, SharedRootSessionBusyError

    try:
        return SharedRootSession.acquire(config.session_id)
    except SharedRootSessionBusyError as busy:
        context = click.get_current_context(silent=True)
        options = context.meta if context else {}
        requested = config.takeover or options.get("takeover", False)
        timeout = options.get("handoff_timeout", config.handoff_timeout)
        interactive = config.invocation_mode == "chat" and sys.stdin.isatty()
        if not requested and interactive:
            _show_owner(console, busy.owner)
            requested = await asyncio.to_thread(
                Confirm.ask,
                "[bold cyan]Request takeover?[/bold cyan]",
                console=console,
                default=False,
            )
            if not requested:
                console.print("[dim]Session left open in the other app.[/dim]")
                busy.displayed = True
        if not requested or busy.store is None:
            raise
        from amplifier_foundation.session import request_release

        deadline = time.monotonic() + timeout
        status = (
            console.status("[cyan]Requesting takeover…[/cyan]", spinner="dots")
            if interactive
            else nullcontext()
        )
        with status as progress:

            def on_progress(stage):
                message = {
                    "accepted": "Takeover accepted. Waiting for the other app…",
                    "draining": "Waiting for active work to stop safely…",
                    "persisting": "Saving session history…",
                }.get(stage, "Waiting for the other app to release the session…")
                progress.update(Text(message, style="cyan"))

            result = await request_release(
                busy.store,
                expected_owner=busy.owner,
                request_id=uuid.uuid4().hex,
                requester_app="Amplifier CLI",
                timeout=timeout,
                on_progress=on_progress if interactive else None,
            )
            # The reply may be lost after release. Try the real lock, but never
            # send a second request to a newly observed owner.
            while True:
                try:
                    root = SharedRootSession.acquire(config.session_id)
                    break
                except SharedRootSessionBusyError as current:
                    same = (current.owner or {}).get("acquisition_id") == (
                        busy.owner or {}
                    ).get("acquisition_id")
                    if (
                        not same
                        or result.status not in {"released", "unreachable"}
                        or time.monotonic() >= deadline
                    ):
                        current.args = (
                            f"Takeover did not complete ({result.status}). {result.message} {current}",
                        )
                        if interactive:
                            progress.stop()
                            _show_takeover_failure(console, result.status, same)
                            current.displayed = True
                        raise current from None
                    await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if interactive:
            console.print(
                "[green]✓[/green] [bold]Session acquired.[/bold] [dim]Continuing here in the CLI.[/dim]"
            )
        return root


class CLIHandoff:
    """Wake idle input, request graceful cancellation, then let normal cleanup run."""

    def __init__(self, initialized, console):
        self.initialized, self.console = initialized, console
        self.root = vars(initialized).get("root_state")
        self.requested = asyncio.Event()
        self.prepared = asyncio.get_running_loop().create_future()
        self.registration = None
        self.source = None
        self.release_request = None
        self.steering = None

    async def start(self):
        if self.root is not None:
            from amplifier_foundation.session import register_release_handler

            self.registration = await register_release_handler(
                self.root.held, prepare_release=self.prepare
            )
        return self

    async def prepare(self, request):
        self.source = request.requester_app
        self.release_request = request
        self.requested.set()
        if self.steering is not None:
            self.steering.set()
        self.initialized.session.coordinator.cancellation.request_graceful()
        request.report_progress("draining")
        return await asyncio.shield(self.prepared)

    async def prompt(self, factory):
        if self.requested.is_set():
            raise EOFError
        prompt = asyncio.create_task(factory())
        release = asyncio.create_task(self.requested.wait())
        try:
            done, _ = await asyncio.wait(
                {prompt, release}, return_when=asyncio.FIRST_COMPLETED
            )
            if release in done:
                raise EOFError
            return await prompt
        finally:
            for task in (prompt, release):
                if not task.done():
                    task.cancel()
            await asyncio.gather(prompt, release, return_exceptions=True)

    async def finish(self, save=None, after_cleanup=None):
        """Complete all persistence before either normal release or handoff."""
        from amplifier_foundation.session import CannotRelease, ReadyToRelease

        if not self.requested.is_set() and self.registration:
            await self.registration.close()
        try:
            if self.release_request is not None:
                self.release_request.report_progress("persisting")
            if save and self.requested.is_set():
                await save()
            if self.root is None:
                await self.initialized.cleanup()
            else:
                await self.initialized.cleanup(release_ownership=False)
            if after_cleanup:
                await after_cleanup()
        except BaseException:
            if self.requested.is_set() and not self.prepared.done():
                self.prepared.set_result(
                    CannotRelease(
                        "cleanup_failed", "CLI could not finish saving and cleanup."
                    )
                )
                await asyncio.shield(self.registration.pending)
            raise
        if self.requested.is_set():
            self.prepared.set_result(ReadyToRelease())
            await asyncio.shield(self.registration.pending)
            if not self.root.held.active:
                content = Text("Closed at the request of: ", style="dim")
                content.append(_app_label(self.source), style="dim bright_yellow")
                content.append(
                    "\nSession history saved. Execution ownership released.",
                    style="not dim",
                )
                _notice(self.console, "Session handed off", content, "green")
        elif self.root is not None:
            # Closing first prevents a new callback from entering after cleanup.
            if self.registration:
                await self.registration.close()
            self.root.release()
        if self.registration:
            await self.registration.close()
