# Changelog

All notable changes to the Amplifier CLI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Collection update and refresh functionality
  - `amplifier collection refresh` command to update installed collections
  - Collections now included in `amplifier update` orchestration
  - SHA-based update detection for collections pinned to mutable refs
  - Support for `--mutable-only` flag to skip pinned versions
  - Consistent UX with module refresh commands
- Shell completion support via `--install-completion` flag
  - Supports Bash, Zsh, and Fish shells
  - Auto-detects current shell when not specified
  - Provides clear instructions for shell configuration
  - Tab completion works for commands, subcommands, options, and arguments
