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
3. **Ctrl-C / SIGINT** – Two independent events arrive when Ctrl-C is pressed:
   the OS SIGINT signal and the ``\\x03`` TTY byte read by prompt_toolkit.

   Without special handling, prompt_toolkit's default ``c-c`` / ``<sigint>``
   key binding calls ``app.exit(exception=KeyboardInterrupt())``.  That
   ``KeyboardInterrupt`` propagates through the ``prompt_async()`` coroutine
   and into asyncio's ``Task.__step_run_and_handle_result()``, which catches
   ``KeyboardInterrupt``/``SystemExit`` and *re-raises* after storing the
   exception (CPython 3.11+).  The re-raise then escapes through
   ``Handle._run()`` (also re-raises ``KeyboardInterrupt``) and
   ``EventLoop._run_once()`` straight into ``asyncio.run()`` — crashing the
   process before any ``except KeyboardInterrupt`` clause in user code can
   catch it.  The ``except KeyboardInterrupt: continue`` that appears in
   ``run()`` around ``await prompt_task`` is therefore unreachable when the
   default binding is in use.

   Additionally, prompt_toolkit's ``Application.run_async()`` calls
   ``loop.add_signal_handler(SIGINT, ...)`` which **overrides** the
   ``signal.signal(SIGINT, sigint_handler)`` installed by
   ``_execute_with_interrupt``, so that handler (which updates the
   cancellation token) is never invoked.

   The fix uses two ``PromptSession`` parameters together:

   * ``interrupt_exception=_CtrlCInterrupt`` — replaces ``KeyboardInterrupt``
     with a plain ``Exception`` subclass.  asyncio's task machinery stores it
     as a normal task exception (no re-raise), so ``await prompt_task`` raises
     it in the normal place and the ``except _CtrlCInterrupt: continue`` clause
     in ``run()`` catches it cleanly.

   * ``handle_sigint=False`` passed to ``prompt_async()`` — prevents
     ``Application.run_async()`` from calling ``loop.add_signal_handler()``,
     leaving ``sigint_handler`` active so the cancellation token is updated
     correctly for graceful → immediate cancellation.

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


