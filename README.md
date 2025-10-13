# Amplifier CLI

Command-line interface for the Amplifier AI-powered modular development platform.

> **Note**: This is a **reference implementation** of an Amplifier CLI. It works with [amplifier-core](https://github.com/microsoft/amplifier-core) and demonstrates how to build a CLI around the official kernel. You can use this as-is, fork it, or build your own CLI using the core.

## Prerequisites

- **Python 3.11+**
- **[UV](https://github.com/astral-sh/uv)** - Fast Python package manager

### Installing UV

```bash
# macOS/Linux/WSL
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Installation

```bash
# Install from PyPI (when published)
uv pip install amplifier-app-cli

# Install from source
uv pip install -e .

# Or use with uvx directly from GitHub
uvx --from git+https://github.com/microsoft/amplifier-app-cli amplifier --help
```

## Quick Start

```bash
# Initialize a configuration
amplifier init

# Run a single command
amplifier run "Create a Python function to calculate fibonacci numbers"

# Start interactive chat mode
amplifier run --mode chat

# Use a specific provider
amplifier run --provider anthropic --model claude-sonnet-4.5 "Your prompt"

# List installed modules
amplifier module list

# Get info about a specific module
amplifier module info loop-basic
```

## Interactive Chat Mode

When you run `amplifier run --mode chat`, you enter an interactive session with powerful slash commands:

```bash
# Planning and execution
/think          # Enter plan mode (read-only, thoughtful responses)
/do             # Exit plan mode and allow modifications

# Session management
/save [file]    # Save conversation transcript
/clear          # Clear conversation history
/status         # Show current session status

# Discovery
/tools          # List available tools
/config         # Show current configuration
/help           # Show all commands

# Control
/stop           # Stop current execution
```

### Plan Mode

Use `/think` to enable plan mode where the AI can analyze and plan without making modifications. This is useful for:
- Reviewing complex changes before executing
- Getting thoughtful analysis of large codebases
- Planning multi-step refactoring

Use `/do` to exit plan mode and allow the AI to make modifications.

### Saving Transcripts

The `/save` command saves your conversation history to `.amplifier/transcripts/`:

```bash
# In chat mode
> /save my_session.json
✓ Transcript saved to .amplifier/transcripts/my_session.json
```

For detailed interactive mode documentation, see [docs/INTERACTIVE_MODE.md](docs/INTERACTIVE_MODE.md).

## Configuration

The CLI can be configured via:
- Command-line options (highest priority)
- Configuration file (`amplifier.toml` or custom path via `--config`)
- Environment variables

Example configuration:
```toml
[provider]
name = "anthropic"
model = "claude-sonnet-4.5"

[modules]
orchestrator = "loop-basic"
context = "context-simple"

[session]
max_tokens = 100000
auto_compact = true
```

## Module Management

The CLI provides commands to manage Amplifier modules:

- `amplifier module list` - List all installed modules
- `amplifier module info <name>` - Show detailed module information
- `amplifier module list --type agent` - List modules by type

## License

MIT - See LICENSE file for details

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
