"""Shared settings/registry files must never be observed half-written.

The defect (recipes-2v5): on a shared host running hundreds of `amplifier`
processes, a bundle registering a `model_role_resolver` capability vanished
from `~/.amplifier/settings.yaml` for ~10 minutes and then came back, with no
change to any command. Recipe steps carrying `model_role: reasoning`
hard-failed with `provider_roles=session-default-fallback` -- a confident,
well-worded error pointing at the *recipe*, which is the wrong place to look.

Two distinct hazards produced that, and they need different remedies:

* **Torn reads** -- `utils/settings_manager.py` truncated settings.yaml in
  place, so a concurrently-starting session could read a partial file. A
  partial YAML document parses fine; it simply lacks whatever had not been
  written yet. Remedy: `utils/atomic_write.py` (temp in same dir, fsync,
  os.replace).
* **Lost updates** -- writing the update-check timestamp is a read-modify-write
  of the *entire* file, and it ran with no lock. A `bundle add` landing
  between its read and its write was silently erased. Atomicity does not fix
  this; the whole sequence must hold the same lock `lib/settings.py` uses.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from filelock import FileLock

from amplifier_app_cli.utils import settings_manager
from amplifier_app_cli.utils.atomic_write import (
    atomic_write_json,
    atomic_write_text,
    atomic_write_yaml,
)


# ---------------------------------------------------------------------------
# The write primitive
# ---------------------------------------------------------------------------


def test_atomic_write_never_touches_the_target_before_the_replace(tmp_path, monkeypatch):
    """Whole old file or whole new file -- there is no third state.

    Asserted at the mechanism: the destination still holds the OLD bytes at
    the instant `os.replace` is called, and the temp file it is replaced from
    lives in the same directory (so the rename cannot cross a filesystem and
    stop being atomic).
    """
    target = tmp_path / "settings.yaml"
    target.write_text("old: 1\n", encoding="utf-8")

    observed: dict[str, object] = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        observed["target_at_replace"] = Path(dst).read_text(encoding="utf-8")
        observed["same_dir"] = Path(src).parent == Path(dst).parent
        observed["src_content"] = Path(src).read_text(encoding="utf-8")
        return real_replace(src, dst)

    fsyncs: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd):
        fsyncs.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(os, "fsync", spy_fsync)

    atomic_write_text(target, "new: 2\n")

    assert observed["target_at_replace"] == "old: 1\n", (
        "the destination was modified before the replace -- that is the torn-read window"
    )
    assert observed["same_dir"] is True, "temp file must live beside the target"
    assert observed["src_content"] == "new: 2\n"
    assert fsyncs, "the temp file must be fsynced before it is renamed into place"
    assert target.read_text(encoding="utf-8") == "new: 2\n"


def test_atomic_write_leaves_no_temp_files_behind(tmp_path):
    target = tmp_path / "settings.yaml"
    for i in range(5):
        atomic_write_yaml(target, {"n": i})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.yaml"]
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == {"n": 4}


def test_atomic_write_preserves_the_original_when_serialization_fails(tmp_path):
    """A failed write must not destroy the file it was going to replace."""
    target = tmp_path / "settings.yaml"
    target.write_text("keep: me\n", encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(Exception):
        atomic_write_yaml(target, {"bad": Unserializable()})

    assert target.read_text(encoding="utf-8") == "keep: me\n"
    assert [p.name for p in tmp_path.iterdir()] == ["settings.yaml"]


def test_atomic_write_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deeper" / "settings.yaml"
    atomic_write_json(target, {"a": 1})
    assert target.exists()


def test_atomic_write_survives_a_reader_racing_it(tmp_path):
    """A reader polling flat-out must only ever see complete documents.

    Two documents, deliberately large and different in length, so a
    truncate-in-place writer would leave an observable partial file.
    """
    target = tmp_path / "settings.yaml"
    doc_a = yaml.safe_dump({"which": "a", "pad": ["a" * 64] * 400})
    doc_b = yaml.safe_dump({"which": "b", "pad": ["b" * 32] * 900})
    atomic_write_text(target, doc_a)

    stop = threading.Event()
    torn: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                seen = target.read_text(encoding="utf-8")
            except FileNotFoundError:
                torn.append("<missing>")
                continue
            if seen not in (doc_a, doc_b):
                torn.append(seen[:80])

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for i in range(150):
            atomic_write_text(target, doc_b if i % 2 else doc_a)
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=5)

    assert not torn, f"reader observed {len(torn)} partial file(s): {torn[:3]}"


# ---------------------------------------------------------------------------
# settings_manager: the writer that actually caused the incident
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_file(tmp_path, monkeypatch) -> Path:
    """Point settings_manager at a throwaway settings.yaml."""
    path = tmp_path / ".amplifier" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_manager, "SETTINGS_FILE", path)
    return path


_REGISTERED_BUNDLE = {
    "bundle": {"added": {"anchors-amp-dev": "git+https://example.invalid/x"}},
    "routing": {"matrix": "anthropic"},
    "updates": {"check_frequency_hours": 4, "auto_prompt": True, "last_check": None},
}


def test_timestamp_write_preserves_another_process_registration(settings_file):
    """The exact incident: writing `last_check` must not erase `bundle.added`."""
    settings_file.write_text(yaml.safe_dump(_REGISTERED_BUNDLE), encoding="utf-8")

    settings_manager.save_update_last_check(datetime(2026, 9, 6, 12, 0, 0))

    after = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert after["bundle"]["added"] == _REGISTERED_BUNDLE["bundle"]["added"]
    assert after["routing"]["matrix"] == "anthropic"
    assert after["updates"]["last_check"] == "2026-09-06T12:00:00"


def test_timestamp_write_refuses_to_overwrite_an_unparseable_settings_file(
    settings_file, caplog
):
    """A read that failed is not a mandate to replace the file with defaults.

    Before: `load_settings()` swallowed the parse error and returned
    DEFAULT_SETTINGS, which `save_update_last_check` then wrote back --
    replacing a real settings file with a three-key stub.
    """
    corrupt = "bundle: {added: {a: b\nrouting: [unclosed\n"
    settings_file.write_text(corrupt, encoding="utf-8")

    settings_manager.save_update_last_check(datetime(2026, 9, 6, 12, 0, 0))

    assert settings_file.read_text(encoding="utf-8") == corrupt


def test_timestamp_write_is_atomic(settings_file, monkeypatch):
    settings_file.write_text(yaml.safe_dump(_REGISTERED_BUNDLE), encoding="utf-8")

    at_replace: list[str] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        at_replace.append(Path(dst).read_text(encoding="utf-8"))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    settings_manager.save_update_last_check(datetime(2026, 9, 6, 12, 0, 0))

    assert at_replace, "settings.yaml must be written via os.replace, not in place"
    assert "last_check: null" in at_replace[0] or "last_check: None" in at_replace[0], (
        "the destination should still hold the OLD document when replace is called"
    )


def test_timestamp_write_blocks_on_the_same_lock_lib_settings_uses(settings_file):
    """The read AND the write happen under one lock, not just the write.

    Holding `settings.yaml.lock` -- the same lock file
    `lib/settings.py::_scope_lock` takes for the global scope -- must stop
    `save_update_last_check` from making any change at all. That is what
    makes a concurrent `bundle add` safe.
    """
    settings_file.write_text(yaml.safe_dump(_REGISTERED_BUNDLE), encoding="utf-8")
    before = settings_file.read_text(encoding="utf-8")

    lock = FileLock(str(settings_file) + ".lock", timeout=10)
    done = threading.Event()

    def writer():
        settings_manager.save_update_last_check(datetime(2026, 9, 6, 12, 0, 0))
        done.set()

    with lock:
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        time.sleep(0.6)
        assert not done.is_set(), "the write proceeded while the lock was held"
        assert settings_file.read_text(encoding="utf-8") == before

    thread.join(timeout=15)
    assert done.is_set(), "the write never completed after the lock was released"

    after = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert after["bundle"]["added"] == _REGISTERED_BUNDLE["bundle"]["added"]
    assert after["updates"]["last_check"] == "2026-09-06T12:00:00"


def test_load_settings_creates_defaults_atomically(settings_file, monkeypatch):
    assert not settings_file.exists()

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    loaded = settings_manager.load_settings()

    assert loaded["updates"]["auto_prompt"] is True
    assert settings_file.exists()
    assert replaced, "the defaults file must be created via os.replace too"


def test_load_settings_does_not_clobber_a_file_created_while_it_waited(settings_file):
    """Re-check under the lock: another process may have won the creation race."""
    lock = FileLock(str(settings_file) + ".lock", timeout=10)
    result: list[dict] = []

    def loader():
        result.append(settings_manager.load_settings())

    with lock:
        thread = threading.Thread(target=loader, daemon=True)
        thread.start()
        time.sleep(0.3)
        # "Another process" registers a bundle while our loader waits.
        settings_file.write_text(yaml.safe_dump(_REGISTERED_BUNDLE), encoding="utf-8")

    thread.join(timeout=15)
    on_disk = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert on_disk["bundle"]["added"], "load_settings erased a file it did not create"


# ---------------------------------------------------------------------------
# lib/settings.py -- the bundle add/remove writer
# ---------------------------------------------------------------------------


def test_app_settings_scope_write_is_atomic_and_round_trips(tmp_path, monkeypatch):
    from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths

    global_settings = tmp_path / "global.yaml"
    paths = SettingsPaths(
        global_settings=global_settings,
        project_settings=tmp_path / "project.yaml",
        local_settings=tmp_path / "local.yaml",
    )
    settings = AppSettings(paths)
    settings.add_bundle("anchors-amp-dev", "git+https://example.invalid/x")

    at_replace: list[str] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        at_replace.append(Path(dst).read_text(encoding="utf-8"))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    settings.add_bundle("other", "git+https://example.invalid/y")

    assert at_replace, "a scope write must go through os.replace"
    assert "other" not in at_replace[0], (
        "the destination should still hold the OLD document when replace is called"
    )
    assert settings.get_added_bundles() == {
        "anchors-amp-dev": "git+https://example.invalid/x",
        "other": "git+https://example.invalid/y",
    }
