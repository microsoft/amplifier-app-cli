"""First direct prospective checkpoint failure at actual public CLI rendering.

Borrow the root assembly read-only. Core, the binding, native save and checkpoint
latch are real; deterministic orchestration, admission failure and terminal input
are synthetic. No providers, live terminal or network are contacted.
"""

import asyncio
import base64
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from copy import deepcopy
from importlib import import_module
from io import BytesIO, StringIO
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "linux", reason="Prospective Foundation custody is Linux-only"),
]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from test_session_media_versioned_runtime import runtime as root_runtime

    yield from root_runtime.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("entry", [
    "interactive", "initial", "text", "json", "json-trace", "failed-text", "failed-json",
])
@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("cleanup_fault", [False, True])
async def test_first_direct_checkpoint_failure_is_private_at_public_presentation(
    runtime, entry, verbose, cancelled, cleanup_fault,
):
    from amplifier_app_cli.session_checkpoint import CheckpointBlocked, check_checkpoint_ready
    from amplifier_app_cli.session_store import SessionStore
    from test_session_media_versioned_runtime import assert_held, committed, public_entry_environment

    main = import_module("amplifier_app_cli.main")
    conversions, prints, tracebacks, logs, payloads, failures, admission = [], [], [], [], [], [], []
    original_contexts = []
    from PIL import Image
    pixels = BytesIO()
    Image.new("RGB", (3, 2), (29, 61, 103)).save(pixels, "PNG")
    private = "data:image/png;base64," + base64.b64encode(pixels.getvalue()).decode() + "?secret=checkpoint-token"

    class PrivateFailure(asyncio.CancelledError if cancelled else RuntimeError):
        def __str__(self):
            conversions.append("str")
            return private

        def __repr__(self):
            conversions.append("repr")
            return private

    primary = PrivateFailure(private)
    primary.__cause__ = ValueError(private + "-cause")
    primary.__context__ = ValueError(private + "-context")
    BaseException.add_note(primary, private + "-note")
    cleanup_error = RuntimeError("synthetic unsettled cleanup") if cleanup_fault else None
    initial = runtime.binding.expected
    original_admit = runtime.admit

    def failed_admit():
        # FIRST final-save asset admission: no earlier checkpoint/hook failure.
        assert runtime.binding.expected == initial and runtime.binding.last_receipt is None
        assert len(admission) == 0
        original_admit()
        admission.append("published-before-refusal")
        raise primary

    runtime.admit = failed_admit

    async def finite_turn(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        runtime.host = coordinator.get_capability("cli.session_media")
        payload, ref = await runtime.stage("PNG", session=runtime.created[-1])
        assert payload.startswith(b"\x89PNG") and ref
        await context.add_message({"role": "assistant", "content": "finite synthetic reply"})
        if entry.startswith("failed-"):
            raise RuntimeError("synthetic orchestration failure")
        return "finite synthetic reply"

    runtime.execute = finite_turn
    prepared = runtime.configuration().prepared_bundle
    original_save = SessionStore.save_versioned
    original_json = json.dumps
    stdout, stderr = StringIO(), StringIO()

    class EntryConsole(Console):
        def print(self, *objects, **kwargs):
            prints.append((objects, kwargs))
            return super().print(*objects, **kwargs)

        def print_exception(self, *args, **kwargs):
            tracebacks.append(sys.exception())
            return super().print_exception(*args, **kwargs)

    def logger_entry(logger, level, message, arguments, exc_info=None, extra=None,
                     stack_info=False, stacklevel=1):
        logs.append((message, arguments, exc_info, extra, stack_info))

    def json_entry(value, *args, **kwargs):
        if type(value) is dict and value.get("status") in {"error", "success"}:
            payloads.append(value)
        return original_json(value, *args, **kwargs)

    def save(native, *args, **kwargs):
        try:
            return original_save(native, *args, **kwargs)
        except BaseException as error:
            failures.append(error)
            original_contexts.append(error.__context__)
            raise

    prompt = MagicMock()
    prompt.prompt_async = AsyncMock(side_effect=["finite prompt", "/exit"])
    with public_entry_environment(runtime, cleanup_error=cleanup_error) as evidence:
        patchers = (
            patch.object(main, "console", EntryConsole(file=evidence.output)),
            patch.object(main, "_create_prompt_session", return_value=prompt),
            patch.object(main, "AppSettings"),
            patch.object(main, "patch_stdout", new=lambda *a, **k: nullcontext()),
            patch.object(main, "process_runtime_mentions", AsyncMock(side_effect=lambda s, t: t)),
            patch.object(SessionStore, "save_versioned", new=save),
            patch.object(logging.Logger, "_log", new=logger_entry),
            patch.object(json, "dumps", new=json_entry),
        )
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            try:
                if entry in {"interactive", "initial"}:
                    await asyncio.wait_for(main.interactive_chat(
                        deepcopy(runtime.plan), [], verbose, session_id="root",
                        bundle_name="fixture-versioned", prepared_bundle=prepared,
                        canonical_binding=runtime.binding, canonical_admit=runtime.admit_callback,
                        initial_prompt="finite prompt" if entry == "initial" else None,
                    ), timeout=5)
                else:
                    await main.execute_single(
                        "finite prompt", deepcopy(runtime.plan), [], verbose, session_id="root",
                        bundle_name="fixture-versioned", prepared_bundle=prepared,
                        output_format=entry.removeprefix("failed-"),
                        canonical_binding=runtime.binding, canonical_admit=runtime.admit_callback,
                    )
            except BaseException as error:
                if cleanup_fault:
                    assert error is cleanup_error
                elif cancelled:
                    assert error is primary
                else:
                    assert entry not in {"interactive", "initial"} and isinstance(error, SystemExit)
                    assert error.code == 1
            else:
                assert entry in {"interactive", "initial"} and not cancelled and not cleanup_fault
        assert main._INTERACTIVE_SETUP.get() is None
        actual = runtime.created[-1]
        native = runtime.native_created[-1]
        failure = actual.coordinator.get_capability("_cli.checkpoint_failure")
        assert failure.primary is primary and failure.interrupted is cancelled
        assert failures == [primary] and admission == ["published-before-refusal"]
        assert primary.args == (private,)
        assert primary.__cause__.args == (private + "-cause",)
        # Python itself links a raise during an earlier execution failure to
        # that active exception. The presentation boundary must not rewrite it.
        assert primary.__context__ is original_contexts[0]
        if not entry.startswith("failed-"):
            assert primary.__context__.args == (private + "-context",)
        assert primary.__notes__ == [private + "-note"]
        assert not conversions
        if entry == "failed-text" and verbose:
            # The earlier ordinary orchestration error is intentionally still
            # rendered; only the later private checkpoint failure is withheld.
            assert len(tracebacks) == 1 and tracebacks[0] is not primary
        else:
            assert not tracebacks
        for objects, kwargs in prints:
            assert all(not isinstance(value, BaseException) for value in objects)
            assert all(private not in value for value in objects if type(value) is str)
        assert private not in evidence.output.getvalue() + stdout.getvalue() + stderr.getvalue()
        assert "... saved" not in evidence.output.getvalue()
        for message, arguments, exc_info, extra, stack_info in logs:
            assert not any(isinstance(value, BaseException) for value in arguments)
            assert type(message) is str and private not in message
            assert exc_info is None and extra is None and not stack_info
        if entry in {"json", "json-trace", "failed-json"}:
            parsed = json.loads(stdout.getvalue())  # Exactly one parseable failed payload.
            assert parsed["status"] == "error"
            if entry == "failed-json":
                assert "synthetic orchestration failure" in parsed["error"]
            else:
                assert parsed["error_type"] == "CheckpointBlocked"
                assert "Exception details withheld" in parsed["error"]
            assert len(payloads) == 1 and payloads[0] == parsed
            assert "response" not in parsed
        else:
            assert not payloads
        assert runtime.binding.expected == initial == committed(runtime.binding.directory)
        assert len(runtime.created) == len(runtime.native_created) == 1
        assert evidence.tty_closes == ["closed"]
        assert evidence.cleanup == (["attempt"] if cleanup_fault else ["attempt", "settled-before-owner-release"])
        if cleanup_fault:
            assert_held(runtime.binding)
            assert evidence.store_closes == []
        # No delivery after a FIRST failed final checkpoint; the private latch
        # independently blocks provider count and completion without replay.
        provider = actual.coordinator.get("providers")["fixture-versioned-provider"]
        raw = next(raw for raw, wrapped in runtime.host.wrappers.values() if wrapped is provider) if cleanup_fault else None
        with pytest.raises(CheckpointBlocked):
            check_checkpoint_ready(actual, native)
        if raw is not None:
            from amplifier_core.message_models import ChatRequest, Message
            request = ChatRequest(messages=[Message(role="user", content="blocked")])
            with pytest.raises(CheckpointBlocked):
                await provider.request_budget(request)
            with pytest.raises(CheckpointBlocked):
                await provider.complete(request)
            assert raw.observed == []
        assert failures == [primary] and admission == ["published-before-refusal"]
        if entry.startswith("failed-"):
            assert logs and logs[-1][0] == main._CHECKPOINT_FAILURE_TEXT
    if cleanup_fault:
        # Fixture-only settlement of the previously unentered physical cleanup.
        await evidence.actual_cleanup(runtime.created[-1])
        runtime.binding.close()


@pytest.mark.parametrize("entry", ["interactive", "initial", "text", "json", "json-trace"])
@pytest.mark.parametrize("verbose", [False, True])
async def test_unbound_final_save_error_keeps_existing_public_rendering(tmp_path, entry, verbose):
    from test_interactive_chat_tty_teardown import _make_initialized, _make_mock_session

    main = import_module("amplifier_app_cli.main")
    session = _make_mock_session()
    session.coordinator.get_capability.return_value = None
    context = session.coordinator.get("context")
    context.get_messages.return_value = [
        {"role": "user", "content": "finite prompt"},
        {"role": "assistant", "content": "finite reply"},
    ]
    primary = RuntimeError("ordinary-unbound-save-error")
    store = MagicMock()
    store.get_metadata_if_exists.return_value = {}
    store.save.side_effect = primary
    initialized = _make_initialized(session)
    initialized.store = store
    output, stdout = StringIO(), StringIO()
    console = Console(file=output)
    prompt = MagicMock()
    prompt.prompt_async = AsyncMock(side_effect=["finite prompt", "/exit"])
    patchers = (
        patch.object(main, "create_initialized_session", AsyncMock(return_value=initialized)),
        patch.object(main, "console", console),
        patch.object(main, "AppSettings"),
        patch.object(main, "_create_prompt_session", return_value=prompt),
        patch.object(main, "patch_stdout", new=lambda *a, **k: nullcontext()),
        patch.object(main, "process_runtime_mentions", AsyncMock(side_effect=lambda s, t: t)),
        patch("amplifier_app_cli.incremental_save.register_incremental_save"),
    )
    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        stack.enter_context(redirect_stdout(stdout))
        traceback = stack.enter_context(patch.object(console, "print_exception", wraps=console.print_exception))
        tty = stack.enter_context(patch.object(main, "close_dedicated_tty_input"))
        try:
            if entry in {"interactive", "initial"}:
                await main.interactive_chat(
                    {}, [tmp_path], verbose,
                    initial_prompt="finite prompt" if entry == "initial" else None,
                )
            else:
                await main.execute_single("finite prompt", {}, [], verbose, output_format=entry)
        except BaseException as error:
            if entry == "initial":
                assert error is primary
            else:
                assert entry not in {"interactive", "initial"}
                assert isinstance(error, SystemExit) and error.code == 1
    store.save.assert_called_once()
    initialized.cleanup.assert_awaited_once()
    tty.assert_called_once()
    if entry in {"json", "json-trace"}:
        assert json.loads(stdout.getvalue())["error"] == "ordinary-unbound-save-error"
    elif entry != "initial":
        assert "ordinary-unbound-save-error" in output.getvalue()
        assert traceback.call_count == int(verbose)
    assert "Exception details withheld" not in output.getvalue() + stdout.getvalue()
