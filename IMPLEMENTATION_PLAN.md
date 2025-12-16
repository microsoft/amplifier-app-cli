# Module Registry Discovery Implementation Plan

## Overview
Add module discovery functionality to amplifier-app-cli by integrating with the amplifier-modules registry. This is a **discovery-only** implementation - installation is handled by existing `amplifier module add` command.

**Spec Reference**: `C:\Users\malicata\source\amplifier-modules\CLI_INTEGRATION_SPEC.md`

---

## Architecture Summary

### What We're Adding
- **RegistryClient**: Fetch and cache registry index from GitHub
- **Three new commands**: `module registry`, `module search`, `module info`
- **Caching layer**: 1-hour TTL to reduce network calls

### What Already Exists (Unchanged)
- `amplifier module list` - List installed modules
- `amplifier module show` - Show installed module details
- `amplifier module add` - Installation (no changes needed)
- `amplifier source add` - Source management (no changes needed)
- Module management infrastructure in `module_manager.py`

---

## File Structure

```
amplifier_app_cli/
├── registry/
│   ├── __init__.py              # NEW: Package exports
│   ├── client.py                # NEW: RegistryClient class
│   └── commands/
│       ├── __init__.py          # NEW: Command exports
│       ├── registry.py          # NEW: Registry list command
│       ├── search.py            # NEW: Search command
│       └── info.py              # NEW: Info command
├── commands/
│   └── module.py                # MODIFY: Add new subcommands
└── paths.py                     # NO CHANGE: Cache dir already exists
```

---

## Implementation Steps

### Phase 1: Registry Client (Core Infrastructure)

**File**: `amplifier_app_cli/registry/client.py`

**Purpose**: Fetch, cache, and query the module registry

**Key Features**:
- Fetch `index.json` from `https://raw.githubusercontent.com/microsoft/amplifier-modules/main/registry/index.json`
- Cache at `~/.amplifier/cache/registry-index.json` with 1-hour TTL
- Methods: `fetch_index()`, `list_modules()`, `search()`, `get_module()`
- Error handling with fallback to cached data

**Dependencies**:
- Use existing `httpx` (already in dependencies)
- Use `Path` from pathlib for cache management
- Use `json` for parsing

**Implementation Notes**:
- Cache directory: `Path.home() / ".amplifier" / "cache"`
- Cache file: `registry-index.json`
- TTL: 3600 seconds (1 hour)
- Retry logic: 2 attempts with exponential backoff
- Fallback: Use cached data if network fails

---

### Phase 2: Discovery Commands

#### 2.1 Registry Command

**File**: `amplifier_app_cli/registry/commands/registry.py`

**Purpose**: Display available modules from registry (similar to `module list` but for registry)

**Options**:
- `--type <type>`: Filter by module type (agent, behavior, provider, bundle, context)
- `--verified`: Show only verified modules
- `--json`: Output as JSON

**Display Format**:
```
Available Modules (3)
════════════════════════════════════════════════════════════════

✓ code-reviewer (1.0.0) [agent]
  Automated code review agent
  Tags: code-quality, automation, review
  Author: Amplifier Team (microsoft)
```

---

#### 2.2 Search Command

**File**: `amplifier_app_cli/registry/commands/search.py`

**Purpose**: Search modules by keyword

**Arguments**:
- `<query>`: Search term (required)

**Options**:
- `--type <type>`: Filter by module type
- `--verified`: Show only verified modules
- `--json`: Output as JSON

**Search Algorithm** (Simple):
- Exact name match: 100% relevance
- Name contains query: 80%
- Description contains query: 60%
- Tags contain query: 70%
- Sort by relevance descending

**Display Format**:
```
Found 2 modules matching "code review":

✓ code-reviewer (1.0.0)
  Automated code review agent
  Relevance: ████████░░ 80%
```

---

#### 2.3 Info Command

**File**: `amplifier_app_cli/registry/commands/info.py`

**Purpose**: Show detailed module information

**Arguments**:
- `<name>`: Module name (required)

**Options**:
- `--json`: Output as JSON

**Display Format**:
```
code-reviewer (1.0.0) ✓ Verified
═══════════════════════════════════════════════════════════════

Description:
  Automated code review agent that analyzes code quality, style,
  and potential issues

Author: Amplifier Team (microsoft)
License: MIT
Repository: https://github.com/microsoft/amplifier-code-reviewer

Type: agent
Entry Point: code_reviewer.agent:CodeReviewerAgent

Compatibility:
  Foundation: >=0.1.0
  Python: >=3.10

Tags: code-quality, automation, review, linting

Installation:
  amplifier module add code-reviewer
```

---

### Phase 3: Integration with Module Commands

**File**: `amplifier_app_cli/commands/module.py`

