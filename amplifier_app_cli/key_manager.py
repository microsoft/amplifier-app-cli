"""API key management for Amplifier."""

import os
import platform
import tempfile
from pathlib import Path

from filelock import FileLock


class KeyManager:
    """Manage API keys in ~/.amplifier/keys.env file."""

    def __init__(self):
        self.keys_file = Path.home() / ".amplifier" / "keys.env"
        # Advisory lock guarding the read-modify-write critical section in
        # save_key()/has_stored_key() -- two ordinary concurrent CLI
        # invocations (two terminals, or a script racing a human) must not
        # silently clobber each other's keys. See
        # docs/designs/provider-instance-credentials.md §5.5.
        self._lock = FileLock(str(self.keys_file) + ".lock", timeout=10)
        self._load_keys()

    def _load_keys(self):
        """Load keys from file into environment if they exist."""
        if not self.keys_file.exists():
            return

        try:
            with open(self.keys_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()  # Strip whitespace from key name
                        # Only set if not already in environment
                        if key not in os.environ:
                            os.environ[key] = value.strip().strip('"').strip("'")
        except Exception:
            # Fail silently - manual env vars will still work
            pass

    def has_key(self, key_name: str) -> bool:
        """Check if API key exists (in env or file)."""
        return key_name in os.environ

    def has_stored_key(self, key_name: str) -> bool:
        """Check if a key is present in the on-disk keys.env store itself,
        independent of the current process environment.

        Distinguishes a genuine live shell-exported env var (present in
        ``os.environ`` but never written to ``keys.env``) from a stale
        ``keys.env`` entry left over from a previously-removed provider
        instance -- see
        docs/designs/provider-instance-credentials.md §5.4.4.
        """
        return key_name in self.stored_keys()

    def stored_keys(self) -> set[str]:
        """Return the set of env-var names currently present in the
        on-disk ``keys.env`` store, independent of the current process
        environment or any settings.yaml placeholder.

        Read-only; takes the same advisory lock as ``save_key()`` so the
        read observes a consistent (non-partially-written) file rather
        than racing a concurrent writer. Used by
        ``provider_config_utils._claimed_env_vars`` so a name backed by a
        real, already-saved secret counts as claimed even before any
        scope's config references it via a ``${VAR}`` placeholder -- see
        docs/designs/provider-instance-credentials.md §5.4.1.
        """
        if not self.keys_file.exists():
            return set()
        names: set[str] = set()
        with self._lock:
            try:
                with open(self.keys_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _v = line.split("=", 1)
                            names.add(k.strip())
            except Exception:
                return set()
        return names

    def save_key(self, key_name: str, key_value: str) -> None:
        """Save API key to keys.env file securely.

        The full read-modify-write sequence is protected by an advisory
        file lock and the write itself is atomic (tmp-file-then-replace),
        so two ordinary concurrent CLI invocations can't silently clobber
        each other's keys and a crash mid-write can't corrupt the store.
        See docs/designs/provider-instance-credentials.md §5.5.
        """
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # Read existing keys
            existing_keys: dict[str, str] = {}
            if self.keys_file.exists():
                with open(self.keys_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            existing_keys[k.strip()] = v

            # Update with new key
            existing_keys[key_name] = f'"{key_value}"'

            # Write back atomically: tmp-file-then-replace so a reader (or a
            # crash) never observes a partially-written keys.env.
            fd, tmp = tempfile.mkstemp(dir=self.keys_file.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write("# Amplifier API Keys\n")
                    f.write(
                        "# Auto-generated by amplifier init or amplifier provider use\n"
                    )
                    f.write("# These are loaded automatically on startup\n\n")
                    for k, v in existing_keys.items():
                        f.write(f"{k}={v}\n")
                os.replace(tmp, self.keys_file)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

            # Set secure permissions (owner read/write only)
            # Skip on Windows where NTFS already restricts file permissions to the user
            if platform.system() != "Windows":
                self.keys_file.chmod(0o600)

            # Also set in current environment
            os.environ[key_name] = key_value
