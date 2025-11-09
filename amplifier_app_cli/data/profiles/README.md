# Bundled Amplifier Profiles

This directory contains bundled Amplifier profiles that ship with the `amplifier-app-cli` package.

## Documentation

**→ [Profile Authoring Guide](https://github.com/microsoft/amplifier-profiles/blob/main/docs/PROFILE_AUTHORING.md)** - Complete guide to creating and customizing profiles

**→ [Profile System Design](https://github.com/microsoft/amplifier-profiles/blob/main/docs/DESIGN.md)** - Architecture and inheritance system

## Bundled Profiles

This CLI application bundles these profiles:

| Profile | Description | Use Case |
|---------|-------------|----------|
| **foundation** | Minimum viable config | Learning, testing basics |
| **base** | Core functionality | Standard usage |
| **dev** | Development tools | Interactive development |
| **production** | Production-optimized | Long-running sessions |
| **test** | Testing config | Automated testing |
| **full** | All features | Exploration, demos |

See profile files in this directory for complete configuration details.

## CLI Commands

```bash
# List available profiles
amplifier profile list

# Show profile details
amplifier profile show dev

# Use a profile
amplifier profile use dev           # Personal choice (gitignored)
amplifier profile use dev --project # Project default (committed)
amplifier profile use dev --global  # User-wide default

# Check active profile
amplifier profile current

# Run with specific profile
amplifier run --profile production "your prompt"
```

## Profile Search Paths

This CLI searches for profiles in order (highest precedence first):

1. Project profiles: `.amplifier/profiles/`
2. User profiles: `~/.amplifier/profiles/`
3. Collection profiles: From installed collections
4. Bundled profiles: This directory

## Creating Custom Profiles

See **→ [Profile Authoring Guide](https://github.com/microsoft/amplifier-profiles/blob/main/docs/PROFILE_AUTHORING.md)** for:
- Profile format and structure
- Inheritance and merging rules
- Module configuration
- Agent loading patterns
- Complete examples

Quick example extending a bundled profile:

```markdown
---
profile:
  name: my-custom
  extends: dev
tools:
  - module: my-custom-tool
    source: git+https://github.com/me/my-tool@main
---

Additional system instructions here...
```
