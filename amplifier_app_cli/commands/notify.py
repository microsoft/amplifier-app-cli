"""Notification configuration commands.

Manages desktop and push notification settings for Amplifier sessions.
"""

from typing import cast

import click
from rich.console import Console
from rich.table import Table

from ..lib.app_settings import AppSettings as LegacyAppSettings
from ..lib.app_settings import ScopeType
from ..paths import create_config_manager
from ..paths import get_effective_scope
from ..paths import ScopeNotAvailableError

console = Console()


def _get_app_settings() -> LegacyAppSettings:
    """Get AppSettings instance with config manager."""
    config_manager = create_config_manager()
    return LegacyAppSettings(config_manager)


@click.group(name="notify")
def notify():
    """Configure notification settings.

    Manage desktop (terminal) and push (ntfy.sh) notifications
    that alert you when the assistant is ready for input.

    Examples:
        amplifier notify status
        amplifier notify desktop --enable
        amplifier notify ntfy --enable --topic my-topic
    """
    pass


@notify.command("status")
def status():
    """Show current notification settings.

    Displays merged configuration from all scopes (local > project > global).
    """
    settings = _get_app_settings()
    config = settings.get_notification_config()

    if not config:
        console.print("[yellow]No notifications configured[/yellow]")
        console.print("\n[dim]Enable with:[/dim]")
        console.print("  amplifier notify desktop --enable")
        console.print("  amplifier notify ntfy --enable --topic <your-topic>")
        return

    table = Table(
        title="Notification Settings", show_header=True, header_style="bold cyan"
    )
    table.add_column("Type", style="green")
    table.add_column("Setting", style="yellow")
    table.add_column("Value", style="white")

    # Desktop settings
    desktop = config.get("desktop", {})
    if desktop:
        table.add_row("desktop", "enabled", str(desktop.get("enabled", False)))
        for key in ["show_device", "show_project", "show_preview", "preview_length"]:
            if key in desktop:
                table.add_row("", key, str(desktop[key]))

    # ntfy settings
    ntfy = config.get("ntfy", {})
    if ntfy:
        table.add_row("ntfy", "enabled", str(ntfy.get("enabled", False)))
        for key in ["topic", "server"]:
            if key in ntfy:
                table.add_row("", key, str(ntfy[key]))

    console.print(table)


@notify.command("desktop")
@click.option(
    "--enable/--disable", default=None, help="Enable or disable desktop notifications"
)
@click.option(
    "--show-device/--no-show-device",
    default=None,
    help="Show device/hostname in notification",
)
@click.option(
    "--show-project/--no-show-project",
    default=None,
    help="Show project name in notification",
)
@click.option(
    "--show-preview/--no-show-preview",
    default=None,
    help="Show message preview in notification",
)
@click.option(
    "--preview-length", type=int, help="Max characters for preview (default: 100)"
)
@click.option("--local", "scope_flag", flag_value="local", help="Apply to local scope")
@click.option(
    "--project", "scope_flag", flag_value="project", help="Apply to project scope"
)
@click.option(
    "--global",
    "scope_flag",
    flag_value="global",
    help="Apply to global scope (default)",
)
def desktop_cmd(
    enable: bool | None,
    show_device: bool | None,
    show_project: bool | None,
    show_preview: bool | None,
    preview_length: int | None,
    scope_flag: str | None,
):
    """Configure desktop/terminal notifications.

    Desktop notifications use terminal escape sequences (OSC 777) that work
    in terminals like WezTerm, iTerm2, and others - even over SSH.

    Examples:
        amplifier notify desktop --enable
        amplifier notify desktop --disable
        amplifier notify desktop --enable --show-preview --global
        amplifier notify desktop --no-show-device --local
    """
    # Check if any option was provided
    if all(
        v is None
        for v in [enable, show_device, show_project, show_preview, preview_length]
    ):
        # No options - show current desktop config
        settings = _get_app_settings()
        config = settings.get_notification_config().get("desktop", {})
        if not config:
            console.print("[yellow]Desktop notifications not configured[/yellow]")
            console.print("\n[dim]Enable with: amplifier notify desktop --enable[/dim]")
        else:
            console.print("[bold]Desktop notification settings:[/bold]")
            for key, value in config.items():
                console.print(f"  {key}: {value}")
        return

    # Determine scope
    config_manager = create_config_manager()
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="global",
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        return

    # Build config from provided options
    settings = _get_app_settings()

    # Get existing config at this scope to merge with
    existing = settings.get_notification_config().get("desktop", {})
    new_config = dict(existing)

    if enable is not None:
        new_config["enabled"] = enable
    if show_device is not None:
        new_config["show_device"] = show_device
    if show_project is not None:
        new_config["show_project"] = show_project
    if show_preview is not None:
        new_config["show_preview"] = show_preview
    if preview_length is not None:
        new_config["preview_length"] = preview_length

    # Save
    settings.set_notification_config("desktop", new_config, cast(ScopeType, scope))

    # Report what was done
    if enable is True:
        console.print("[green]✓ Desktop notifications enabled[/green]")
    elif enable is False:
        console.print("[yellow]✓ Desktop notifications disabled[/yellow]")
    else:
        console.print("[green]✓ Desktop notification settings updated[/green]")

    console.print(f"  Scope: {scope}")


