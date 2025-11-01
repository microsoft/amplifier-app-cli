# Amplifier CLI

Command-line interface for the Amplifier AI-powered modular development platform.

> **Note**: This is a **reference implementation** of an Amplifier CLI. It works with [amplifier-core](https://github.com/microsoft/amplifier-core) and demonstrates how to build a CLI around the kernel. You can use this as-is, fork it, or build your own CLI using the core.

## Installation

### For Users

```bash
# Try without installing
uvx --from git+https://github.com/microsoft/amplifier@next amplifier

# Install globally
uv tool install git+https://github.com/microsoft/amplifier@next
```

## Quick Start

```bash
# First-time setup (auto-runs if no config)
amplifier init

# Install shell completion (optional, one-time setup)
amplifier --install-completion

# Single command
amplifier run "Create a Python function to calculate fibonacci numbers"

# Interactive chat mode
amplifier

# Use specific profile
amplifier run --profile dev "Your prompt"
```

## Commands

### Configuration Commands

```bash
# Provider management
amplifier provider use <name> [--local|--project|--global]
amplifier provider current
amplifier provider list
amplifier provider reset [--scope]

# Profile management
amplifier profile use <name> [--local|--project|--global]
amplifier profile current
amplifier profile list
amplifier profile show <name>
amplifier profile default [--set <name>|--clear]
amplifier profile reset

# Collection management
amplifier collection add <git-url> [--local]
amplifier collection list
amplifier collection show <name>
amplifier collection remove <name> [--local]

# Module management
amplifier module add <name> [--local|--project|--global]
amplifier module remove <name> [--scope]
amplifier module current
amplifier module list
amplifier module show <name>

# Source management
amplifier source add <id> <uri> [--local|--project|--global]
amplifier source remove <id> [--scope]
amplifier source list
amplifier source show <id>
```

### Session Commands

```bash
amplifier run "prompt"               # Single interaction
amplifier                            # Interactive chat
amplifier session list               # Recent sessions
amplifier session show <id>          # Session details
amplifier session resume <id>        # Continue session
amplifier session delete <id>        # Delete session
amplifier session cleanup [--days N] # Clean up old sessions
```

### Utility Commands

```bash
amplifier init                       # First-time setup
amplifier logs                       # Watch activity log
amplifier --install-completion       # Set up tab completion
amplifier --version                  # Show version
amplifier --help                     # Show help
```

## Shell Completion

Enable tab completion with one command. Amplifier automatically installs completion for standard shell setups.

### One-Command Installation

```bash
amplifier --install-completion
```

**What happens**:
1. Detects your shell (bash, zsh, or fish) from `$SHELL`
2. **Automatically appends** the completion line to your shell config:
   - Bash: `~/.bashrc`
   - Zsh: `~/.zshrc`
   - Fish: `~/.config/fish/completions/amplifier.fish`
3. Checks if already installed (safe to run multiple times)
4. Falls back to manual instructions if custom configuration detected

**Output (standard setup)**:
```
Detected shell: bash
✓ Added completion to /home/user/.bashrc

To activate:
  source ~/.bashrc

Or start a new terminal.
```

**Output (already installed)**:
```
Detected shell: bash
✓ Completion already configured in /home/user/.bashrc
```

### Tab Completion Works Everywhere

Once active, tab completion works throughout the CLI:

```bash
amplifier pro<TAB>         # Completes to "profile"
amplifier profile u<TAB>   # Completes to "use"
amplifier profile use <TAB> # Shows available profiles
amplifier run --<TAB>      # Shows all options
```

## Architecture

This CLI is built on top of amplifier-core and provides:

- **Profile system** - Reusable, composable configuration bundles (via amplifier-profiles)
- **Settings management** - Three-scope configuration (local/project/global via amplifier-config)
- **Module resolution** - Five-layer module source resolution (via amplifier-module-resolution)
- **Collection system** - Shareable expertise bundles (via amplifier-collections)
- **Session storage** - Project-scoped session persistence
- **Interactive mode** - REPL with slash commands
- **Key management** - Secure API key storage

## Supported Providers

- **Anthropic Claude** - Recommended, most tested (Sonnet, Opus models)
- **OpenAI** - Good alternative (GPT-4o, GPT-4o-mini, o1 models)
- **Azure OpenAI** - Enterprise users with Azure subscriptions
- **Ollama** - Local, free, no API key needed

### Provider sources

`amplifier provider use …` now pins the canonical module source for each
first-party provider (for example, the OpenAI provider resolves to
`git+https://github.com/microsoft/amplifier-module-provider-openai@main`).
Existing installations inherit these canonical URIs at runtime as well, so
fresh environments download the provider code via **uv** automatically. No
manual source overrides are required for the built-in providers.

## Development

### Prerequisites

- Python 3.11+
- [UV](https://github.com/astral-sh/uv) package manager

### Setup

```bash
cd amplifier-app-cli
uv pip install -e .
uv run pytest
```

### Project Structure

```
amplifier_app_cli/
├── commands/          # CLI command implementations (provider, collection, init, logs, setup)
├── data/
│   ├── collections/   # Bundled collections (foundation, developer-expertise)
│   ├── agents/        # Bundled agents (inside collections)
│   ├── profiles/      # Profile defaults and metadata
│   └── context/       # Bundled context files
├── lib/               # Shared libraries
│   └── mention_loading/ # @mention expansion system
├── utils/             # Utility functions
├── banners/           # Banner art
├── paths.py           # Path configuration and factory functions
├── key_manager.py     # API key management
├── provider_manager.py # Provider configuration
├── module_manager.py  # Module management
├── session_store.py   # Session persistence
├── agent_config.py    # Agent configuration utilities
└── main.py            # CLI entry point

toolkit/               # Standalone scenario tool utilities (at repo root)
├── utilities/         # Structural utilities (file ops, progress, validation)
├── examples/          # Example tools (tutorial_analyzer)
└── templates/         # Tool templates
```

**Note**: Core functionality provided by libraries:
- `amplifier-profiles` - Profile loading and compilation
- `amplifier-config` - Settings management
- `amplifier-module-resolution` - Module source resolution
- `amplifier-collections` - Collection installation and discovery

## Related Documentation

- [Complete user guide](../docs/USER_ONBOARDING.md)
- [Configuration reference](../docs/USER_ONBOARDING.md#quick-reference)
- [Collections guide](../docs/COLLECTIONS_GUIDE.md)
- [Scenario tools guide](../docs/SCENARIO_TOOLS_GUIDE.md)
- [Profile authoring](../docs/PROFILE_AUTHORING.md)
- [Agent authoring](../docs/AGENT_AUTHORING.md)
- [Module development](../docs/MODULE_DEVELOPMENT.md)
- [Toolkit guide](../docs/TOOLKIT_GUIDE.md)

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
