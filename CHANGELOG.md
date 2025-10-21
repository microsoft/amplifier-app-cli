# Changelog

All notable changes to amplifier-app-cli are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Enhanced UI System**: Complete visual overhaul with TinkerTasker-inspired improvements
  - Markdown rendering for LLM responses (bold, italic, code blocks, lists)
  - Live elapsed time display during LLM operations with real-time updates
  - Tree-style formatting with bullet characters (● and ⎿) for visual hierarchy
  - Configurable tool output truncation (default: 3 lines, -1 for all)
  - Event bus architecture for clean separation of display logic

- **UI Configuration in Profiles**: New `ui` section in profile YAML
  - `tool_output_lines`: Control how many lines of tool output to show
  - `max_arg_length`: Maximum length for tool arguments in display
  - `show_elapsed_time`: Toggle live progress feedback
  - `use_tree_formatting`: Enable/disable tree-style output
  - `render_markdown`: Toggle markdown rendering in responses

- **Session Configuration**: Exposed `max_iterations` in orchestrator config
  - Configurable per profile (was previously hardcoded at 30)
  - Different defaults by use case: base/foundation (30), dev (50), production/full (100), test (20)
  - Prevents "maximum iterations exceeded" errors on complex tasks
  - Documented in Profile README with adjustment guidelines

- **Display Modules**: New architecture for extensible display handling
  - `amplifier_app_cli/events/` - Event bus and message schemas
  - `amplifier_app_cli/display/` - Display handlers and formatters

### Changed

- **Profile Updates**: All bundled profiles now include UI configuration
  - `base.md`: Standard defaults (3 lines, markdown enabled)
  - `dev.md`: Development-friendly (3 lines, full features)
  - `production.md`: Minimal output (2 lines, optimized)
  - `test.md`: Verbose for debugging (-1 lines, show all)
  - `full.md`: All features enabled (5 lines)
  - `foundation.md`: Basic UI config

### Improved

- **Documentation**: Comprehensive UI configuration guide
  - Profile README expanded with UI configuration section
  - INTERACTIVE_MODE.md updated with visual features guide
  - Main README highlights new UI capabilities

### Technical

- Simplified main loop by extracting display logic to event handlers
- Event-driven display architecture enables multiple subscribers
- Clean separation between execution logic and visual formatting
- All display features configurable without code changes

---

## Notes

- Conservative 5-day implementation timeline
- Bonus features (syntax highlighting, collapsible output) planned for future iterations
- All changes maintain backwards compatibility with existing profiles
- Event bus kept separate from kernel hooks (different purposes)
