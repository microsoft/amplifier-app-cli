"""LEGACY: Centralized imports from deprecated libraries for Phase 4 deletion.

DELETE WHEN: Profiles/collections removed in Phase 4.

This module centralizes all imports from deprecated libraries so they can be
managed in a single location. The libraries are:
- amplifier-collections (pure LEGACY)
- amplifier-profiles (pure LEGACY)
- amplifier-config (SHARED - bundles still use Scope, ConfigManager)
- amplifier-module-resolution (SHARED - bundles still use GitSource, FileSource)

PHASE 4 DELETION STRATEGY:
==========================

1. PURE LEGACY (delete submodules entirely):
   - collections.py → Delete (only used by profile/collection codepath)
   - profiles.py → Delete (only used by profile codepath)
   - agents.py → Delete (only used by profile/collection codepath)

2. SHARED UTILITIES (migrate before deleting):
   - config.py → Migrate to lib/settings.py (bundles use Scope, ConfigManager)
   - resolver.py → Migrate to lib/settings.py (bundles use StandardModuleSourceResolver)
   - sources.py → Migrate to lib/settings.py (bundles use GitSource, FileSource)

3. FILES TO DELETE (outside this directory):
   - commands/collection.py → Delete entire file
   - commands/profile.py → Delete entire file
   - tests/test_paths_library_mechanisms.py → Delete entire file

4. FUNCTIONS TO DELETE (marked with # LEGACY-DELETE comments):
   - paths.py: get_collection_search_paths, get_collection_lock_path,
               get_profile_search_paths, create_collection_resolver,
               create_profile_loader, get_agent_search_paths_for_profile,
               CLICollectionModuleProvider class
   - utils/update_executor.py: execute_selective_collection_update function
   - utils/source_status.py: _check_collection_sources function

5. REFACTORING NEEDED:
   - After migrating shared utilities, update imports in:
     commands/bundle.py, commands/module.py, commands/source.py,
     paths.py, provider_manager.py, module_manager.py, provider_sources.py
"""

# ----- Module Resolution (from resolver.py, sources.py) -----
# ----- Agents (from agents.py) -----
from .agents import Agent
from .agents import AgentLoader
from .agents import AgentMetadata
from .agents import AgentResolver
from .agents import CollectionAgentsResolver
from .agents import parse_agent_file

# ----- Collections (from collections.py) -----
# Re-exports from amplifier_collections
from .collections import CollectionInstallError
from .collections import CollectionLock
from .collections import CollectionMetadata
from .collections import CollectionResolver
from .collections import discover_collection_resources

# Local utility functions
from .collections import extract_collection_name_from_path
from .collections import get_collection_subpath
from .collections import install_collection
from .collections import is_collection_path
from .collections import list_agents
from .collections import list_profiles
from .collections import make_collection_module_id
from .collections import parse_collection_module_id
from .collections import uninstall_collection

# ----- Config Management (from config.py) -----
from .config import ConfigManager
from .config import ConfigPaths
from .config import Scope

# ----- Profiles (from profiles.py) -----
from .profiles import CollectionProfilesResolver
from .profiles import DataclassProfile
from .profiles import ModuleConfig
from .profiles import Profile
from .profiles import ProfileLoader
from .profiles import ProfileMetadata
from .profiles import PydanticProfile
from .profiles import PydanticProfileMetadata
from .profiles import SessionConfig
from .profiles import compile_profile_to_mount_plan
from .profiles import deep_merge
from .profiles import merge_module_items
from .profiles import merge_module_lists
from .profiles import merge_profile_configs
from .profiles import merge_profile_dicts
from .profiles import parse_markdown_body
from .profiles import parse_profile_file
from .resolver import CollectionModuleProviderProtocol
from .resolver import SettingsProviderProtocol
from .resolver import StandardModuleSourceResolver
from .sources import FileSource
from .sources import GitSource
from .sources import InstallError
from .sources import ModuleResolutionError
from .sources import PackageSource

__all__ = [
    # ===== Module Resolution =====
    "StandardModuleSourceResolver",
    "SettingsProviderProtocol",
    "CollectionModuleProviderProtocol",
    "FileSource",
    "GitSource",
    "PackageSource",
    "ModuleResolutionError",
    "InstallError",
    # ===== Config Management =====
    "ConfigManager",
    "ConfigPaths",
    "Scope",
    # ===== Collections =====
    # Re-exports from amplifier_collections
    "CollectionInstallError",
    "CollectionLock",
    "CollectionMetadata",
    "CollectionResolver",
    "discover_collection_resources",
    "install_collection",
    "list_agents",
    "list_profiles",
    "uninstall_collection",
    # Local utility functions
    "extract_collection_name_from_path",
    "get_collection_subpath",
    "is_collection_path",
    "make_collection_module_id",
    "parse_collection_module_id",
    # ===== Profiles =====
    # Pydantic schema
    "ModuleConfig",
    "SessionConfig",
    "Profile",
    "PydanticProfile",
    "PydanticProfileMetadata",
    # Dataclass schema
    "ProfileMetadata",
    "DataclassProfile",
    # Loader
    "ProfileLoader",
    "CollectionProfilesResolver",
    # Parsing
    "parse_profile_file",
    "parse_markdown_body",
    # Merging
    "deep_merge",
    "merge_module_lists",
    "merge_module_items",
    "merge_profile_configs",
    "merge_profile_dicts",
    # Compiler
    "compile_profile_to_mount_plan",
    # ===== Agents =====
    "AgentMetadata",
    "Agent",
    "AgentLoader",
    "AgentResolver",
    "CollectionAgentsResolver",
    "parse_agent_file",
]
