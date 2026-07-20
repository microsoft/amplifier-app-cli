"""Behavior composition and bundle preparation presentation policy."""

from __future__ import annotations

from ..lib.settings import NotificationFlags


def _format_progress(action: str, detail: str) -> str:
    """Format a foundation preparation event for the CLI spinner."""
    labels = {
        "loading": f"Loading {detail}",
        "composing": f"Composing {detail}",
        "installing_package": f"Installing package {detail}",
        "activating": f"Activating {detail}",
        "installing": f"Installing {detail}",
    }
    return labels.get(action, f"{action}: {detail}")


def _build_modes_behaviors() -> list[str]:
    """Return the always-available modes behavior URI."""
    return [
        "git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=behaviors/modes.yaml",
    ]


def _build_notification_behaviors(flags: NotificationFlags) -> list[str]:
    """Build notification behavior URIs from resolved app policy flags."""
    if not (flags.desktop_enabled or flags.push_enabled):
        return []

    behaviors = ["git+https://github.com/microsoft/amplifier-bundle-notify@main"]
    if flags.desktop_enabled:
        behaviors.append(
            "git+https://github.com/microsoft/amplifier-bundle-notify@main#subdirectory=behaviors/desktop-notifications.yaml"
        )
    if flags.push_enabled:
        behaviors.append(
            "git+https://github.com/microsoft/amplifier-bundle-notify@main#subdirectory=behaviors/push-notifications.yaml"
        )
    return behaviors
