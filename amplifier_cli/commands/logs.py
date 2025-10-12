import time
from pathlib import Path
import click


@click.command("logs")
@click.option("--path", default="./amplifier.log.jsonl", help="Path to JSONL log file")
@click.option("--follow/--no-follow", default=True, help="Tail the log")
@click.option("--filter", "filter_text", default=None, help="Substring to filter lines")
def logs_cmd(path: str, follow: bool, filter_text: str | None):
    """Tail the unified JSONL log."""
    p = Path(path)
    if not p.exists():
        click.echo(f"No log file at {p}")
        return

    with p.open("r", encoding="utf-8") as f:
        if follow:
            # seek to end
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.25)
                    continue
                if filter_text and filter_text not in line:
                    continue
                click.echo(line.rstrip())
        else:
            for line in f:
                if filter_text and filter_text not in line:
                    continue
                click.echo(line.rstrip())
