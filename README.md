# Amplifier CLI

Command-line interface for the Amplifier AI-powered modular development platform.

## Installation

```bash
# Install from PyPI (when published)
pip install amplifier-app-cli

# Install from source
pip install -e .

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
