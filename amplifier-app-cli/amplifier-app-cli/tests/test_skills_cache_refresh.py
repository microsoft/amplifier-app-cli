"""Tests for skills cache refresh in amplifier update command."""

from unittest.mock import patch

from rich.console import Console


def test_refresh_skills_cache_empty_dir_no_error(tmp_path):
    """_refresh_skills_cache returns gracefully when skills cache directory is empty."""
    from amplifier_app_cli.commands.update import _refresh_skills_cache

    # Create an empty skills cache directory
    skills_dir = tmp_path / ".amplifier" / "cache" / "skills"
    skills_dir.mkdir(parents=True)

    console = Console(quiet=True)

    with patch("pathlib.Path.home", return_value=tmp_path):
        _refresh_skills_cache(console)  # Should not raise


def test_refresh_skills_cache_missing_dir_no_error(tmp_path):
    """_refresh_skills_cache returns gracefully when skills cache dir does not exist."""
    from amplifier_app_cli.commands.update import _refresh_skills_cache

    # Do NOT create the directory -- function should return early without error
    console = Console(quiet=True)

    with patch("pathlib.Path.home", return_value=tmp_path):
        _refresh_skills_cache(console)  # Should not raise
