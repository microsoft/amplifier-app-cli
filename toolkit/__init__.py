"""Amplifier-Dev Toolkit: Build Sophisticated AI Tools with Metacognitive Recipes.

The toolkit teaches building AI tools using multi-config metacognitive recipes:
- Multiple specialized configs (each optimized for its cognitive role)
- Code orchestration (flow control, state management, decisions)
- Structural utilities (file discovery, progress, validation)

Key principle: Code for structure, specialized AI configs for intelligence.

See docs/TOOLKIT_GUIDE.md for complete guide.

Example: toolkit/examples/tutorial_analyzer/ - Complete pedagogical exemplar.

Modules:
- utilities.file_ops: File discovery, JSON I/O, path validation
- utilities.progress: Progress reporting
- utilities.validation: Input validation

Philosophy:
- Use AmplifierSession directly (don't wrap kernel mechanisms)
- Each tool owns its state (no state frameworks)
- Multi-config pattern for sophisticated tools
- Start simple (Level 1 fixed configs), add complexity only when needed
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
