# Remaining Profile/Collection References - Detailed Breakdown

After hard cutover, ~130 references remain. Here's what they are:

---

## Category 1: ConfigManager (~20 refs) - ACTUAL DEPENDENCY

### What It Is

ConfigManager is a class that provider/module management code depends on:

```python
# lib/config_compat.py
class ConfigManager:
    """Settings management configuration layer."""
    
    def __init__(self, paths: ConfigPaths):
        self.paths = paths
    
    def get_merged_settings(self) -> dict:
        # Read and merge YAML from local/project/global
        ...
```

### Where It's Used

**provider_manager.py:**
```python
class ProviderManager:
    def __init__(self, config: ConfigManager):  # ← Requires ConfigManager
        self.config = config
```

**module_manager.py:**
```python
class ModuleManager:
    def __init__(self, config_manager: ConfigManager | None = None):  # ← Optional but used
        self.config_manager = config_manager or create_config_manager()
```

**provider_loader.py:**
```python
def load_provider_module(
    module_id: str,
    config_manager: "ConfigManager | None" = None,  # ← Optional parameter
    ...
) -> ProviderMount:
    ...
```

### Example Usage Chain

```python
# paths.py creates it
def create_config_manager() -> ConfigManager:
    return ConfigManager(paths=get_cli_config_paths())

# provider commands use it
from .paths import create_config_manager

config = create_config_manager()
provider_mgr = ProviderManager(config)
```

### Why It Exists

This is a **separate layer** from AppSettings:
- **AppSettings**: Used by bundle/source commands (modern)
- **ConfigManager**: Used by provider/module managers (older code)

Both do similar things (read/write YAML), but different subsystems depend on each.

### To Remove ConfigManager

Would require refactoring:
- `provider_manager.py` - migrate to AppSettings
- `module_manager.py` - migrate to AppSettings  
- `provider_loader.py` - update signature
- All callers of these classes

**Effort**: ~2-3 hours  
**Risk**: Medium (touches provider loading)  
**Benefit**: Removes ~20 references, consolidates settings APIs

---

## Category 2: `profile_name` Parameter (~30 refs) - DISPLAY/METADATA ONLY

### What It Is

A parameter threaded through session creation functions, used only for display and metadata:

```python
# main.py
def interactive_chat(
    session: AmplifierSession,
    verbose: bool,
    session_id: str | None = None,
    profile_name: str = "unknown",  # ← This parameter
    prepared_bundle: PreparedBundle | None = None,
    ...
):
    """
    Args:
        profile_name: Profile or bundle name (e.g., "dev" or "bundle:foundation")
    """
    
    # Used here to create session metadata
    metadata = {
        "bundle": profile_name,  # ← Stored as "bundle" key
        "created": datetime.now(),
        ...
    }
    store.save(session_id, transcript, metadata)
```

### Where It Appears

**Function signatures** (parameter threading):
- `main.py:interactive_chat(profile_name=...)` - line 1177
- `main.py:register_incremental_save(..., profile_name, ...)` - line 1228
- `main.py:non_interactive_prompt(..., profile_name, ...)` - line 1453
- `commands/run.py` - 4 call sites passing `profile_name=config_source_name`
- `commands/session.py` - 3 call sites passing `profile_name=active_bundle`

