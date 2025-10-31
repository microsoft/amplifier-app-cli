"""
File Operations Utilities

Safe, consistent file handling with retry logic for cloud-synced and network drives.
Provides robust operations that handle transient I/O errors gracefully.
"""

import contextlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def discover_files(base_path: Path | str, pattern: str = "**/*.md", max_items: int | None = None) -> list[Path]:
    """Discover files recursively with pattern.

    Structural utility for file discovery - does NOT involve LLM.
    Use with AmplifierSession for AI operations on discovered files.

    Always uses recursive patterns (with **) to ensure complete discovery.
    This prevents common mistakes with non-recursive patterns.

    Args:
        base_path: Directory to search (or single file). Accepts str or Path.
        pattern: Glob pattern (should use ** for recursion)
        max_items: Optional limit on number of files

    Returns:
        List of matching file paths, sorted for consistency

    Example:
        >>> # Structural: discover files (accepts str or Path)
        >>> files = discover_files("docs", "**/*.md")
        >>> files = discover_files(Path("docs"), "**/*.md")
        >>> print(f"Found {len(files)} markdown files")
        >>>
        >>> # AI operations: use AmplifierSession directly with defined config
        >>> from amplifier_core import AmplifierSession
        >>>
        >>> config = {
        ...     "session": {"orchestrator": "loop-basic"},
        ...     "providers": [{
        ...         "module": "provider-anthropic",
        ...         "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        ...         "config": {"model": "claude-sonnet-4-5"}
        ...     }]
        ... }
        >>>
        >>> async with AmplifierSession(config=config) as session:
        ...     for file in files:
        ...         response = await session.execute(f"Analyze: {file.read_text()}")
    """
    base_path = Path(base_path) if isinstance(base_path, str) else base_path

    if base_path.is_file():
        return [base_path]

    if not base_path.is_dir():
        raise ValueError(f"Path is neither file nor directory: {base_path}")

    # Ensure pattern is recursive
    if not pattern.startswith("**"):
        logger.warning(f"Pattern '{pattern}' is not recursive. Consider using '**/{pattern}'")

    files = list(base_path.glob(pattern))

    # Sort for consistent ordering across runs
    files = sorted(files)

    if max_items:
        files = files[:max_items]

    return files


def write_json(
    data: dict | list,
    output_path: Path,
    ensure_ascii: bool = False,
    indent: int = 2,
    max_retries: int = 3,
) -> None:
    """Write JSON with error handling and retry logic.

    Structural utility for JSON persistence - does NOT involve LLM.
    Use for saving results after AmplifierSession processing.

    Handles cloud-synced folders (OneDrive, Dropbox) and network drives
    that may have transient I/O errors. Uses atomic writes to prevent
    partial file corruption.

    Args:
        data: Data to serialize to JSON
        output_path: Target file path
        ensure_ascii: Whether to escape non-ASCII characters
        indent: JSON indentation (None for compact)
        max_retries: Maximum retry attempts for I/O errors

    Raises:
        OSError: If write fails after all retries
        TypeError: If data is not JSON serializable
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use temporary file for atomic write
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    retry_delay = 0.5

    for attempt in range(max_retries):
        try:
            # Write to temp file
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
                f.flush()  # Ensure data is written to disk

            # Atomic rename (on POSIX systems)
            temp_path.replace(output_path)
            return

        except OSError as e:
            if e.errno == 5 and attempt < max_retries - 1:  # I/O error
                if attempt == 0:  # Log warning on first retry
                    logger.warning(
                        f"File I/O error writing to {output_path} - retrying. "
                        "This may be due to cloud-synced files (OneDrive, Dropbox, etc.). "
                        "Consider enabling 'Always keep on this device' for better performance."
                    )
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                # Clean up temp file if it exists
                if temp_path.exists():
                    with contextlib.suppress(OSError):
                        temp_path.unlink()
                raise

        except Exception:
            # Clean up temp file on any other error
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()
            raise

    # If we get here without exception, write succeeded
    return


def read_json(path: Path, max_retries: int = 3) -> dict | list:
    """Read and parse JSON file with retry logic.

    Structural utility for JSON loading - does NOT involve LLM.
    Use for loading data/config before AmplifierSession processing.

    Handles cloud-synced folders and network drives that may
    have transient I/O errors when files aren't immediately available.

    Args:
        path: Path to JSON file
        max_retries: Maximum retry attempts for I/O errors

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file contains invalid JSON
        OSError: If read fails after all retries
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")

    retry_delay = 0.5

    for attempt in range(max_retries):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)

        except OSError as e:
            if e.errno == 5 and attempt < max_retries - 1:  # I/O error
                if attempt == 0:
                    logger.warning(
                        f"File I/O error reading {path} - retrying. File may be cloud-synced and not locally available."
                    )
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise

        except json.JSONDecodeError as e:
            # Don't retry JSON decode errors
            raise ValueError(f"Invalid JSON in {path}: {e}")

    # Unreachable but required by linter
    raise RuntimeError(f"Failed to read {path} after {max_retries} attempts")


