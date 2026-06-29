"""Anchored steering input during agent turns.

``SteeringInputManager`` runs a ``prompt_toolkit`` prompt pinned at the bottom
of the terminal while the agent works a turn.  Typed input is forwarded to
``session.steer()``; a live "N queued" badge in the prompt line stays
visible until all steers have been drained by the orchestrator.

Design notes
~~~~~~~~~~~~
* ``patch_stdout()`` is activated by ``_execute_with_interrupt`` around the
  *whole* turn (not just the prompt).  Rich's ``Console.file`` property reads
  ``sys.stdout`` dynamically (``self._file`` is ``None`` by default in the
  singleton created at module import), so the patched proxy is picked up at
  write time automatically — no changes to ``console.py`` are required.

* ``PromptSession.app`` is the persistent ``Application`` object created once
  in ``PromptSession.__init__`` and reused across ``prompt_async()`` calls.
  ``Application.invalidate()`` is thread-safe (uses ``loop.call_soon_threadsafe``
  internally), so it is safe to call from any coroutine on the same event loop.

* ``_input_provider`` allows tests to inject a mock input source without
  spinning up a real TTY.  Production code leaves it ``None``.

Correctness interactions handled
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. **patch_stdout** – caller activates it for the whole turn; Rich writes flow
   through the patched ``sys.stdout`` and appear above the pinned prompt.
2. **StdinArbiter** – when ``arbiter.approval_active`` is ``True`` the running
   ``prompt_async()`` task is cancelled (releasing the terminal), the manager
   waits for approval to finish, then restarts the prompt.
3. **Ctrl-C / SIGINT** – ``KeyboardInterrupt`` raised from ``prompt_async()``
   is caught and the loop continues; the ``sigint_handler`` installed by
   ``_execute_with_interrupt`` has already updated the cancellation token.
4. **Teardown** – ``stop_event.set()`` + ``task.cancel()`` + ``await`` in the
   ``finally`` block of ``_execute_with_interrupt``; the inner polling loop
   honours ``stop_event`` within the next 50 ms tick.
5. **Empty / whitespace** – silently ignored in ``_enqueue()``.
   **Fail-loud** – visible message when ``steer_cap`` is ``None``.
   **Overflow** – visible rejection on ``SteeringQueueFull``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class SteeringInputManager:
    """Anchored steering prompt + queued-badge for a single agent turn.

    Lifecycle::

        manager = SteeringInputManager(steer_cap, arbiter, stop_event, console)
        task = asyncio.create_task(manager.run())
        # ... turn runs ...
        stop_event.set()
        task.cancel()
        await task   # or swallow CancelledError
    """

    def __init__(
        self,
        steer_cap: Any | None,
        arbiter: Any | None,
        stop_event: asyncio.Event,
        console: Any,
        *,
        _input_provider: (Callable[[], Coroutine[Any, Any, str | None]] | None) = None,
    ) -> None:
        self._steer_cap = steer_cap
        self._arbiter = arbiter
        self._stop_event = stop_event
        self._console = console
        # Monotonic pair — increment-only counters that are the single source
        # of truth for the badge.  Badge = max(0, _enqueued_total - _injected_total).
        # Order-insensitive: cannot go negative; self-heals once events catch up.
        self._enqueued_total: int = 0
        self._injected_total: int = 0
        # PromptSession is created lazily in run(); stored here so
        # on_steering_injected() can call self._pt_session.app.invalidate().
        self._pt_session: Any = None
        # Injectable for tests (avoids real TTY requirement).
        self._input_provider = _input_provider

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of messages currently queued but not yet injected.

        Derived from the monotonic pair: ``max(0, _enqueued_total -
        _injected_total)``.  The ``max(0, …)`` clamp prevents the badge from
        going negative if an inject event arrives from a cross-turn carry
        (§3.4 of steering-drain.md).
        """
        return max(0, self._enqueued_total - self._injected_total)

    # ------------------------------------------------------------------
    # Hook callback (registered on "orchestrator:steering_injected")
    # ------------------------------------------------------------------

    async def on_steering_injected(self, event: str, data: dict[str, Any]) -> None:
        """Increment injected_total when the orchestrator drains one steer.

        Signature matches the Amplifier hook dispatcher calling convention:
        ``handler(event_type: str, payload: dict)`` — two positional arguments
        (see ``IncrementalSaveHook.on_tool_post`` in ``incremental_save.py`` for
        the canonical reference).

        One ``orchestrator:steering_injected`` event == one drained message.
        Payload keys emitted by the orchestrator: ``orchestrator``, ``content``,
        ``iteration``, ``queued_remaining``, ``metadata``.

        Badge derives from ``max(0, _enqueued_total - _injected_total)`` so an
        extra/foreign inject event cannot drive it below zero — the ``max``
        clamp in ``pending_count`` handles that.
        """
        self._injected_total += 1
        if self._pt_session is not None:
            try:
                self._pt_session.app.invalidate()
            except Exception:
                pass  # App not running or already cleaned up; non-fatal

    # ------------------------------------------------------------------
    # Prompt message (callable passed to prompt_toolkit as ``message=``)
    # ------------------------------------------------------------------

    def _prompt_message(self) -> Any:
        """Return the current prompt message with live queued-count annotation.

        Passed as ``message=self._prompt_message`` (a callable) so
        prompt_toolkit re-evaluates it on each ``app.invalidate()`` call,
        keeping the queued count visible without bottom-toolbar CPR support.

        When N messages are queued: ``"  steer (N queued ⧗): "``
        When no messages are pending: ``"  steer: "``
        """
        from prompt_toolkit.formatted_text import HTML

        n = self.pending_count  # derived from monotonic pair
        if n > 0:
            return HTML(f"  steer ({n} queued \u29d7): ")
        return HTML("  steer: ")

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def _enqueue(self, text: str) -> None:
        """Validate *text*, call steer_cap, increment enqueued_total, print ack.

        Empty / whitespace-only *text* is silently ignored (not forwarded).
        All other failures are surfaced as visible console messages (fail-loud).

        Ack gating (§5.3 of steering-drain.md):
          * ``stop_event`` **not** set (turn live): ``⧗ queued: {text}``
          * ``stop_event`` **set** (turn ending/ended): ``⧗ queued for next turn: {text}``
        """
        if not text.strip():
            return  # Silently ignore blank / whitespace-only lines

        if self._steer_cap is None:
            self._console.print(
                "[yellow]Steering unavailable: this orchestrator does not "
                "support session.steer.[/yellow]"
            )
            return

        try:
            self._steer_cap(text)
            self._enqueued_total += 1
            # Gate the ack on whether the turn is still live (§5.3 steering-drain.md).
            # stop_event set  → steer will apply to the NEXT turn (turn ending/ended)
            # stop_event clear → steer will be consumed by the current turn (live)
            if self._stop_event.is_set():
                self._console.print(f"[dim]\u29d7 queued for next turn: {text}[/dim]")
            else:
                self._console.print(f"[dim]\u29d7 queued: {text}[/dim]")
            # Refresh the prompt so the callable message re-renders with the new count.
            if self._pt_session is not None:
                try:
                    self._pt_session.app.invalidate()
                except Exception:
                    pass
        except Exception as exc:  # ValueError, SteeringQueueFull, etc.
            self._console.print(f"[red]Steering rejected: {exc}[/red]")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the anchored steering prompt for the duration of a turn.

        Exits when ``stop_event`` is set (normal turn end), on EOF (Ctrl-D),
        or when the task is cancelled by the ``finally`` block in
        ``_execute_with_interrupt``.
        """
        if self._input_provider is None:
            from prompt_toolkit import PromptSession

            self._pt_session = PromptSession()
            # Pass a callable so prompt_toolkit re-evaluates the message on each
            # app.invalidate() call, keeping the queued count live in the prompt.
            _message: Any = self._prompt_message
        else:
            _message = None  # not used in the test / injected path

        try:
            while not self._stop_event.is_set():
                # Suspend during approval — yield stdin to Confirm.ask.
                if self._arbiter is not None and self._arbiter.approval_active:
                    await asyncio.sleep(0.05)
                    continue

                # Launch input as a cancellable task so we can abort it when
                # approval starts mid-prompt or stop_event fires.
                if self._input_provider is not None:
                    prompt_task: asyncio.Task[str | None] = asyncio.create_task(
                        self._input_provider()
                    )
                else:
                    assert self._pt_session is not None
                    prompt_task = asyncio.create_task(
                        self._pt_session.prompt_async(
                            message=_message,
                        )
                    )

                # Poll until the task finishes or an abort condition arises.
                aborted_for_approval = False
                while not prompt_task.done():
                    if self._stop_event.is_set():
                        # Turn ended — cancel and exit cleanly.
                        prompt_task.cancel()
                        try:
                            await prompt_task
                        except (
                            asyncio.CancelledError,
                            KeyboardInterrupt,
                            EOFError,
                        ):
                            pass
                        return

                    if self._arbiter is not None and self._arbiter.approval_active:
                        # Abort prompt so approval gets exclusive stdin access.
                        prompt_task.cancel()
                        try:
                            await prompt_task
                        except (
                            asyncio.CancelledError,
                            KeyboardInterrupt,
                            EOFError,
                        ):
                            pass
                        # Wait for approval to finish before restarting.
                        while (
                            self._arbiter is not None
                            and self._arbiter.approval_active
                            and not self._stop_event.is_set()
                        ):
                            await asyncio.sleep(0.05)
                        aborted_for_approval = True
                        break

                    await asyncio.sleep(0.05)

                if aborted_for_approval:
                    continue  # Restart outer while — re-check stop_event

                if self._stop_event.is_set():
                    if not prompt_task.done():
                        prompt_task.cancel()
                        try:
                            await prompt_task
                        except (
                            asyncio.CancelledError,
                            KeyboardInterrupt,
                            EOFError,
                        ):
                            pass
                    return

                # Retrieve result (task is already done at this point).
                try:
                    text = await prompt_task
                except asyncio.CancelledError:
                    continue
                except KeyboardInterrupt:
                    # Ctrl-C: sigint_handler in _execute_with_interrupt has
                    # already updated the cancellation token.  Just continue —
                    # the poll loop will detect it and cancel execute_task.
                    continue
                except EOFError:
                    # Ctrl-D: user is done with steering input.
                    break
                except Exception as exc:
                    logger.debug("Steering prompt error: %s", exc)
                    continue

                if self._stop_event.is_set():
                    break

                if text is not None:
                    await self._enqueue(text.strip() if text else "")

        finally:
            self._pt_session = None
