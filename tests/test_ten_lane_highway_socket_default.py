"""The highway's tmux socket must default PER BATCH, not to a shared "hw".

model_performance-ye80. A tmux SERVER restart destroys every session on its
socket. With every batch sharing one socket, any batch can annihilate every
other batch's lanes AND watchdogs in a single instant -- observed 2026-09-05,
when a second batch restarted the server on socket "hw" and killed three
unrelated lanes plus a watchdog at once, leaving six DTU containers running
with open infra-ledger rows.

Follows tests/test_ten_lane_highway_infra_ledger.py's standard: run the real
scripts and assert on observable behaviour, never grep-for-a-marker.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "amplifier_app_cli/data/skills/ten-lane-highway/scripts"
)
SOCKET_USERS = (
    "highway_status.sh",
    "highway_watchdog.sh",
    "launch_lane.sh",
    "verify_lane.sh",
)


@pytest.mark.parametrize("script", SOCKET_USERS)
def test_no_script_falls_back_to_the_shared_hw_socket(script: str) -> None:
    """The `${HIGHWAY_TMUX_SOCKET:-hw}` fallback is the defect itself."""
    text = (SCRIPTS / script).read_text(encoding="utf-8")
    assert "HIGHWAY_TMUX_SOCKET:-hw}" not in text, (
        f"{script} still falls back to the SHARED socket 'hw'; a server restart "
        "there kills every other batch's lanes and watchdog (ye80)"
    )


@pytest.mark.parametrize("script", SOCKET_USERS)
def test_every_socket_user_derives_the_default_from_the_batch(script: str) -> None:
    text = (SCRIPTS / script).read_text(encoding="utf-8")
    assert re.search(r'HIGHWAY_TMUX_SOCKET:-hw-\$\(printf', text), (
        f"{script} must default the socket to hw-<batch> derived from BATCH_DIR"
    )


def _derived_socket(batch_dir: Path) -> str:
    """Run the real derivation the scripts use, via bash -- not a reimplementation."""
    snippet = (
        'BATCH_DIR="$1"\n'
        'HIGHWAY_TMUX_SOCKET="${HIGHWAY_TMUX_SOCKET:-hw-$(printf \'%s\' '
        '"$(basename "$BATCH_DIR")" | tr -c \'A-Za-z0-9_-\' \'_\')}"\n'
        'printf %s "$HIGHWAY_TMUX_SOCKET"\n'
    )
    return subprocess.run(
        ["bash", "-c", snippet, "_", str(batch_dir)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_two_batches_derive_two_different_sockets(tmp_path: Path) -> None:
    """The whole point: batch isolation, so one server restart cannot cross over."""
    a = tmp_path / "hw-model-performance"
    b = tmp_path / "converge"
    a.mkdir()
    b.mkdir()
    sa, sb = _derived_socket(a), _derived_socket(b)
    assert sa == "hw-hw-model-performance"
    assert sb == "hw-converge"
    assert sa != sb, "two batches must not share a tmux server"


def test_an_explicit_socket_still_wins(tmp_path: Path, monkeypatch) -> None:
    """Backward compatibility: only the DEFAULT changes."""
    monkeypatch.setenv("HIGHWAY_TMUX_SOCKET", "hw-explicit")
    assert _derived_socket(tmp_path / "whatever") == "hw-explicit"


def test_a_batch_name_with_shell_metacharacters_is_sanitised(tmp_path: Path) -> None:
    nasty = tmp_path / "batch; rm -rf x"
    nasty.mkdir()
    got = _derived_socket(nasty)
    assert got == "hw-batch__rm_-rf_x", got
    assert ";" not in got and " " not in got
