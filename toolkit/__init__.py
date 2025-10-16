"""
Amplifier-Dev Toolkit

A focused, philosophy-aligned utility package for building development tools
in amplifier-dev. Adopts proven patterns while staying true to amplifier's
kernel philosophy.

Core principles:
- Ruthless simplicity (minimal abstractions)
- Standard patterns (consistent interfaces)
- Fail gracefully (partial results > complete failure)
- Mechanism not policy (provide capabilities, users decide usage)
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
]