@notify.command("ntfy")
@click.option(
    "--enable/--disable", default=None, help="Enable or disable ntfy push notifications"
)
@click.option("--topic", type=str, help="ntfy topic name (treat like a password!)")
@click.option("--server", type=str, help="ntfy server URL (default: https://ntfy.sh)")
@click.option("--local", "scope_flag", flag_value="local", help="Apply to local scope")
@click.option(
    "--project", "scope_flag", flag_value="project", help="Apply to project scope"
)
@click.option(
    "--global",
    "scope_flag",
    flag_value="global",
    help="Apply to global scope (default)",
)
def ntfy_cmd(
    enable: bool | None,
    topic: str | None,
    server: str | None,
    scope_flag: str | None,
):
    """Configure ntfy.sh push notifications.

    Push notifications via ntfy.sh for mobile devices. Install the ntfy app
    on iOS/Android and subscribe to your topic.

    IMPORTANT: Topics are public! Use a unique, hard-to-guess topic name.

    Examples:
        amplifier notify ntfy --enable --topic my-secret-topic
        amplifier notify ntfy --disable
        amplifier notify ntfy --server https://my-ntfy-server.com --global
    """
    # Check if any option was provided
    if all(v is None for v in [enable, topic, server]):
        # No options - show current ntfy config
        settings = _get_app_settings()
        config = settings.get_notification_config().get("ntfy", {})
        if not config:
            console.print("[yellow]ntfy notifications not configured[/yellow]")
            console.print(
                "\n[dim]Enable with: amplifier notify ntfy --enable --topic <your-topic>[/dim]"
            )
        else:
            console.print("[bold]ntfy notification settings:[/bold]")
            for key, value in config.items():
                # Mask topic partially for security
                if key == "topic" and value:
                    masked = value[:3] + "***" if len(value) > 3 else "***"
                    console.print(f"  {key}: {masked}")
                else:
                    console.print(f"  {key}: {value}")
        return

    # Validate: enabling requires a topic
    if enable is True and not topic:
        settings = _get_app_settings()
        existing_topic = settings.get_notification_config().get("ntfy", {}).get("topic")
        if not existing_topic:
            console.print("[red]Error:[/red] --topic is required when enabling ntfy")
            console.print(
                "\n[dim]Example: amplifier notify ntfy --enable --topic my-topic[/dim]"
            )
            return

    # Determine scope
    config_manager = create_config_manager()
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="global",
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        return

    # Build config from provided options
    settings = _get_app_settings()

    # Get existing config to merge with
    existing = settings.get_notification_config().get("ntfy", {})
    new_config = dict(existing)

    if enable is not None:
        new_config["enabled"] = enable
    if topic is not None:
        new_config["topic"] = topic
    if server is not None:
        new_config["server"] = server

    # Save
    settings.set_notification_config("ntfy", new_config, cast(ScopeType, scope))

    # Report what was done
    if enable is True:
        console.print("[green]✓ ntfy push notifications enabled[/green]")
        if topic:
            console.print(f"  Topic: {topic[:3]}*** (masked for security)")
    elif enable is False:
        console.print("[yellow]✓ ntfy push notifications disabled[/yellow]")
    else:
        console.print("[green]✓ ntfy notification settings updated[/green]")

    console.print(f"  Scope: {scope}")


@notify.command("reset")
@click.option(
    "--desktop", "reset_type", flag_value="desktop", help="Reset only desktop settings"
)
@click.option(
    "--ntfy", "reset_type", flag_value="ntfy", help="Reset only ntfy settings"
)
@click.option(
    "--all", "reset_type", flag_value="all", help="Reset all notification settings"
)
@click.option("--local", "scope_flag", flag_value="local", help="Reset at local scope")
@click.option(
    "--project", "scope_flag", flag_value="project", help="Reset at project scope"
)
@click.option(
    "--global",
    "scope_flag",
    flag_value="global",
    help="Reset at global scope (default)",
)
def reset_cmd(reset_type: str | None, scope_flag: str | None):
    """Reset notification settings.

    Removes notification configuration at the specified scope.

    Examples:
        amplifier notify reset --all
        amplifier notify reset --desktop --local
        amplifier notify reset --ntfy --global
    """
    if not reset_type:
        console.print(
            "[red]Error:[/red] Specify what to reset: --desktop, --ntfy, or --all"
        )
        return

    # Determine scope
    config_manager = create_config_manager()
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="global",
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        return

    settings = _get_app_settings()

    if reset_type == "all":
        settings.clear_notification_config(None, cast(ScopeType, scope))
        console.print("[green]✓ All notification settings cleared[/green]")
    else:
        settings.clear_notification_config(reset_type, cast(ScopeType, scope))
        console.print(f"[green]✓ {reset_type} notification settings cleared[/green]")

    console.print(f"  Scope: {scope}")


__all__ = ["notify"]
