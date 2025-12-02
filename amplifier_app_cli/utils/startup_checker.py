"""Startup update checking with frequency control.

Philosophy: Simple, non-blocking, user-controllable.
"""

import logging
from datetime import datetime

from rich.console import Console

from .settings_manager import DEFAULT_SETTINGS
from .settings_manager import get_update_settings
from .settings_manager import save_update_last_check
from .source_status import check_all_sources

logger = logging.getLogger(__name__)
console = Console()


def should_check_on_startup() -> bool:
    """Decide if we should check based on frequency settings.

    Returns:
        True if we should check now, False otherwise
    """
    settings = get_update_settings()

    # Check if auto_prompt is enabled
    if not settings.get("auto_prompt", True):
        return False

    # Check last_check timestamp
    last_check = settings.get("last_check")
    if last_check is None:
        return True  # Never checked

    try:
        last_check_dt = datetime.fromisoformat(last_check)
        hours_since = (datetime.now() - last_check_dt).total_seconds() / 3600

        frequency_hours = settings.get("check_frequency_hours", DEFAULT_SETTINGS["updates"]["check_frequency_hours"])
        return hours_since >= frequency_hours
    except (ValueError, TypeError):
        # Invalid timestamp - treat as never checked
        return True


async def check_and_notify():
    """Check for updates and notify user if available.

    Philosophy: Non-blocking, graceful failure, visible when updates exist.
    """
    if not should_check_on_startup():
        return

    try:
        # Check all sources including cached modules (show progress indicator)
        with console.status("[dim]Checking for updates...[/dim]", spinner="dots"):
            report = await check_all_sources(include_all_cached=True)

        if report.has_updates:
            console.print()
            console.print(
                "[bold yellow]⚡ Updates available![/bold yellow] Run [cyan]amplifier update[/cyan] to install."
            )
            console.print()

        # Save check time even if no updates (respect frequency)
        save_update_last_check(datetime.now())

    except Exception as e:
        # Silently fail - don't disrupt startup
        logger.debug(f"Startup update check failed: {e}")
        # Still save timestamp to avoid spam retries
        save_update_last_check(datetime.now())
