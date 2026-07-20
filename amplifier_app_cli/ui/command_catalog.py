"""Canonical built-in slash-command catalog."""

from __future__ import annotations

from .command_registry import CommandAvailability
from .command_registry import CommandOwner
from .command_registry import CommandRegistry
from .command_registry import CommandSource
from .command_registry import CommandSpec
from .command_registry import CompletionProvider
from .command_registry import CompletionSpec
from .command_registry import default_phase_for


def _spec(
    name: str,
    description: str,
    action: str,
    owner: CommandOwner,
    handler: str,
    *,
    aliases: tuple[str, ...] = (),
    completion: CompletionSpec | None = None,
    availability: CommandAvailability | None = None,
) -> CommandSpec:
    return CommandSpec(
        name,
        description,
        default_phase_for(name),
        CommandSource.BUILTIN,
        action,
        owner,
        handler,
        aliases=aliases,
        availability=availability
        or (
            CommandAvailability.INTERACTIVE
            if owner is CommandOwner.PROCESSOR
            else CommandAvailability.SESSION
        ),
        completion=completion,
    )


_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_CONFIG = (
    "show",
    "context",
    "tools",
    "hooks",
    "providers",
    "agents",
    "behaviors",
    "diff",
    "save",
    "set",
)

BUILTIN_COMMAND_SPECS = (
    _spec(
        "/init",
        "Scaffold project memory without overwriting it",
        "session_ui",
        CommandOwner.CORE,
        "_init",
    ),
    _spec(
        "/permissions",
        "Inspect or select the active trust preset",
        "session_ui",
        CommandOwner.SESSION,
        "_permissions_result",
        completion=CompletionSpec(("show", "preset", "set")),
    ),
    _spec(
        "/mcp",
        "List or edit project MCP servers",
        "session_ui",
        CommandOwner.MCP,
        "execute",
        completion=CompletionSpec(("list", "add", "remove", "reload")),
        availability=CommandAvailability.CAPABILITY,
    ),
    _spec(
        "/mode",
        "Inspect or switch mode (chat, plan, brainstorm, build, auto)",
        "handle_mode",
        CommandOwner.PROCESSOR,
        "_dispatch_mode_command",
        completion=CompletionSpec(provider=CompletionProvider.MODE),
    ),
    _spec(
        "/modes",
        "List available modes",
        "list_modes",
        CommandOwner.PROCESSOR,
        "_dispatch_modes_command",
    ),
    _spec(
        "/model",
        "Inspect or switch the live provider model",
        "session_ui",
        CommandOwner.CORE,
        "_model",
        completion=CompletionSpec(provider=CompletionProvider.MODEL),
    ),
    _spec(
        "/effort",
        "Inspect or set live reasoning effort",
        "session_ui",
        CommandOwner.CORE,
        "_effort",
        aliases=("/strength",),
        completion=CompletionSpec(_EFFORTS),
    ),
    _spec(
        "/btw",
        "Ask a side question without conversation context",
        "session_ui",
        CommandOwner.CORE,
        "_btw",
    ),
    _spec(
        "/save",
        "Save conversation transcript",
        "save_transcript",
        CommandOwner.PROCESSOR,
        "_dispatch_save_command",
    ),
    _spec(
        "/status",
        "Show session status",
        "show_status",
        CommandOwner.PROCESSOR,
        "_dispatch_status_command",
    ),
    _spec(
        "/context",
        "Show context usage and cache telemetry",
        "session_ui",
        CommandOwner.SESSION,
        "_context_result",
    ),
    _spec(
        "/compact",
        "Request context compaction with an optional focus",
        "session_ui",
        CommandOwner.CORE,
        "_compact",
    ),
    _spec(
        "/answer",
        "Answer deferred decisions in one batch",
        "session_ui",
        CommandOwner.SESSION,
        "_answer_result",
    ),
    _spec(
        "/clear",
        "Clear conversation context and optionally name it",
        "session_ui",
        CommandOwner.CORE,
        "_clear",
    ),
    _spec(
        "/resume",
        "List or resolve resumable sessions",
        "session_ui",
        CommandOwner.CORE,
        "_resume",
    ),
    _spec(
        "/branch",
        "Create a resumable copy of this session",
        "session_ui",
        CommandOwner.CORE,
        "_branch",
    ),
    _spec(
        "/export",
        "Export this session as Markdown or JSON",
        "session_ui",
        CommandOwner.CORE,
        "_export",
        completion=CompletionSpec(("markdown", "json")),
    ),
    _spec(
        "/help",
        "Show available commands",
        "show_help",
        CommandOwner.PROCESSOR,
        "_dispatch_help_command",
    ),
    _spec(
        "/config",
        "Live session config \u2014 /config [category] [disable|enable name]",
        "show_config",
        CommandOwner.PROCESSOR,
        "_dispatch_config_command",
        completion=CompletionSpec(_CONFIG),
    ),
    _spec(
        "/tools",
        "List available tools",
        "list_tools",
        CommandOwner.PROCESSOR,
        "_dispatch_tools_command",
    ),
    _spec(
        "/agents",
        "List available agents",
        "list_agents",
        CommandOwner.PROCESSOR,
        "_dispatch_agents_command",
    ),
    _spec(
        "/tasks",
        "Toggle live parent and child agent lanes",
        "session_ui",
        CommandOwner.SESSION,
        "_tasks_result",
    ),
    _spec(
        "/background",
        "Detach to a shell while the current session keeps running",
        "session_ui",
        CommandOwner.CORE,
        "_background",
    ),
    _spec(
        "/allowed-dirs",
        "Manage allowed write directories",
        "manage_allowed_dirs",
        CommandOwner.PROCESSOR,
        "_dispatch_allowed_dirs_command",
    ),
    _spec(
        "/denied-dirs",
        "Manage denied write directories",
        "manage_denied_dirs",
        CommandOwner.PROCESSOR,
        "_dispatch_denied_dirs_command",
    ),
    _spec(
        "/rename",
        "Rename current session",
        "rename_session",
        CommandOwner.PROCESSOR,
        "_dispatch_rename_command",
    ),
    _spec(
        "/fork",
        "Run a directive in a background session copy",
        "session_ui",
        CommandOwner.CORE,
        "_fork",
    ),
    _spec(
        "/diff",
        "Show the current or staged working-tree diff summary",
        "session_ui",
        CommandOwner.SESSION,
        "_diff_result",
        completion=CompletionSpec(("staged", "full")),
    ),
    _spec(
        "/review",
        "Review a scope without modifying files",
        "session_ui",
        CommandOwner.SESSION,
        "_review_result",
    ),
    _spec(
        "/ledger",
        "Show session spend versus outcome",
        "session_ui",
        CommandOwner.SESSION,
        "_ledger_result",
    ),
    _spec(
        "/rewind",
        "Show addressable turn checkpoints",
        "session_ui",
        CommandOwner.SESSION,
        "_rewind_result",
    ),
    _spec(
        "/doctor",
        "Check interactive session capabilities",
        "session_ui",
        CommandOwner.SESSION,
        "_doctor_result",
    ),
    _spec(
        "/improve",
        "Propose evidence-backed configuration improvements",
        "session_ui",
        CommandOwner.SESSION,
        "_improve_result",
    ),
    _spec(
        "/feedback",
        "Open a prefilled CLI feedback issue",
        "session_ui",
        CommandOwner.CORE,
        "_feedback",
    ),
    _spec(
        "/skills",
        "List available skills",
        "list_skills",
        CommandOwner.PROCESSOR,
        "_dispatch_skills_command",
    ),
    _spec(
        "/skill",
        "Load a skill (e.g., /skill simplify)",
        "load_skill",
        CommandOwner.PROCESSOR,
        "_dispatch_skill_command",
        completion=CompletionSpec(provider=CompletionProvider.SKILL),
    ),
)

BUILTIN_COMMAND_REGISTRY = CommandRegistry(BUILTIN_COMMAND_SPECS)

__all__ = ["BUILTIN_COMMAND_REGISTRY", "BUILTIN_COMMAND_SPECS"]
