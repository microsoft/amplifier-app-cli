"""Keep the settings server's view of keys and environment current, without restarts.

The page answers "what will Amplifier see when it runs from here?". Amplifier
only reads its process environment, filled in by KeyManager from keys.env for
names the environment doesn't already have. So:

  * the environment this server was launched with is the truth. A name in it
    (even exported as "") keeps its launch value;
  * keys.env is re-read on every refresh (the page polls), so a key saved
    elsewhere needs no restart; a name that leaves keys.env and was not in the
    launch environment is removed.

KeyManager has already copied keys.env into os.environ by the time the server
starts, so a launch value equal to keys.env's is taken to have come from it.

The user's login shell (`$SHELL -ilc 'env -0'`) is read only when the page
asks (POST /api/env/reread). It explains why a new terminal would see something
different, and where its startup files set a name; it never changes os.environ.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .settings_web_system import read_keys_file

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHELL_TIMEOUT_S = 12.0


def shell_env_enabled() -> bool:
    """Sandboxes turn this off so a developer's real shell keys never leak in."""
    return os.environ.get("AMPLIFIER_SETTINGS_SHELL_ENV", "").lower() not in ("0", "off", "false", "no")


def _run_shell(args: list[str], extra: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    """Run the user's login shell with a minimal, quiet environment."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "SHELL", "LANG") if k in os.environ}
    env |= {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb", "DISABLE_AUTO_UPDATE": "true", **extra}
    return subprocess.run(  # noqa: S603 -- the user's own login shell, as a new terminal runs it
        [shell, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=SHELL_TIMEOUT_S,
        env=env,
        cwd=str(Path.home()),
        check=False,
    )


def login_shell_env() -> dict[str, str]:
    """Environment of a fresh login shell, as a new terminal tab would have it."""
    chunks = _run_shell(["-ilc", "env -0"], {"AMPLIFIER_SETTINGS_ENV_PROBE": "1"}).stdout.decode(errors="replace").split("\0")
    # anything a startup file printed lands in front of the first variable: drop it
    starts = [m.start() for m in re.finditer(r"(?:^|(?<=\n))[A-Za-z_][A-Za-z0-9_]*=", chunks[0])]
    chunks[0] = chunks[0][starts[-1] :] if starts else ""
    env = {name: value for name, sep, value in (c.partition("=") for c in chunks) if sep and NAME.match(name)}
    if "HOME" not in env:
        raise RuntimeError("login shell produced no environment")
    return env


def where_set(name: str) -> list[str]:
    """Startup files (file:line) where a new login shell sets `name`. Values are never returned."""
    if not NAME.match(name) or not shell_env_enabled():
        return []
    ps4 = "+%x:%I> " if (os.environ.get("SHELL") or "/bin/zsh").endswith("zsh") else "+${BASH_SOURCE}:${LINENO}> "
    try:
        trace = _run_shell(["-ilxc", "exit"], {"PS4": ps4}).stderr.decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    home, found = str(Path.home()), []
    for m in re.finditer(r"^\++([^:>\n]+):(\d+)> .*\b" + re.escape(name) + "=", trace, re.MULTILINE):
        where = f"{m.group(1).replace(home, '~', 1)} line {m.group(2)}"
        if where not in found:
            found.append(where)
    return found


class LiveEnv:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        stored = read_keys_file()
        self._launch = {k: v for k, v in os.environ.items() if stored.get(k) != v}
        self._names: set[str] = set()  # every name refresh() has managed
        self._shell: dict[str, str] | None = None
        self.reread_at: float | None = None
        self.differences: list[dict[str, Any]] = []
        self.fingerprint = ""

    def refresh(self, names: set[str]) -> bool:
        """Bring os.environ up to date for `names` and keys.env; return True if anything changed."""
        with self._lock:
            stored = read_keys_file()
            self._names |= names | set(stored)
            for name in self._names:
                if name in self._launch:
                    os.environ[name] = self._launch[name]
                elif name in stored:
                    os.environ[name] = stored[name]
                else:
                    os.environ.pop(name, None)
            digest = hashlib.sha256("\0".join(f"{n}={os.environ.get(n)}" for n in sorted(self._names)).encode()).hexdigest()
            changed, self.fingerprint = digest != self.fingerprint, digest
            return changed

    def reread(self, names: set[str]) -> dict[str, Any]:
        """Where a new login shell would give `names` different values than this server has.

        Only set/unset is reported, never values.
        """
        if not shell_env_enabled():
            return {"differences": [], "error": "Reading your login shell is turned off here (AMPLIFIER_SETTINGS_SHELL_ENV=off)."}
        try:
            shell = login_shell_env()
        except (OSError, subprocess.SubprocessError, RuntimeError) as e:
            return {"differences": [], "error": f"Couldn't read your login shell's environment: {e}"}
        stored = read_keys_file()
        diffs = [
            {
                "name": n,
                "launch": "set" if n in self._launch else "unset",
                "shell": "set" if n in shell else "unset",
                "where": where_set(n) if n in shell else [],
            }
            for n in sorted(names)
            # compare what Amplifier would end up with: the environment's value, else keys.env's
            if self._launch.get(n, stored.get(n)) != shell.get(n, stored.get(n))
        ]
        with self._lock:
            self._shell, self.differences, self.reread_at = shell, diffs, time.time()
        return {"differences": diffs, "error": None}

    def shell_value(self, name: str) -> str | None:
        """What a new terminal sets `name` to; None if it doesn't, or no re-read happened."""
        return (self._shell or {}).get(name)

    def info(self) -> dict[str, Any]:
        return {"source": "launch", "reread_at": self.reread_at, "differences": self.differences}
