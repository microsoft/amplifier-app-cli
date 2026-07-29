"""Terminal title/notification sanitization hardening (codex terminal_title.rs).

Two-tier sanitization decision, documented here deliberately:

1. Title/notification path (``ui/repl.py::_sanitize_terminal_title``) uses the
   AGGRESSIVE set: control characters, Trojan-Source bidi controls
   (U+202A-202E, U+2066-2069), the full invisible-formatting block
   U+2060-206F, ZWSP/ZWNJ/ZWJ (U+200B-200D), LRM/RLM, variation selectors
   (U+FE00-FE0F and the U+E0100-E01EF supplement), BOM (U+FEFF), interlinear
   annotation (U+FFF9-FFFB), astral tag characters (U+E0000-E007F), soft
   hyphen, CGJ, Mongolian vowel separator -- plus whitespace collapsing and a
   240-character cap. Emoji fidelity does not matter in a window title, and
   title text is interpolated into OSC escape sequences, so we match codex,
   which applies this set to titles only.

2. Shared ``ui/runtime_values.py::sanitize()`` feeds transcript blocks, tool
   previews, footers, and agent lanes. It strips the same bidi controls, ZWSP,
   BOM, interlinear annotation, invisible operators (U+2060-2064), deprecated
   formatting (U+206A-206F), and astral tag characters -- but deliberately
   KEEPS ZWNJ/ZWJ (U+200C, U+200D) and variation selectors (U+FE00-FE0F)
   because removing them breaks emoji ZWJ sequences (e.g. woman technologist,
   U+1F469 ZWJ U+1F4BB) and complex scripts (Persian, Indic) that
   legitimately appear in model output.
"""

from amplifier_app_cli.ui.repl import _TITLE_MAX_CHARS
from amplifier_app_cli.ui.repl import _sanitize_terminal_title
from amplifier_app_cli.ui.repl import build_terminal_title
from amplifier_app_cli.ui.repl import terminal_notification_sequence
from amplifier_app_cli.ui.repl import terminal_title_sequence
from amplifier_app_cli.ui.runtime_values import sanitize


def test_title_strips_trojan_source_and_invisible_formatting() -> None:
    hostile = "Pro\u202ej\u2066e\u200fc\u061ct\u200b \ufeffT\u2060itle"
    assert _sanitize_terminal_title(hostile) == "Project Title"


def test_title_strips_zwj_zwnj_variation_selectors_and_tags() -> None:
    hostile = (
        "a\u200cb\u200dc"  # ZWNJ, ZWJ
        "\ufe0f\ufe00"  # variation selectors
        "\U000e0041\U000e007f"  # astral tag characters
        "\U000e0100"  # variation selectors supplement
        "\u00ad\u034f\u180ed"  # soft hyphen, CGJ, Mongolian vowel separator
        "\ufff9e\ufffaf\ufffb"  # interlinear annotation
    )
    assert _sanitize_terminal_title(hostile) == "abcdef"


def test_title_replaces_control_chars_and_collapses_whitespace() -> None:
    sanitized = _sanitize_terminal_title(
        "  Project\t|\nWorking\x1b\x07\x9d\x9c |  Thread  "
    )
    assert sanitized == "Project | Working | Thread"


def test_title_caps_at_240_chars() -> None:
    assert _TITLE_MAX_CHARS == 240
    sanitized = _sanitize_terminal_title("a" * (_TITLE_MAX_CHARS + 10))
    assert len(sanitized) == _TITLE_MAX_CHARS
    assert not _sanitize_terminal_title("").strip()


def test_title_cap_never_leaves_trailing_whitespace() -> None:
    sanitized = _sanitize_terminal_title("a" * (_TITLE_MAX_CHARS - 1) + " bcd")
    assert len(sanitized) <= _TITLE_MAX_CHARS
    assert sanitized == sanitized.rstrip()


def test_title_sequence_rejects_osc_injection_with_bidi_smuggling() -> None:
    sequence = terminal_title_sequence("ok\u202e\x1b]0;evil\x07\u2066 title")
    payload = sequence[len("\x1b]0;") : -1]
    assert sequence.startswith("\x1b]0;")
    assert sequence.endswith("\a")
    assert "\x1b" not in payload
    assert "\x07" not in payload
    assert "\u202e" not in payload
    assert "\u2066" not in payload


def test_notification_sequence_strips_invisibles_and_bounds_fields() -> None:
    notification = terminal_notification_sequence(
        "Amp\u202elifier" + "t" * 200, "done\u200b\ufeff " + "b" * 400
    )
    body = notification[len("\x1b]777;notify;") : -1]
    assert notification.startswith("\x1b]777;notify;")
    assert notification.endswith("\a")
    assert "\u202e" not in body
    assert "\u200b" not in body
    assert "\ufeff" not in body
    title_field, body_field = body.split(";", maxsplit=1)
    assert len(title_field) <= 80
    assert len(body_field) <= 240


def test_build_terminal_title_sanitizes_untrusted_task_summary() -> None:
    title = build_terminal_title(
        cwd="/tmp/amplifier-app-cli",
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        task_summary="fix\u202e \x1b]0;evil\x07 the bug",
        is_running=True,
    )
    assert "\x1b" not in title
    assert "\x07" not in title
    assert "\u202e" not in title
    assert len(title) <= _TITLE_MAX_CHARS


def test_shared_sanitize_strips_bidi_zwsp_bom_and_tag_chars() -> None:
    hostile = (
        "a\u202eb\u2066c\u061cd"  # Trojan-Source bidi controls
        "\u200be\ufefff"  # ZWSP, BOM
        "\u2060\u2061\u2062\u2063\u2064g"  # word joiner + invisible operators
        "\u206a\u206fh"  # deprecated formatting
        "\ufff9i\ufffaj\ufffbk"  # interlinear annotation
        "\U000e0041\U000e007fl"  # astral tag characters
    )
    assert sanitize(hostile) == "abcdefghijkl"


def test_shared_sanitize_preserves_emoji_sequences() -> None:
    # ZWJ sequence (woman technologist) and VS16 presentation (red heart)
    # must survive sanitize() -- see module docstring for the decision.
    woman_technologist = "\U0001f469\u200d\U0001f4bb"
    red_heart = "\u2764\ufe0f"
    zwnj_script = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"  # Persian
    assert sanitize(woman_technologist) == woman_technologist
    assert sanitize(red_heart) == red_heart
    assert sanitize(zwnj_script) == zwnj_script


def test_shared_sanitize_keeps_newlines_and_tabs() -> None:
    assert sanitize("line1\r\nline2\tend\u200b") == "line1\nline2\tend"
