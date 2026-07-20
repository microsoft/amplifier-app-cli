# Migration map: `main.py` decomposition

The pre-TUI `amplifier_app_cli/main.py` (commit `87b93ef^`, 3,477 lines) was
decomposed into `runtime/`, `ui/`, and `commands/` modules; `main.py` is now a
~490-line click entrypoint with thin compatibility adapters (kept as patchable
seams — see `tests/test_main_entrypoint_boundary.py`).

Derivation: symbols enumerated with
`git show 87b93ef^:amplifier_app_cli/main.py | grep -nE '^(async def|def|class| {4}(async )?def)'`
and located in the current tree by grep. Line numbers below are from the old
file. Paths are relative to `amplifier_app_cli/`.

Status legend:

- **moved** — same logic, new home (possibly renamed, underscore dropped).
- **rewritten** — behavior preserved, implementation restructured
  (dataclass request/dependency seams, mixins).
- **replaced** — superseded by a new mechanism; old name kept only as a
  compat wrapper in `main.py` where noted.
- **kept** — still lives in `main.py`.

## Top-level symbols

| Old symbol (line) | New location | Status |
|---|---|---|
| `_ensure_utf8_output` (102) | `runtime/terminal_encoding.py` `ensure_utf8_output` | moved (re-imported by `main.py` under the old alias) |
| `_attach_llm_error_filter` (138) | `runtime/log_filter_setup.py` `attach_llm_error_filter` | moved; thin wrapper kept in `main.py` |
| `_detect_shell` (169) | `commands/completion.py` `detect_shell` | moved |
| `_get_shell_config_file` (192) | `commands/completion.py` `shell_config_file` | moved |
| `_completion_already_installed` (221) | `commands/completion.py` `completion_already_installed` | moved |
| `_can_safely_modify` (242) | `commands/completion.py` `can_safely_modify` | moved |
| `_install_completion_to_config` (268) | `commands/completion.py` `install_completion_to_config` | moved |
| `_show_manual_instructions` (309) | `commands/completion.py` `show_manual_instructions` | moved |
| `_parse_config_flags` (330) | `ui/command_config_flags.py` `parse_config_flags` | moved |
| `class CommandProcessor` (366) | `ui/command_processor.py` (facade over mixins, see below) | rewritten |
| `get_module_search_paths` (2400) | `main.py` | kept |
| `cli` (2435) | `main.py` | kept (slimmed; completion handling delegates to `commands/completion.py`) |
| `process_runtime_mentions` (2512) | `main.py` `_process_runtime_mentions` (+ public alias) | kept |
| `_create_prompt_session` (2543) | `runtime/prompt_session.py` `create_interactive_prompt_session` | replaced; compat wrapper kept in `main.py` |
| ↳ nested `insert_newline` / `accept_input` / `get_prompt` (2590–2600) | `ui/repl.py` (plain REPL); layered equivalents in `ui/layered_repl_layout.py` + `ui/layered_repl_keys.py` | moved |
| `interactive_chat` (2626) | `runtime/interactive_resume_loop.py` `run_interactive_loop` → `runtime/interactive_host.py` `run_interactive_host` | rewritten; `main.interactive_chat` is a thin adapter |
| ↳ nested `_extract_model_name` (2708) | `incremental_save.py`; single-shot path has `runtime/single_execution.py` `_model_name` | rewritten |
| ↳ nested `_save_session` (2719) | `runtime/session_persistence.py` `InteractiveSessionPersistence` | rewritten |
| ↳ nested `_repair_transcript_if_needed` (2741) | `runtime/transcript_repair.py` `repair_interactive_transcript` | rewritten |
| ↳ nested `_execute_with_interrupt` (2795) | `runtime/interactive_turn.py` `InteractiveTurnRunner` + `runtime/turn_execution.py` `await_turn_or_interrupt` + `runtime/execution_interrupt.py` `ExecutionInterruptController` | rewritten |
| `execute_single` (3162) | `runtime/single_execution.py` `run_single_execution` | rewritten; `main.execute_single` is a thin adapter |
| `main` (3469) | `main.py` | kept |

Compat wrappers also kept in `main.py`: `_apply_ui_mode_transition` and
`_next_shift_tab_state` delegate to `ui/interaction_controller.py`
(new mechanism introduced by the decomposition, no direct old-symbol
ancestor).

## `CommandProcessor` methods

`CommandProcessor` is now a facade composed of mixins:
`CommandModeMixin` (`ui/command_modes.py`), `CommandSessionMixin`
(`ui/command_sessions.py`), `CommandConfigMixin` (`ui/command_config.py`),
`CommandConfigDashboardMixin` (`ui/command_config_dashboard.py`),
`CommandAdminMixin` (`ui/command_admin.py`). Shared rendering policy lives in
`ui/dashboard_renderer.py`. Contract pinned by
`tests/test_command_processor_boundary.py`.

