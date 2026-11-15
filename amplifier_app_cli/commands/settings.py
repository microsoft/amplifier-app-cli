"""`amplifier settings` -- edit settings in a local web page."""

from __future__ import annotations

import os
import signal
import sys
import webbrowser

import click

from ..console import console


def _stop(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


@click.command("settings")
@click.option(
    "--port", default=0, show_default="random", help="Port on 127.0.0.1 to serve on."
)
@click.option(
    "--no-browser", is_flag=True, help="Print the link instead of opening a browser."
)
@click.option("--verbose", is_flag=True, help="Log each request.")
def settings(port: int, no_browser: bool, verbose: bool) -> None:
    """Open your Amplifier settings in a browser.

    Edits the same files as `amplifier provider`, `amplifier bundle` and
    `amplifier routing`: ~/.amplifier/settings.yaml (global) and, when run
    inside a project, .amplifier/settings.yaml (project) and
    .amplifier/settings.local.yaml (local). The page is served on 127.0.0.1
    only and stops when you press Ctrl+C. The printed link works once: open
    it again and the page asks you to run `amplifier settings` again.
    """
    from ..settings_web import SettingsServer
    from .provider import ensure_providers_ready

    # Same repair every provider command runs first: after `amplifier update`
    # the provider modules are gone, and without them nothing can be tested.
    console.print("[dim]Checking provider modules...[/dim]")
    try:
        ensure_providers_ready()
    except Exception as e:  # noqa: BLE001 -- the page still works; tests will say why
        console.print(f"[yellow]Couldn't check provider modules: {e}[/yellow]")

    server = SettingsServer(port=port, verbose=verbose)
    url = server.launch_url
    console.print("[bold]Amplifier settings[/bold] is running at:")
    console.print(f"  {url}", soft_wrap=True, highlight=False)
    console.print("[dim]Press Ctrl+C to stop.[/dim]")
    if not no_browser:
        webbrowser.open(url)
    # Ctrl+C must always stop the page, whatever handler something else installed;
    # SIGTERM (e.g. `kill`) stops it the same way.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nSettings page stopped.")
    finally:
        server.server_close()
    # A request thread may still be waiting on a provider that isn't answering;
    # exiting normally would join it. Nothing else is left to clean up.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
