"""Test configuration - stub missing optional modules.

The amplifier-session-storage[hybrid] and [cosmos] extras are only
available from local development builds, not from the git source.
Stub them so the test suite can import the CLI modules.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

# Stub amplifier_session_storage.hybrid before any CLI imports
_hybrid = ModuleType("amplifier_session_storage.hybrid")
_hybrid_file = ModuleType("amplifier_session_storage.hybrid.file_storage")
_hybrid_file.HybridFileStorage = MagicMock  # type: ignore[attr-defined]
_hybrid_file.HybridFileStorageConfig = MagicMock  # type: ignore[attr-defined]
sys.modules["amplifier_session_storage.hybrid"] = _hybrid
sys.modules["amplifier_session_storage.hybrid.file_storage"] = _hybrid_file

# Stub amplifier_session_storage.cosmos.file_storage
_cosmos = sys.modules.get("amplifier_session_storage.cosmos")
if _cosmos is None:
    _cosmos = ModuleType("amplifier_session_storage.cosmos")
    sys.modules["amplifier_session_storage.cosmos"] = _cosmos
_cosmos_file = ModuleType("amplifier_session_storage.cosmos.file_storage")
_cosmos_file.CosmosFileConfig = MagicMock  # type: ignore[attr-defined]
sys.modules["amplifier_session_storage.cosmos.file_storage"] = _cosmos_file