**Changes**:
1. Import registry commands
2. Add new `registry` subcommand (list modules from registry)
3. Add new `search` subcommand (search registry)
4. Add new `info` subcommand (show registry module details)
5. Keep ALL existing commands unchanged

**Command Structure** (after changes):
```bash
amplifier module list              # UNCHANGED: List installed modules
amplifier module registry          # NEW: List from registry (accepts same filters as list)
amplifier module search <query>    # NEW: Search registry
amplifier module info <name>       # NEW: Show module details from registry
amplifier module show <name>       # UNCHANGED: Show installed module details
amplifier module add <name>        # UNCHANGED: Install module
amplifier module remove <name>     # UNCHANGED: Remove module
amplifier module current           # UNCHANGED: Show current modules
amplifier module update            # UNCHANGED: Update modules
```

---

## Error Handling

### Network Errors
- **Issue**: Registry URL unreachable
- **Solution**: Fall back to cached data if available
- **Message**: "Using cached registry data (network unavailable)"

### Cache Errors
- **Issue**: Cache file corrupted or unreadable
- **Solution**: Fetch fresh data, create new cache
- **Message**: "Refreshing registry cache..."

### Module Not Found
- **Issue**: User searches for non-existent module
- **Solution**: Show suggestions based on similar names
- **Message**:
```
ERROR: Module 'xyz' not found in registry

Did you mean:
  - code-reviewer
  - review-assistant

Run 'amplifier module list' to see all available modules
```

---

## Testing Strategy

### Unit Tests
1. **RegistryClient Tests**:
   - Test cache hit/miss scenarios
   - Test TTL expiration
   - Test network failure fallback
   - Test JSON parsing

2. **Command Tests**:
   - Test list with various filters
   - Test search relevance ranking
   - Test info display formatting
   - Test JSON output mode

### Integration Tests
1. Test with real registry (optional, can mock)
2. Test cache directory creation
3. Test error scenarios

### Manual Testing Checklist
- [ ] `amplifier module registry` - Shows modules from registry
- [ ] `amplifier module registry --type agent` - Filters correctly
- [ ] `amplifier module registry --verified` - Shows only verified
- [ ] `amplifier module registry --json` - Outputs valid JSON
- [ ] `amplifier module search "review"` - Finds relevant modules
- [ ] `amplifier module info code-reviewer` - Shows details
- [ ] `amplifier module list` - Still shows installed modules (unchanged)
- [ ] Network failure - Falls back to cache
- [ ] First run (no cache) - Fetches successfully

---

## Dependencies

**No new dependencies needed!** All required packages already in `pyproject.toml`:
- `httpx` - HTTP requests
- `rich` - Terminal output
- `click` - CLI framework
- `json` (stdlib) - JSON parsing
- `pathlib` (stdlib) - File paths

---

## Success Criteria

✅ Users can list all modules from registry
✅ Users can search modules by keyword
✅ Users can view detailed module information
✅ Registry index is cached (1-hour TTL)
✅ Graceful error handling when registry unavailable
✅ Clear integration with existing `amplifier module add`
✅ Help text explains discovery → installation workflow

---

## Future Enhancements (Out of Scope)

These are **not** part of this implementation but noted for future consideration:

1. **Version Management**: Show available versions, update checks
2. **Module Publishing**: Commands to publish new modules
3. **Dependency Resolution**: Show module dependencies
4. **Default Registry**: Configure registry as default source
5. **Multiple Registries**: Support for custom registries

---

## Timeline Estimate

- **Phase 1** (Registry Client): 2-3 hours
- **Phase 2** (Discovery Commands): 3-4 hours
- **Phase 3** (Integration): 1-2 hours
- **Testing**: 2-3 hours

**Total**: 8-12 hours for complete implementation

---

## Questions/Considerations

1. **Cache location**: Should we use existing `.amplifier/cache` or create `.amplifier/registry/cache`?
   - **Decision**: Use existing `.amplifier/cache` for consistency

2. **Command naming**: Should new registry commands replace or complement existing commands?
   - **Decision**: Keep existing commands unchanged, add new `registry` command for discovery

3. **Default registry**: Should we add the amplifier-modules registry as a default source?
   - **Decision**: Not in this phase - this is discovery only, users still use `amplifier module add`

4. **Error verbosity**: How verbose should error messages be?
   - **Decision**: Concise by default, detailed with `--verbose` flag (if available)

---

## References

- **Spec**: `C:\Users\malicata\source\amplifier-modules\CLI_INTEGRATION_SPEC.md`
- **Registry URL**: `https://github.com/microsoft/amplifier-modules`
- **Registry Index**: `https://raw.githubusercontent.com/microsoft/amplifier-modules/main/registry/index.json`
- **Existing Code**: `amplifier_app_cli/commands/module.py`
