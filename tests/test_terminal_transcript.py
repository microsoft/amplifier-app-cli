"""Tests for stateful terminal transcript capture."""

from amplifier_app_cli.ui.terminal_transcript import TerminalTranscript


def _fragments(transcript: TerminalTranscript, line: int = 0):
    return list(transcript.formatted_lines[line])


def test_plain_text_and_bounded_lines_report_omissions():
    transcript = TerminalTranscript(max_lines=3)

    transcript.write("zero\none\ntwo\nthree")

    assert transcript.plain_lines == ("one", "two", "three")
    assert transcript.plain_text == "one\ntwo\nthree"
    assert transcript.omitted_line_count == 1
    assert transcript.omitted_count == 1


def test_unbounded_transcript_retains_every_completed_line():
    transcript = TerminalTranscript(max_lines=None)

    transcript.write("\n".join(f"history-{index:05d}" for index in range(20_025)))
    lines = transcript.plain_lines

    assert len(lines) == 20_025
    assert lines[0] == "history-00000"
    assert lines[-1] == "history-20024"
    assert transcript.omitted_line_count == 0


def test_sgr_styles_survive_split_writes_and_reset():
    transcript = TerminalTranscript()

    transcript.write("\x1b[1;3")
    transcript.write("1mred")
    transcript.write(" text\x1b[0m normal")

    assert transcript.plain_lines == ("red text normal",)
    assert _fragments(transcript) == [
        ("ansired bold", "red text"),
        ("", " normal"),
    ]
    assert "\x1b" not in transcript.plain_text


def test_extended_sgr_colors_become_prompt_toolkit_styles():
    transcript = TerminalTranscript()

    transcript.write("\x1b[38;2;10;20;30mtrue ")
    transcript.write("\x1b[48;5;196mindexed")

    assert _fragments(transcript) == [
        ("#0a141e", "true "),
        ("#0a141e bg:#ff0000", "indexed"),
    ]


def test_split_osc_hyperlinks_keep_label_and_visible_target():
    transcript = TerminalTranscript()

    transcript.write("before \x1b]8;;https://exam")
    transcript.write("ple.com\x1b\\docs")
    transcript.write("\x1b]8;;\x1b\\ after")

    assert transcript.plain_text == "before docs (https://example.com) after"
    assert "\x1b" not in transcript.plain_text


def test_bel_terminated_osc_is_discarded_across_writes():
    transcript = TerminalTranscript()

    transcript.write("left\x1b]0;window")
    transcript.write(" title\x07right")

    assert transcript.plain_text == "leftright"


def test_bel_still_terminates_osc_after_an_embedded_escape():
    transcript = TerminalTranscript()

    transcript.write("left\x1b]0;title\x1b\x07right")

    assert transcript.plain_text == "leftright"


def test_non_osc_terminal_strings_and_unknown_escapes_never_leak():
    transcript = TerminalTranscript()

    transcript.write("a\x1bPignored")
    transcript.write(" payload\x1b\\b\x1bc")
    transcript.write("c\x9fmore\x9cd")

    assert transcript.plain_text == "abcd"
    assert all(ord(char) >= 32 for char in transcript.plain_text)


def test_carriage_return_overwrites_from_start_of_current_line():
    transcript = TerminalTranscript()

    transcript.write("progress 10%\rprogress 20%\n")
    transcript.write("long-value\rshort\n")

    assert transcript.plain_lines == ("progress 20%", "shortvalue")


def test_erase_line_supports_short_carriage_return_updates():
    transcript = TerminalTranscript()

    transcript.write("long progress\r\x1b[2Kdone")

    assert transcript.plain_text == "done"


def test_wide_characters_use_terminal_cell_overwrite_semantics():
    transcript = TerminalTranscript()

    transcript.write("界界\rA")

    assert transcript.plain_text == "A 界"


def test_sgr_style_continues_across_lines_and_formatted_text_has_newlines():
    transcript = TerminalTranscript()

    transcript.write("\x1b[36mone\ntwo")

    assert transcript.plain_lines == ("one", "two")
    assert _fragments(transcript, 0) == [("ansicyan", "one")]
    assert _fragments(transcript, 1) == [("ansicyan", "two")]
    assert list(transcript.formatted_text) == [
        ("ansicyan", "one"),
        ("", "\n"),
        ("ansicyan", "two"),
    ]


def test_incomplete_escape_bytes_are_not_exposed():
    transcript = TerminalTranscript()

    transcript.write("safe\x1b[31")
    assert transcript.plain_text == "safe"

    transcript.write("mred")
    assert transcript.plain_text == "safered"
    assert "\x1b" not in transcript.plain_text


def test_clear_resets_output_omissions_and_active_style():
    transcript = TerminalTranscript(max_lines=1)
    transcript.write("\x1b[31mold\nnew")

    transcript.clear()
    transcript.write("plain")

    assert transcript.plain_lines == ("plain",)
    assert transcript.omitted_line_count == 0
    assert _fragments(transcript) == [("", "plain")]


def test_constructor_rejects_invalid_bounds():
    try:
        TerminalTranscript(max_lines=0)
    except ValueError as error:
        assert "max_lines" in str(error)
    else:
        raise AssertionError("Expected max_lines validation")


def test_huge_cursor_parameters_are_bounded():
    transcript = TerminalTranscript()

    transcript.write("start\x1b[999999999999999999999Cx")
    transcript.write("\x1b[999999999999999999999Gz")

    assert len(transcript.plain_text) < 20_000
    assert "\x1b" not in transcript.plain_text


def test_osc_link_target_drops_embedded_control_characters():
    transcript = TerminalTranscript()

    transcript.write("\x1b]8;;https://example.com/bad\n\x00\tpath\x1b\\docs")
    transcript.write("\x1b]8;;\x1b\\")

    assert transcript.plain_text == "docs (https://example.com)"


def test_osc_link_target_hides_credentials_query_and_fragment():
    transcript = TerminalTranscript()

    transcript.write(
        "\x1b]8;;https://user:secret@example.com/private?token=signed#fragment\x1b\\"
        "docs\x1b]8;;\x1b\\"
    )

    assert transcript.plain_text == "docs (https://example.com)"
    assert "secret" not in transcript.plain_text
    assert "token" not in transcript.plain_text


def test_line_cells_and_combining_marks_are_bounded():
    transcript = TerminalTranscript()

    transcript.write("x" + "\u0301" * 10_000)
    transcript.write("\n" + "y" * 100_000)

    first, second = transcript.plain_lines
    assert len(first) <= 32
    assert len(second) < 1_024
