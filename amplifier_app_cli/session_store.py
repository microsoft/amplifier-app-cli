"""
Session persistence management for Amplifier.

Manages session state persistence to filesystem with atomic writes,
backup mechanism, and corruption recovery.
"""

import contextlib
import json
import logging
import shutil
import tempfile
from datetime import UTC
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionStore:
    """
    Manages session persistence to filesystem.

    Contract:
    - Inputs: session_id (str), transcript (list), metadata (dict)
    - Outputs: Saved files or loaded data tuples
    - Side Effects: Filesystem writes to ~/.amplifier/sessions/<session-id>/
    - Errors: FileNotFoundError for missing sessions, IOError for disk issues
    - Files created: transcript.jsonl, metadata.json, profile.toml
    """

    def __init__(self, base_dir: Path | None = None):
        """Initialize with base directory for sessions.

        Args:
            base_dir: Base directory for session storage. Defaults to ~/.amplifier/sessions
        """
        if base_dir is None:
            base_dir = Path.home() / ".amplifier" / "sessions"
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, transcript: list, metadata: dict) -> None:
        """Save session state atomically with backup.

        Args:
            session_id: Unique session identifier
            transcript: List of message objects for the session
            metadata: Session metadata dictionary

        Raises:
            ValueError: If session_id is empty or invalid
            IOError: If unable to write files after retries
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty")

        # Sanitize session_id to prevent path traversal
        if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(f"Invalid session_id: {session_id}")

        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Save transcript with atomic write
        self._save_transcript(session_dir, transcript)

        # Save metadata with atomic write
        self._save_metadata(session_dir, metadata)

        logger.debug(f"Session {session_id} saved successfully")

    def _save_transcript(self, session_dir: Path, transcript: list) -> None:
        """Save transcript with atomic write and backup.

        Args:
            session_dir: Directory for this session
            transcript: List of message objects
        """
        transcript_file = session_dir / "transcript.jsonl"
        backup_file = session_dir / "transcript.jsonl.backup"

        # Create backup if file exists
        if transcript_file.exists():
            try:
                shutil.copy2(transcript_file, backup_file)
            except Exception as e:
                logger.warning(f"Failed to create backup: {e}")

        # Write to temp file first (atomic write pattern)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=session_dir, prefix="transcript_", suffix=".tmp", delete=False
        ) as tmp_file:
            temp_path = Path(tmp_file.name)
            try:
                for message in transcript:
                    json.dump(message, tmp_file, ensure_ascii=False)
                    tmp_file.write("\n")
                tmp_file.flush()

                # Atomic rename
                temp_path.replace(transcript_file)

            except Exception as e:
                # Clean up temp file on failure
                with contextlib.suppress(Exception):
                    temp_path.unlink()
                raise OSError(f"Failed to save transcript: {e}") from e

    def _save_metadata(self, session_dir: Path, metadata: dict) -> None:
        """Save metadata with atomic write and backup.

        Args:
            session_dir: Directory for this session
            metadata: Metadata dictionary
        """
        metadata_file = session_dir / "metadata.json"
        backup_file = session_dir / "metadata.json.backup"

        # Create backup if file exists
        if metadata_file.exists():
            try:
                shutil.copy2(metadata_file, backup_file)
            except Exception as e:
                logger.warning(f"Failed to create backup: {e}")

        # Write to temp file first (atomic write pattern)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=session_dir, prefix="metadata_", suffix=".tmp", delete=False
        ) as tmp_file:
            temp_path = Path(tmp_file.name)
            try:
                json.dump(metadata, tmp_file, indent=2, ensure_ascii=False)
                tmp_file.flush()

                # Atomic rename
                temp_path.replace(metadata_file)

            except Exception as e:
                # Clean up temp file on failure
                with contextlib.suppress(Exception):
                    temp_path.unlink()
                raise OSError(f"Failed to save metadata: {e}") from e

    def load(self, session_id: str) -> tuple[list, dict]:
        """Load session state with corruption recovery.

        Args:
            session_id: Session identifier to load

        Returns:
            Tuple of (transcript, metadata)

        Raises:
            FileNotFoundError: If session does not exist
            ValueError: If session_id is invalid
            IOError: If unable to read files after recovery attempts
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty")

        # Sanitize session_id
        if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(f"Invalid session_id: {session_id}")

        session_dir = self.base_dir / session_id
        if not session_dir.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found")

        # Load transcript with recovery
        transcript = self._load_transcript(session_dir)

        # Load metadata with recovery
        metadata = self._load_metadata(session_dir)

        logger.debug(f"Session {session_id} loaded successfully")
        return transcript, metadata

    def _load_transcript(self, session_dir: Path) -> list:
        """Load transcript with corruption recovery.

        Args:
            session_dir: Directory for this session

        Returns:
            List of message objects
        """
        transcript_file = session_dir / "transcript.jsonl"
        backup_file = session_dir / "transcript.jsonl.backup"

        # Try main file first
        if transcript_file.exists():
            try:
                transcript = []
                with open(transcript_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:  # Skip empty lines
                            transcript.append(json.loads(line))
                return transcript
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load transcript, trying backup: {e}")

        # Try backup if main file failed or missing
        if backup_file.exists():
            try:
                transcript = []
                with open(backup_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:  # Skip empty lines
                            transcript.append(json.loads(line))
                logger.info("Loaded transcript from backup")
                return transcript
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"Backup also corrupted: {e}")

        # Return empty transcript if both failed
        logger.warning("Both transcript files corrupted, returning empty transcript")
        return []

    def _load_metadata(self, session_dir: Path) -> dict:
        """Load metadata with corruption recovery.

        Args:
            session_dir: Directory for this session

        Returns:
            Metadata dictionary
        """
        metadata_file = session_dir / "metadata.json"
        backup_file = session_dir / "metadata.json.backup"

        # Try main file first
        if metadata_file.exists():
            try:
                with open(metadata_file, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load metadata, trying backup: {e}")

        # Try backup if main file failed or missing
        if backup_file.exists():
            try:
                with open(backup_file, encoding="utf-8") as f:
                    metadata = json.load(f)
                logger.info("Loaded metadata from backup")
                return metadata
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"Backup also corrupted: {e}")

        # Return minimal metadata if both failed
        logger.warning("Both metadata files corrupted, returning minimal metadata")
        return {
            "session_id": session_dir.name,
            "recovered": True,
            "recovery_time": datetime.now(UTC).isoformat(),
        }

    def exists(self, session_id: str) -> bool:
        """Check if session exists.

        Args:
            session_id: Session identifier to check

        Returns:
            True if session exists, False otherwise
        """
        if not session_id or not session_id.strip():
            return False

        # Sanitize session_id
        if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            return False

        session_dir = self.base_dir / session_id
        return session_dir.exists() and session_dir.is_dir()

    def list_sessions(self) -> list[str]:
        """List all session IDs.

        Returns:
            List of session identifiers, sorted by modification time (newest first)
        """
        if not self.base_dir.exists():
            return []

        sessions = []
        for session_dir in self.base_dir.iterdir():
            if session_dir.is_dir() and not session_dir.name.startswith("."):
                # Include session with its modification time for sorting
                try:
                    mtime = session_dir.stat().st_mtime
                    sessions.append((session_dir.name, mtime))
                except Exception:
                    # If we can't get mtime, include with 0
                    sessions.append((session_dir.name, 0))

        # Sort by modification time (newest first) and return just the names
        sessions.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in sessions]

    def save_profile(self, session_id: str, profile: dict) -> None:
        """Save profile snapshot used for session.

        Args:
            session_id: Session identifier
            profile: Profile configuration dictionary

        Raises:
            ValueError: If session_id is invalid
            IOError: If unable to write profile
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty")

        # Sanitize session_id
        if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(f"Invalid session_id: {session_id}")

        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        profile_file = session_dir / "profile.toml"

        # Convert profile dict to TOML format
        import tomli_w

        # Write to temp file first (atomic write pattern)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=session_dir, prefix="profile_", suffix=".tmp", delete=False
        ) as tmp_file:
            temp_path = Path(tmp_file.name)
            try:
                toml_content = tomli_w.dumps(profile)
                tmp_file.write(toml_content)
                tmp_file.flush()

                # Atomic rename
                temp_path.replace(profile_file)

            except Exception as e:
                # Clean up temp file on failure
                with contextlib.suppress(Exception):
                    temp_path.unlink()
                raise OSError(f"Failed to save profile: {e}") from e

        logger.debug(f"Profile saved for session {session_id}")

    def cleanup_old_sessions(self, days: int = 30) -> int:
        """Remove sessions older than specified days.

        Args:
            days: Number of days to keep sessions (default 30)

        Returns:
            Number of sessions removed
        """
        if days < 0:
            raise ValueError("days must be non-negative")

        if not self.base_dir.exists():
            return 0

        from datetime import timedelta

        cutoff_time = datetime.now(UTC) - timedelta(days=days)
        cutoff_timestamp = cutoff_time.timestamp()

        removed = 0
        for session_dir in self.base_dir.iterdir():
            if not session_dir.is_dir() or session_dir.name.startswith("."):
                continue

            try:
                # Check modification time
                mtime = session_dir.stat().st_mtime
                if mtime < cutoff_timestamp:
                    # Remove old session
                    shutil.rmtree(session_dir)
                    logger.info(f"Removed old session: {session_dir.name}")
                    removed += 1
            except Exception as e:
                logger.error(f"Failed to remove session {session_dir.name}: {e}")

        if removed > 0:
            logger.info(f"Cleaned up {removed} old sessions")

        return removed
