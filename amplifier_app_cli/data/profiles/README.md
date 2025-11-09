# Profile System Defaults

This directory contains system-level defaults for the Amplifier profile system.

## Contents

- `DEFAULTS.yaml` - System default profile configuration (single source of truth)
- `__init__.py` - Python utility: `get_system_default_profile()`

## Bundled Profiles Location

**Profiles are NOT in this directory.** They are bundled in collections:

- **Foundation collection**: `data/collections/foundation/profiles/`
  - foundation.md, base.md, production.md, test.md

- **Developer-Expertise collection**: `data/collections/developer-expertise/profiles/`
  - dev.md, full.md

See `amplifier_app_cli/data/collections/` for bundled collections.

## System Default Profile

The default profile used when no profile is active is defined in `DEFAULTS.yaml`.

To change the system default, edit `DEFAULTS.yaml` (this is the ONLY place to change it).

Current default: `developer-expertise:dev`

## Documentation

**→ [Profile Authoring Guide](https://github.com/microsoft/amplifier-profiles/blob/main/docs/PROFILE_AUTHORING.md)** - Complete profile system documentation

**→ [Collections Guide](https://github.com/microsoft/amplifier-collections/blob/main/README.md)** - Understanding collections