| Old method (line) | New location | Status |
|---|---|---|
| `_render_config_tree` (422) | `ui/dashboard_renderer.py` (delegating stub kept on the facade) | moved |
| `_print_wrapped_items` (428) | `ui/dashboard_renderer.py` (delegating stub kept) | moved |
| `_redact_value` (443) | `ui/dashboard_renderer.py` (delegating stub kept; redaction policy owned there) | moved |
| `__init__` (451) | `ui/command_processor.py` | rewritten (registry refresh, shortcut population) |
| `_populate_mode_shortcuts` (465) | `ui/command_processor.py` | kept |
| `_populate_skill_shortcuts` (473) | `ui/command_processor.py` | kept |
| `process_input` (481) | `ui/command_processor.py` (registry-driven; see `ui/command_registry.py`) | rewritten |
| `_split_mode_trailing` (551) | `ui/command_processor.py` | kept |
| `handle_command` (594) | `ui/command_processor.py` (`_dispatch_*` methods + `_execution_spec`) | rewritten |
| `_handle_mode` (655) | `ui/command_modes.py` | moved |
| `_list_modes` (823) | `ui/command_modes.py` | moved |
| `_mode_info` (912) | `ui/command_modes.py` | moved |
| `_save_transcript` (986) | `ui/command_sessions.py` (sanitization in `session_store.py`) | moved |
| `_get_status` (1028) | `ui/command_sessions.py` | moved |
| `_clear_context` (1077) | `ui/command_sessions.py` | moved |
| `_rename_session` (1083) | `ui/command_sessions.py` | moved |
| `_fork_session` (1113) | `ui/command_sessions.py` | moved |
| `_format_help` (1227) | `ui/command_sessions.py` | moved |
| `_display_bundle_name` (1273) | `ui/command_sessions.py` (typed protocol in the config mixins) | moved |
| `_render_simple_section` (1277) | `ui/command_config.py` (shared impl in `ui/dashboard_renderer.py`) | moved |
| `_render_hooks_section_v2` (1291) | `ui/command_config.py` | moved |
| `_render_behaviors_section_v2` (1311) | `ui/command_config.py` | moved |
| `_render_items_with_behavior_attribution` (1323) | `ui/command_config.py` | moved |
| `_render_context_section` (1336) | `ui/command_config.py` | moved |
| `_render_agents_section` (1348) | `ui/command_config.py` | moved |
| `_get_config_display` (1360) | `ui/command_config.py` | rewritten (routing documented in its docstring) |
| `_render_config_help` (1492) | `ui/command_config.py` | moved |
| `_render_providers_section_v2` (1537) | `ui/command_config.py` | moved |
| `_render_tools_section` (1549) | `ui/command_config.py` (shared impl in `ui/dashboard_renderer.py`) | moved |
| `_render_config_dashboard` (1561) | `ui/command_config.py` | moved |
| `_render_category_summary` (1618) | `ui/command_config.py` | moved |
| `_render_config_category` (1636) | `ui/command_config.py` | moved |
| `_render_config_dashboard_v2` (1693) | `ui/command_config_dashboard.py` | moved |
| `_render_config_item` (1863) | `ui/command_config_dashboard.py` (item rendering via `ui/item_renderer.py`) | rewritten |
| `_handle_config_toggle` (1911) | `ui/command_config_dashboard.py` | moved |
| `_handle_config_diff` (1970) | `ui/command_config_dashboard.py` | moved |
| `_handle_config_save` (1988) | `ui/command_config_dashboard.py` | moved |
| `_handle_config_set` (1997) | `ui/command_config_dashboard.py` | moved |
| `_render_legacy_config` (2022) | `ui/command_config_dashboard.py` | moved |
| `_render_bundle_config` (2044) | `ui/command_config_dashboard.py` | moved |
| `_list_tools` (2108) | `ui/command_admin.py` | moved |
| `_list_agents` (2126) | `ui/command_admin.py` | moved |
| `_manage_allowed_dirs` (2187) | `ui/command_admin.py` | moved |
| `_manage_denied_dirs` (2252) | `ui/command_admin.py` | moved |
| `_list_skills` (2317) | `ui/command_admin.py` | moved |
| `_load_skill` (2350) | `ui/command_admin.py` | moved |

## See also

- `docs/designs/interactive-tui-architecture.md` — the current architecture.
- `docs/decisions/ADR-0006-full-screen-pinned-interactive-shell.md` — why the
  interactive shell became a full-screen layered application.
