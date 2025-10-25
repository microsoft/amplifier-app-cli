"""
Amplifier Toolkit - Structural Utilities for CLI Tools

Ultra-simple utilities for building amplifier-dev CLI tools.

Philosophy:
- Code handles: loops, file I/O, state, coordination
- Amplifier-core handles: LLM interactions via orchestrator
- Toolkit provides: structural utilities only

What This Provides:
- File operations (discover_files, read_json, write_json)
- Progress reporting (ProgressReporter)
- Input validation (validate_input_path, validate_minimum_files)

What This Does NOT Provide:
- Session wrappers (use AmplifierSession directly)
- State management frameworks (each tool owns its state)
- LLM utilities (amplifier-core handles everything)

See docs/TOOLKIT_GUIDE.md for complete usage patterns.
"""

__version__ = "0.1.0"

# Export key utilities for easy access
from toolkit.utilities.file_ops import discover_files
from toolkit.utilities.file_ops import read_json
from toolkit.utilities.file_ops import validate_path_exists
from toolkit.utilities.file_ops import write_json
from toolkit.utilities.progress import ProgressReporter
from toolkit.utilities.validation import validate_input_path
from toolkit.utilities.validation import validate_minimum_files
from toolkit.utilities.validation import validate_output_path

# Alias for compatibility with template/examples
require_minimum_files = validate_minimum_files

__all__ = [
    # File operations
    "discover_files",
    "read_json",
    "write_json",
    "validate_path_exists",
    # Progress reporting
    "ProgressReporter",
    # Validation
    "validate_input_path",
    "validate_output_path",
    "validate_minimum_files",
    "require_minimum_files",  # Alias for validate_minimum_files
]
