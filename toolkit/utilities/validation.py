"""
Validation Utilities

Common validation patterns for CLI tools.
Provides clear, consistent error messages for input validation.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_input_path(path: Path, must_exist: bool = True, must_be_dir: bool = False) -> bool:
    """Validate input path with specific requirements.

    Structural utility - input validation before processing.
    Use before creating expensive AmplifierSession.

    Args:
        path: Path to validate
        must_exist: Whether the path must exist
        must_be_dir: Whether the path must be a directory

    Returns:
        True if valid

    Raises:
        ValueError: If validation fails with descriptive message

    Example:
        >>> validate_input_path(Path("docs"), must_be_dir=True)
        True  # If docs/ exists and is a directory
    """
    path = Path(path)

    if must_exist and not path.exists():
        raise ValueError(f"Path does not exist: {path}")

    if must_be_dir and path.exists() and not path.is_dir():
        raise ValueError(f"Path must be a directory: {path}")

    if path.exists() and path.is_dir() and not list(path.iterdir()):
        logger.warning(f"Directory is empty: {path}")

    return True


def validate_output_path(path: Path, allow_overwrite: bool = True) -> bool:
    """Validate output path can be created or overwritten.

    Structural utility - pre-processing validation.
    Use before creating expensive AmplifierSession.

    Args:
        path: Output path to validate
        allow_overwrite: Whether existing files can be overwritten

    Returns:
        True if valid

    Raises:
        ValueError: If validation fails

    Example:
        >>> validate_output_path(Path("results.json"))
        True  # If parent directory exists
    """
    path = Path(path)

    # Check if path is a directory
    if path.exists() and path.is_dir():
        raise ValueError(f"Output path is a directory: {path}")

    # Check if parent directory exists
    if not path.parent.exists():
        raise ValueError(f"Output directory does not exist: {path.parent}")

    # Check overwrite permission
    if path.exists() and not allow_overwrite:
        raise ValueError(f"Output file already exists and overwrite not allowed: {path}")

    # Warn about overwrite
    if path.exists() and allow_overwrite:
        logger.warning(f"Output file will be overwritten: {path}")

    return True


def validate_minimum_files(files: list, minimum: int, file_type: str = "files") -> bool:
    """Validate minimum number of files found.

    Structural utility - input validation before processing.
    Use before creating expensive AmplifierSession.

    Args:
        files: List of files found
        minimum: Minimum required
        file_type: Description for error message (e.g., "markdown files", "profiles")

    Returns:
        True if enough files

    Raises:
        ValueError: If too few files

    Example:
        >>> files = list(Path("docs").glob("**/*.md"))
        >>> validate_minimum_files(files, minimum=2, file_type="markdown files")
        True  # If 2+ markdown files found
    """
    if len(files) < minimum:
        raise ValueError(f"Need at least {minimum} {file_type}, found {len(files)}")

    return True


def validate_pattern(pattern: str) -> bool:
    """Validate glob pattern is properly formatted.

    Args:
        pattern: Glob pattern to validate

    Returns:
        True if valid

    Raises:
        ValueError: If pattern is invalid

    Example:
        >>> validate_pattern("**/*.md")
        True
        >>> validate_pattern("*.md")  # Logs warning
        True
    """
    if not pattern:
        raise ValueError("Pattern cannot be empty")

    # Warn about non-recursive patterns
    if not pattern.startswith("**"):
        logger.warning(f"Pattern '{pattern}' is not recursive. Consider using '**/{pattern}' to search subdirectories")

    # Check for common mistakes
    if pattern.count("*") > 4:
        logger.warning(f"Pattern may be overly complex: {pattern}")

    if "/" in pattern and not pattern.startswith("**/"):
        logger.warning(f"Pattern includes path but isn't recursive: {pattern}")

    return True


def validate_file_extension(path: Path, allowed_extensions: list[str]) -> bool:
    """Validate file has an allowed extension.

    Args:
        path: File path to check
        allowed_extensions: List of allowed extensions (e.g., [".json", ".yaml"])

    Returns:
        True if valid

    Raises:
        ValueError: If extension not allowed

    Example:
        >>> validate_file_extension(Path("data.json"), [".json", ".jsonl"])
        True
    """
    if not allowed_extensions:
        return True  # No restriction

    extension = path.suffix.lower()
    if extension not in allowed_extensions:
        raise ValueError(f"File extension '{extension}' not allowed. Expected one of: {', '.join(allowed_extensions)}")

    return True


def validate_json_structure(data: dict, required_fields: list[str]) -> bool:
    """Validate JSON/dict has required fields.

    Args:
        data: Dictionary to validate
        required_fields: List of required field names

    Returns:
        True if valid

    Raises:
        ValueError: If missing required fields

    Example:
        >>> data = {"name": "test", "version": "1.0"}
        >>> validate_json_structure(data, ["name", "version"])
        True
    """
    missing = [field for field in required_fields if field not in data]

    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    return True


def validate_range(
    value: int | float, min_value: int | float | None = None, max_value: int | float | None = None, name: str = "value"
) -> bool:
    """Validate a numeric value is within range.

    Args:
        value: Value to check
        min_value: Minimum allowed value (inclusive)
        max_value: Maximum allowed value (inclusive)
        name: Name for error messages

    Returns:
        True if valid

    Raises:
        ValueError: If out of range

    Example:
        >>> validate_range(5, min_value=1, max_value=10, name="batch_size")
        True
    """
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be at least {min_value}, got {value}")

    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be at most {max_value}, got {value}")

    return True


def validate_not_empty(value: str | list | dict, name: str = "value") -> bool:
    """Validate that a value is not empty.

    Args:
        value: Value to check (string, list, or dict)
        name: Name for error messages

    Returns:
        True if not empty

    Raises:
        ValueError: If empty

    Example:
        >>> validate_not_empty("hello", "input_text")
        True
        >>> validate_not_empty([], "results")
        ValueError: results cannot be empty
    """
    if not value:
        raise ValueError(f"{name} cannot be empty")

    return True
