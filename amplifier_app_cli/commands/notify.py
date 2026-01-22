"""Notification configuration commands.

Manages desktop and push notification settings for Amplifier sessions.

SECURITY NOTE:
    ntfy.sh topics are PUBLIC - anyone who knows your topic can read your
    notifications. Topics are stored securely in ~/.amplifier/keys.env,
    NOT in settings.yaml.
"""

from typing import cast

import click
from rich.console import Console
from rich.table import Table

from ..key_manager import KeyManager
from ..lib.settings import AppSettings
from ..lib.settings import ScopeType
from ..paths import create_config_manager
from ..paths import get_effective_scope
from ..paths import ScopeNotAvailableError

console = Console()

# Environment variable name for ntfy topic (Amplifier-specific to avoid collisions)
NTFY_TOPIC_ENV_VAR = "AMPLIFIER_NTFY_TOPIC"


def _get_app_settings() -> AppSettings:
    """Get AppSettings instance."""
    return AppSettings()


def _has_ntfy_topic() -> bool:
    """Check if ntfy topic is configured (in env or keys.env)."""
    key_manager = KeyManager()
    return key_manager.has_key(NTFY_TOPIC_ENV_VAR)


@click.group(name="notify")
def notify():
    """Configure notification settings.

    Manage desktop (terminal) and push (ntfy.sh) notifications
    that alert you when the assistant is ready for input.

    SECURITY: ntfy topics are stored in ~/.amplifier/keys.env (not settings.yaml)
    because anyone who knows your topic can read your notifications.

    Examples:
        amplifier notify status
        amplifier notify desktop --enable
        amplifier notify ntfy --enable
    """
    pass


@notify.command("status")
def status():
    """Show current notification settings.

    Displays merged configuration from all scopes (local > project > global).
    Note: ntfy topic is never displayed for security reasons.
    """
    settings = _get_app_settings()
    config = settings.get_notification_config()
    has_topic = _has_ntfy_topic()

    if not config and not has_topic:
        console.print("[yellow]No notifications configured[/yellow]")
        console.print("\n[dim]Enable with:[/dim]")
        console.print("  amplifier notify desktop --enable")
        console.print("  amplifier notify ntfy --enable")
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

    # ntfy settings - NEVER show topic
    ntfy = config.get("ntfy", {})
    ntfy_enabled = ntfy.get("enabled", False)
    if ntfy or has_topic:
        table.add_row("ntfy", "enabled", str(ntfy_enabled))
        # Show topic status but never the actual value
        table.add_row(
            "",
            "topic",
            "[green]configured[/green]" if has_topic else "[red]not set[/red]",
        )
        if "server" in ntfy:
            table.add_row("", "server", str(ntfy["server"]))

    console.print(table)

    if ntfy_enabled and not has_topic:
        console.print(
            "\n[yellow]Warning:[/yellow] ntfy is enabled but no topic is configured."
        )
        console.print("[dim]Run: amplifier notify ntfy --enable[/dim]")


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
    server: str | None,
    scope_flag: str | None,
):
    """Configure ntfy.sh push notifications.

    Push notifications via ntfy.sh for mobile devices. Install the ntfy app
    on iOS/Android and subscribe to your topic.

    SECURITY WARNING:
        ntfy.sh topics are PUBLIC - anyone who knows your topic can:
        - See all your notifications
        - Send messages to your topic

        For this reason, the topic is stored securely in ~/.amplifier/keys.env,
        NOT in settings.yaml. You will be prompted to enter it securely.

    Examples:
        amplifier notify ntfy --enable           # Prompts for topic securely
        amplifier notify ntfy --disable
        amplifier notify ntfy --server https://my-ntfy-server.com
    """
    has_topic = _has_ntfy_topic()

    # Check if any option was provided
    if all(v is None for v in [enable, server]):
        # No options - show current ntfy config
        settings = _get_app_settings()
        config = settings.get_notification_config().get("ntfy", {})
        console.print("[bold]ntfy notification settings:[/bold]")
        console.print(f"  enabled: {config.get('enabled', False)}")
        console.print(
            f"  topic: {'[green]configured[/green]' if has_topic else '[red]not set[/red]'}"
        )
        if "server" in config:
            console.print(f"  server: {config['server']}")
        if not has_topic:
            console.print("\n[dim]Configure with: amplifier notify ntfy --enable[/dim]")
        return

    # Determine scope for settings.yaml options (not topic)
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
    key_manager = KeyManager()

    # If enabling and no topic configured, prompt for it
    if enable is True and not has_topic:
        console.print("\n[bold yellow]⚠ Security Notice[/bold yellow]")
        console.print("ntfy.sh topics are PUBLIC. Anyone who knows your topic can:")
        console.print("  • See all your notifications")
        console.print("  • Send messages to your topic")
        console.print("\nUse a unique, hard-to-guess topic name.")
        console.print("Example: amplifier-myname-x7k9m2\n")

        # Secure prompt (input not echoed)
        topic = click.prompt(
            "Enter ntfy topic",
            hide_input=True,
            confirmation_prompt="Confirm topic",
        )

        if not topic:
            console.print("[red]Error:[/red] Topic cannot be empty")
            return

        # Save topic securely to keys.env
        key_manager.save_key(NTFY_TOPIC_ENV_VAR, topic)
        console.print("[green]✓ Topic saved to ~/.amplifier/keys.env[/green]")

    # Build config for settings.yaml (non-secret options only)
    existing = settings.get_notification_config().get("ntfy", {})
    new_config = dict(existing)

    # Remove topic from config if it somehow exists (security cleanup)
    new_config.pop("topic", None)

    if enable is not None:
        new_config["enabled"] = enable
    if server is not None:
        new_config["server"] = server

    # Save to settings.yaml
    settings.set_notification_config("ntfy", new_config, cast(ScopeType, scope))

    # Report what was done
    if enable is True:
        console.print("[green]✓ ntfy push notifications enabled[/green]")
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
    Note: This does NOT remove the ntfy topic from keys.env.
    To remove the topic, manually edit ~/.amplifier/keys.env.

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

    if reset_type in ("ntfy", "all"):
        console.print("\n[dim]Note: ntfy topic in keys.env was NOT removed.[/dim]")
        console.print("[dim]To remove it, edit ~/.amplifier/keys.env[/dim]")


__all__ = ["notify"]
