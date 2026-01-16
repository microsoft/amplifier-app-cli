# Final Cosmetic Cleanup

## Remaining Non-FIXME, Non-Legitimate References

### 1. Variable Name: default_profile (2 refs)
```python
# commands/tool.py:256,342
use_bundle, default_bundle, default_profile = _should_use_bundle()
```
**Fix**: Rename `default_profile` → `_unused` (it's always None anyway)

### 2. Comment: "profile mode" (1 ref)
```python
# lib/mention_loading/app_resolver.py:116
# === BUNDLE MAPPINGS (dict fallback for profile mode) ===
```
**Fix**: Change to `# === BUNDLE MAPPINGS ===`

### 3. Comments in main.py (2 refs)
```python
# main.py:767
# Note: agents can be a dict (resolved agents) or list/other format (profile config)

# main.py:1044
# If no command specified, launch chat mode with current profile
```
**Fix**: Remove "(profile config)" and "with current profile"

### 4. Comment about defaults (1 ref)
```python
# provider_manager.py:114
# Priority 1 ensures explicitly configured provider wins over profile defaults (100)
```
**Fix**: Change "profile defaults" → "default providers"

### Total: 6 cosmetic items
