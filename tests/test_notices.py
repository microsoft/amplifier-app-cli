import pytest

from amplifier_app_cli.ui.notices import NoticeKind
from amplifier_app_cli.ui.notices import TransientNoticeState


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


def test_notice_expires_after_four_seconds_by_default() -> None:
    clock = Clock()
    state = TransientNoticeState(clock=clock)

    notice = state.show("agents 1 done", kind=NoticeKind.SUCCESS)

    assert notice.expires_at == 14.0
    assert state.current() == notice
    clock.now = 14.0
    assert state.current() is None


def test_new_notice_replaces_previous_and_notifies_listeners() -> None:
    clock = Clock()
    state = TransientNoticeState(clock=clock)
    changes = []
    remove = state.add_listener(lambda: changes.append(state.current()))

    first = state.show("first")
    second = state.show("second", kind=NoticeKind.WARNING)
    remove()
    state.clear()

    assert first != second
    assert [notice.text for notice in changes] == ["first", "second"]


def test_notice_text_is_single_line_bounded_and_control_free() -> None:
    state = TransientNoticeState(clock=lambda: 1.0)

    notice = state.show("  copied\n\x1b  " + "x" * 300)

    assert "\n" not in notice.text
    assert "\x1b" not in notice.text
    assert len(notice.text) == 240


@pytest.mark.parametrize("duration", [0, -1, 31])
def test_notice_rejects_invalid_durations(duration: float) -> None:
    state = TransientNoticeState()

    with pytest.raises(ValueError):
        state.show("notice", duration_seconds=duration)


def test_notice_rejects_empty_text() -> None:
    state = TransientNoticeState()

    with pytest.raises(ValueError):
        state.show("\n\x1b")