**Usage** (what it's actually used for):
```python
# main.py:1209 - Session initialization
session = AmplifierSession(
    config=session_config,
    bundle_name=profile_name,  # ← Becomes session.bundle_name for display
)

# main.py:1271 - Session metadata
metadata = {
    "bundle": profile_name,  # ← Stored for session resume
    "created": datetime.now(),
    ...
}

# main.py:1232 - Config summary display
config_summary = get_effective_config_summary(config, profile_name)
```

### What Happens If We Rename It

**Option A: Rename parameter**
```python
# Change all function signatures:
def interactive_chat(
    ...
    bundle_name: str = "unknown",  # ← Was profile_name
    ...
):
```

**Impact**: 
- ~30 lines changed (all parameter names)
- Zero functional change
- Clearer naming

**Option B: Keep it**
```python
# Keep profile_name but it actually contains bundle name
profile_name: str = "foundation"  # Contains "foundation" bundle
```

**Impact**:
- Confusing variable name
- Works perfectly fine
- 30 references to "profile" remain

### Is This Context Poisoning?

**Arguable**:
- ❌ Variable named `profile_name` but contains bundle names
- ✅ Only used internally for threading data
- ✅ Never exposed to users
- ✅ Docstrings say "Profile or bundle name" (technically correct)

### Recommendation

**Low priority rename** - cosmetic consistency, not context poisoning in the user-facing sense.

---

## Category 3: Backward Compat Comments (~80 refs) - NOT PROFILE/COLLECTION RELATED

### What These Are

Comments about backward compatibility for **other features**, not profiles/collections:

**Example 1: Agent spawn policy backward compat**
```python
# agent_config.py:35
if not spawn_policy:
    # No spawn policy - return parent unchanged (backward compatible)
    return parent_tools
```

**Context**: This is about agent **spawn policies** (tool inheritance), NOT profiles.

---

**Example 2: Re-exported functions**
```python
# utils/update_check.py:75
def check_module_sources(module_sources: dict) -> list:
    """Re-exported from source_status for backwards compatibility."""
    return source_status.check_module_sources(module_sources)
```

**Context**: Function moved to another module, old import path still works. NOT about profiles.

---

**Example 3: Display code compatibility**
```python
# commands/module.py:345
# Returns dict for backward compatibility with existing display code.
return {
    "name": module_id,
    "source": source_uri,
    ...
}
```

**Context**: Function returns dict instead of object for display layer. NOT about profiles.

---

**Example 4: Legacy module cache format**
```python
# utils/module_cache.py:295
# Also check legacy format: {hash}/{ref}/.amplifier_cache_metadata.json
for legacy_meta in cache_dir.rglob(".amplifier_cache_metadata.json"):
    ...
```

**Context**: Old cache directory structure vs new. NOT about profiles.

---

**Example 5: ConfigManager compatibility layer**
```python
# lib/config_compat.py:1-3
"""Settings management configuration layer.

This module provides ConfigManager and related classes for backward compatibility.
"""
```

**Context**: ConfigManager is called "backward compatibility" because it's older API vs AppSettings. NOT about profiles (though it was used by profile code).

---

**Example 6: Settings scope compatibility**
```python
# lib/settings.py:103
"""Get active bundle name.

Reads from bundle.active path for compatibility with bundle commands.
"""
```

**Context**: "Compatibility with bundle commands" means the bundle.py commands expect this structure. NOT about profiles.

---

**Example 7: ESC cancellation**
```python
# main.py:67
# Cancel flag for ESC-based cancellation (legacy, kept for compatibility)
_cancel_requested = False
```

**Context**: Old keyboard interrupt handling. NOT about profiles.

---

### Summary of "Backward Compat" Comments

Most of these comments explain:
- Why a function returns a dict instead of object (display compat)
- Why old import paths still work (re-exports)
- Why old cache formats are supported (migration)
- Why ESC key handling exists (user expectations)
- Why certain data structures are used (other code expects them)

**Only 1-2** actually reference the ConfigManager being "for backward compatibility" in the sense that it's older API.

---

## Visual Breakdown

```
Total remaining: ~130 references to "profile" or "collection"

ConfigManager dependency:          ████░░░░░░  ~20 refs  (15%)
profile_name parameter:            ██████░░░░  ~30 refs  (23%)
Other backward compat comments:    ████████░░  ~80 refs  (62%)
```

---

## What Each Category Means

### 1. ConfigManager (~20 refs)

**Type**: Structural dependency  
**Effort to remove**: 2-3 hours (refactoring)  
**Poisoning level**: Low (it's just an older settings API)  
**User impact**: Zero (internal only)

**Example**:
```python
# paths.py:14
from amplifier_app_cli.lib.config_compat import ConfigManager

# paths.py:239
def create_config_manager() -> ConfigManager:
    return ConfigManager(paths=get_cli_config_paths())
```

**To remove**: Migrate ProviderManager and ModuleManager to use AppSettings instead

---

### 2. `profile_name` Parameter (~30 refs)

**Type**: Misleading variable name  
**Effort to remove**: 30 minutes (find/replace)  
**Poisoning level**: Medium (confusing but works)  
**User impact**: Zero (never see it)

**Example**:
```python
# main.py:1177
def interactive_chat(
    profile_name: str = "unknown",  # ← Contains bundle name, not profile
):
    metadata = {
        "bundle": profile_name,  # ← Saved as "bundle" key
    }
```

**To remove**: Global find/replace `profile_name` → `bundle_name` in parameters

---

### 3. Other Backward Compat Comments (~80 refs)

**Type**: Legitimate backward compat for OTHER features  
**Effort to remove**: Not applicable (they're correct)  
**Poisoning level**: Zero (not about profiles/collections)  
**User impact**: Zero

**Examples**:
```python
# Re-exports for old import paths
Re-exported from source_status for backwards compatibility.

# Spawn policy default behavior  
No spawn policy - return parent unchanged (backward compatible)

# Cache format migration
Also check legacy format: {hash}/{ref}/.amplifier_cache_metadata.json

# Display layer expectations
Returns dict for backward compatibility with existing display code.
```

**To remove**: Don't - these are about other features

---

## Decision Matrix

| Category | Refs | Effort | Context Poison? | Recommendation |
|----------|------|--------|-----------------|----------------|
| ConfigManager | ~20 | 2-3 hrs | Low | Migrate later (separate task) |
| profile_name param | ~30 | 30 min | Medium | Rename now (easy win) |
| Other compat comments | ~80 | N/A | Zero | Keep (not about profiles) |

---

## Bottom Line

**Real remaining profile/collection poisoning**: ~50 references (ConfigManager + profile_name)

**Not actually poisoning**: ~80 references (backward compat for other features)

**Quick win**: Rename `profile_name` → `bundle_name` (30 mins, removes 30 refs)

**Bigger task**: Migrate ConfigManager users to AppSettings (2-3 hrs, removes 20 refs)

