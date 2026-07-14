from __future__ import annotations

from amplifier_app_cli.ui.bottom_stdout import TranscriptOutput
from amplifier_app_cli.ui.bottom_stdout import TranscriptOutputBridge


def test_flush_commits_one_atomic_transcript_chunk() -> None:
    chunks: list[str] = []
    output = TranscriptOutput(chunks.append)

    assert output.write("immutable ") == len("immutable ")
    assert output.write("transcript\n") == len("transcript\n")
    output.flush()

    assert chunks == ["immutable transcript\n"]


def test_nested_batch_defers_flush_and_commits_once() -> None:
    chunks: list[str] = []
    output = TranscriptOutput(chunks.append)

    with output.batch():
        output.write("restored ")
        output.flush()
        with output.batch():
            output.write("history")
            output.flush()
        assert chunks == []
        output.write("\n")

    assert chunks == ["restored history\n"]


def test_bridge_routes_stdout_and_restores_it(capsys) -> None:
    chunks: list[str] = []
    bridge = TranscriptOutputBridge(chunks.append)

    with bridge.patch():
        print("captured")
    print("ordinary")

    assert chunks == ["captured\n"]
    assert capsys.readouterr().out == "ordinary\n"
