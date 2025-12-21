# Profile System Defaults (DEPRECATED)

> **⚠️ DEPRECATED**: The profile system is deprecated. Use **bundles** instead.
> See [Bundle Guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md) for the current approach.
> See [Migration Guide](https://github.com/microsoft/amplifier/blob/main/docs/MIGRATION_COLLECTIONS_TO_BUNDLES.md) to migrate existing profiles.

This directory contains legacy system-level defaults for backward compatibility.

## Migration Path

```bash
# Check your current configuration
amplifier bundle current

# If using profiles, migrate to bundles:
amplifier bundle clear  # Clears profile, defaults to foundation bundle

# Or explicitly set a bundle
amplifier bundle use foundation
```

## Contents (Legacy)

- `DEFAULTS.yaml` - Legacy system default profile configuration
- `__init__.py` - Python utility for backward compatibility

## Current Approach: Bundles

New users should use bundles instead of profiles:

```bash
# List available bundles
amplifier bundle list

# Use a bundle
amplifier bundle use foundation

# Run with a specific bundle
amplifier run --bundle foundation "Your prompt"
```

## Documentation

**→ [Bundle Guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md)** - Creating and using bundles (current)

**→ [Migration Guide](https://github.com/microsoft/amplifier/blob/main/docs/MIGRATION_COLLECTIONS_TO_BUNDLES.md)** - Migrating from profiles to bundles