class _CtrlCInterrupt(Exception):
    """Sentinel raised by the steering PromptSession when Ctrl-C is pressed.

    Using a plain ``Exception`` subclass instead of ``KeyboardInterrupt``
    prevents asyncio's ``Task.__step_run_and_handle_result()`` from re-raising
    the exception after storing it (CPython 3.11+ re-raises only
    ``KeyboardInterrupt`` and ``SystemExit``).  The re-raise would otherwise
    escape through ``Handle._run()`` and crash ``asyncio.run()``.

    The actual SIGINT signal is handled separately by the ``sigint_handler``
    installed in ``_execute_with_interrupt`` (kept active because we pass
    ``handle_sigint=False`` to ``prompt_async()``).
    """


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
        command_processor: Any | None = None,
        session: Any = None,
        _input_provider: (Callable[[], Coroutine[Any, Any, str | None]] | None) = None,
        _mentions_expander: (
            Callable[[Any, str], Coroutine[Any, Any, str]] | None
        ) = None,
    ) -> None:
        self._steer_cap = steer_cap
        self._arbiter = arbiter
        self._stop_event = stop_event
        self._console = console
        # Reuse path (docs/designs/steering-input-reuse.md, Fork A, locked):
        # the SAME CommandProcessor + session used by the REPL, so steered
        # input goes through the SAME classification + @-mention expansion.
        # Both optional (None preserves the old raw-passthrough behavior) so
        # existing constructors/tests are unaffected.
        self._command_processor = command_processor
        self._session = session
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
        # Injectable for tests (avoids importing the real main.py module).
        # When None, the real amplifier_app_cli.main.process_runtime_mentions
        # is lazily imported in _enqueue -- same function the REPL calls.
        self._mentions_expander = _mentions_expander

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

    @property
    def is_composing(self) -> bool:
        """True while the user is actively mid-typing a steer.

        THROTTLE/COALESCE spike (revertable, display-only): consumed by
        hooks-streaming-ui via ``_execute_with_interrupt`` publishing this
        property as a callback (``hooks_instance._composing_fn``), so the
        streaming Live preview can reduce its repaint cadence instead of
        fighting the pinned steering prompt for the terminal.

        True iff a ``PromptSession`` is currently open (``_pt_session`` is
        not None) AND its in-progress buffer text is non-empty.  Defensive:
        ``_pt_session`` is None (not prompting) or any exception while
        inspecting prompt_toolkit internals (e.g. a torn-down Application)
        resolves to False rather than raising.
        """
        session = self._pt_session
        if session is None:
            return False
        try:
            return bool(session.app.current_buffer.text)
        except Exception:
            return False

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

        When N messages are queued: ``"  (N queued ⧗) "``
        When no messages are pending: empty string (the "steer" label is hidden).
        """
        from prompt_toolkit.formatted_text import HTML

        n = self.pending_count  # derived from monotonic pair
        if n > 0:
            return HTML(f"  ({n} queued \u29d7) ")
        return HTML("")

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def _enqueue(self, text: str) -> None:
        """Validate *text*, call steer_cap, increment enqueued_total, print ack.

        Empty / whitespace-only *text* is silently ignored (not forwarded).
        All other failures are surfaced as visible console messages (fail-loud).

        Turn-end gating:
          * ``stop_event`` **not** set (turn live): forward + ``⧗ queued: {text}``
          * ``stop_event`` **set** (turn ending/ended): do NOT forward — the
            orchestrator's execute()-entry clear() would discard it — and print
            an honest "turn ended — not sent" ack instead of a false promise.
        """
        if not text.strip():
            return  # Silently ignore blank / whitespace-only lines

        if self._steer_cap is None:
            self._console.print(
                "[yellow]Steering unavailable: this orchestrator does not "
                "support session.steer.[/yellow]"
            )
            return

        # Turn already ending/ended: a steer enqueued now is wiped by the
        # orchestrator's execute()-entry clear() before the next turn runs, so it
        # would NOT be delivered. Be honest (no false "queued for next turn"
        # promise) and do not enqueue it. Cross-turn delivery is the parked
        # FollowUpQueue's job, not the mid-turn SteeringQueue's.
        if self._stop_event.is_set():
            self._console.print(
                f"[yellow]\u29d7 turn ended \u2014 not sent: {text}\n"
                "  steering applies mid-turn; resend it while the agent is "
                "working.[/yellow]"
            )
            return

        # Reuse path (docs/designs/steering-input-reuse.md, Fork A, locked):
        # run steered text through the SAME CommandProcessor.process_input the
        # REPL uses, then the SAME process_runtime_mentions @-mention
        # expansion, before delivering it.  command_processor is optional
        # (None preserves the old raw-passthrough behavior for any caller that
        # does not wire it).
        if self._command_processor is not None:
            action, data = self._command_processor.process_input(text)

            if action != "prompt":
                # Locked command policy: REJECT-ALL mid-turn. No /mode
                # whitelist, no handle_command call on the steering path \u2014
                # an honest, actionable rejection instead of silent drop or
                # raw injection.
                self._console.print(
                    f"[yellow]\u29d7 commands aren't applied mid-turn \u2014 finish "
                    f"or cancel the turn first, then run it: {text}[/yellow]"
                )
                return

            expander = self._mentions_expander
            if expander is None:
                from .main import process_runtime_mentions as expander

            try:
                text = await expander(self._session, data["text"])
            except Exception as exc:
                # Must never strand the drain loop: catch, fail loud, do not
                # steer a half-expanded message.
                self._console.print(
                    f"[red]\u29d7 couldn't expand mentions: {exc}[/red]"
                )
                return

        try:
            self._steer_cap(text)
            self._enqueued_total += 1
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

            # interrupt_exception=_CtrlCInterrupt: replace the default
            # KeyboardInterrupt with a plain Exception subclass so that asyncio's
            # Task.__step_run_and_handle_result() does NOT re-raise after storing
            # the exception.  The re-raise only applies to KeyboardInterrupt /
            # SystemExit (CPython 3.11+) and would otherwise propagate through
            # Handle._run() → _run_once() → asyncio.run(), crashing the process.
            self._pt_session = PromptSession(interrupt_exception=_CtrlCInterrupt)
            # Pass a callable so prompt_toolkit re-evaluates the message on each
            # app.invalidate() call, keeping the queued count live in the prompt.
            _message: Any = self._prompt_message
        else:
            _message = None  # not used in the test / injected path

        # Tracks the current child input task (if any) so the outer
        # ``finally`` below can guarantee it is never left orphaned — see
        # that block for why this is necessary in addition to the explicit
        # cancel+await branches inside the poll loop.
        prompt_task: asyncio.Task[str | None] | None = None

        try:
            while not self._stop_event.is_set():
                # Suspend during approval — yield stdin to Confirm.ask.
                if self._arbiter is not None and self._arbiter.approval_active:
                    await asyncio.sleep(0.05)
                    continue

                # Launch input as a cancellable task so we can abort it when
                # approval starts mid-prompt or stop_event fires.
                if self._input_provider is not None:
                    prompt_task = asyncio.create_task(self._input_provider())
                else:
                    assert self._pt_session is not None
                    prompt_task = asyncio.create_task(
                        self._pt_session.prompt_async(
                            message=_message,
                            # handle_sigint=False: do NOT let prompt_toolkit call
                            # loop.add_signal_handler(SIGINT, ...), which would
                            # override the sigint_handler installed by
                            # _execute_with_interrupt and prevent the cancellation
                            # token from being updated when Ctrl-C is pressed.
                            handle_sigint=False,
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
                except _CtrlCInterrupt:
                    # Ctrl-C while the steering prompt was active.
                    # PromptSession raised _CtrlCInterrupt (not KeyboardInterrupt)
                    # so asyncio's task machinery stores it normally and does NOT
                    # re-raise, allowing this except clause to be reached.
                    # The OS SIGINT was handled by sigint_handler in
                    # _execute_with_interrupt (kept active via handle_sigint=False),
                    # which updated the cancellation token.  The poll loop in
                    # _execute_with_interrupt will detect the cancellation on its
                    # next 50 ms tick and cancel execute_task.
                    continue
                except KeyboardInterrupt:
                    # Safety net: should not be raised with the current
                    # PromptSession(interrupt_exception=_CtrlCInterrupt) setup,
                    # but kept in case a future code path reaches this point.
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
            # Orphan-prevention net (see the module docstring's "Teardown"
            # note and the ``prompt_task`` comment above): cancellation
            # delivered to run() overwhelmingly lands at the bare
            # ``await asyncio.sleep(0.05)`` in the inner poll loop above —
            # a point with no cleanup logic of its own — rather than at one
            # of the three explicit cancel+await branches inside that loop.
            # When that happens, control skips straight from the interrupted
            # await to this ``finally``, leaving ``prompt_task`` alive and
            # still holding prompt_toolkit's raw_mode() terminal context.
            # This block is the single, unconditional backstop: whatever
            # path got us here (return, break, normal fall-through, or a
            # propagating CancelledError/other exception), any live
            # prompt_task is cancelled and awaited before run() truly exits.
            # Safe to run even when the explicit branches already handled
            # cleanup (prompt_task.done() is then True and this is a no-op),
            # and safe to await here even while a CancelledError is actively
            # propagating out of this function (a single external cancel
            # request delivers exactly one CancelledError at the interrupted
            # await; further awaits in ``finally`` proceed normally unless
            # cancelled again).
            if prompt_task is not None and not prompt_task.done():
                prompt_task.cancel()
                try:
                    await prompt_task
                except (asyncio.CancelledError, KeyboardInterrupt, Exception):
                    pass
