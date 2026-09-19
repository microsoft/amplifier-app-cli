"""CLI policy for Foundation's cooperative session ownership mechanism."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid

import click


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
        if not requested and config.invocation_mode == "chat" and sys.stdin.isatty():
            console.print(str(busy), markup=False)
            requested = await asyncio.to_thread(
                click.confirm, "Request takeover?", default=False
            )
        if not requested or busy.store is None:
            raise
        from amplifier_foundation.session import request_release

        deadline = time.monotonic() + timeout
        result = await request_release(
            busy.store,
            expected_owner=busy.owner,
            request_id=uuid.uuid4().hex,
            requester_app="Amplifier CLI",
            timeout=timeout,
        )
        # The reply may be lost after release. Try the real lock, but never send
        # a second request to a newly observed owner.
        while True:
            try:
                return SharedRootSession.acquire(config.session_id)
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
                    raise current from None
                await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))


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
        from amplifier_foundation.session import ReadyToRelease, CannotRelease

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
                self.console.print(
                    f"CLI session closed at the request of {self.source}. "
                    "Session history saved. Execution ownership released.",
                    markup=False,
                )
        elif self.root is not None:
            # Closing first prevents a new callback from entering after cleanup.
            if self.registration:
                await self.registration.close()
            self.root.release()
        if self.registration:
            await self.registration.close()