def validate_path_exists(path: Path, path_type: str = "path") -> Path:
    """Validate that a path exists with clear error message.

    Args:
        path: Path to validate
        path_type: Description for error message (e.g., "input file", "config")

    Returns:
        The validated Path object

    Raises:
        ValueError: If path doesn't exist
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"{path_type.capitalize()} does not exist: {path}")
    return path


def safe_read_text(path: Path, encoding: str = "utf-8", max_retries: int = 3) -> str:
    """Read text file with retry logic for cloud-synced files.

    Args:
        path: Path to text file
        encoding: File encoding
        max_retries: Maximum retry attempts

    Returns:
        File contents as string

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If read fails after all retries
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")

    retry_delay = 0.5

    for attempt in range(max_retries):
        try:
            return path.read_text(encoding=encoding)

        except OSError as e:
            if e.errno == 5 and attempt < max_retries - 1:
                if attempt == 0:
                    logger.warning(f"File I/O error reading {path} - retrying")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise

    # Unreachable but required by linter
    raise RuntimeError(f"Failed to read {path} after {max_retries} attempts")


def safe_write_text(content: str, path: Path, encoding: str = "utf-8", max_retries: int = 3) -> None:
    """Write text file with retry logic for cloud-synced files.

    Args:
        content: Text content to write
        path: Target file path
        encoding: File encoding
        max_retries: Maximum retry attempts

    Raises:
        OSError: If write fails after all retries
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    retry_delay = 0.5

    for attempt in range(max_retries):
        try:
            path.write_text(content, encoding=encoding)
            return

        except OSError as e:
            if e.errno == 5 and attempt < max_retries - 1:
                if attempt == 0:
                    logger.warning(f"File I/O error writing to {path} - retrying")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise

    # Unreachable but required by linter
    raise RuntimeError(f"Failed to write to {path} after {max_retries} attempts")


def append_jsonl(record: dict, path: Path, max_retries: int = 3) -> None:
    """Append a record to a JSONL (JSON Lines) file.

    Structural utility for incremental logging - does NOT involve LLM.
    Use for saving processing checkpoints during AmplifierSession work.

    Each record is written as a single line of JSON.
    Useful for streaming logs or incremental results.

    Args:
        record: Dictionary to append
        path: Path to JSONL file
        max_retries: Maximum retry attempts

    Raises:
        OSError: If write fails after all retries
        TypeError: If record is not JSON serializable
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    retry_delay = 0.5
    json_line = json.dumps(record, ensure_ascii=False) + "\n"

    for attempt in range(max_retries):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json_line)
                f.flush()
            return

        except OSError as e:
            if e.errno == 5 and attempt < max_retries - 1:
                if attempt == 0:
                    logger.warning(f"File I/O error appending to {path} - retrying")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise

    # Unreachable but required by linter
    raise RuntimeError(f"Failed to append to {path} after {max_retries} attempts")
