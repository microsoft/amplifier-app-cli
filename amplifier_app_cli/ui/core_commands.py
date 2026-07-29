"""Runtime-backed implementations for the normative interactive command set."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from amplifier_core.message_models import ChatRequest, Message

from amplifier_app_cli.session_store import SessionStore, sanitize_message

from .command_catalog import BUILTIN_COMMAND_REGISTRY
from .command_registry import CommandOwner

logger = logging.getLogger(__name__)

_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
_EFFORT_ALIASES = {"max": "xhigh"}
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,49}$")
_MAX_EXPORT_MESSAGES = 100_000
_FEEDBACK_URL = "https://github.com/microsoft/amplifier-app-cli/issues/new"


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    text: str = ""
    prompt: str = ""
    transient: bool = False

    def __post_init__(self) -> None:
        if not self.text and not self.prompt:
            raise ValueError("command outcome cannot be empty")


class CoreCommandService:
    """Execute commands against mounted coordinator and session mechanisms."""

    COMMANDS = BUILTIN_COMMAND_REGISTRY.names_for_owner(CommandOwner.CORE)

    def __init__(
        self,
        *,
        session: Any | None,
        coordinator: Any | None,
        session_id: str,
        bundle_name: str,
        cwd: Path,
        store: SessionStore | None = None,
    ) -> None:
        self._session = session
        self._coordinator = coordinator
        self._session_id = session_id
        self._bundle_name = bundle_name.removeprefix("bundle:") or "unknown"
        self._cwd = cwd.resolve()
        self._store = store or SessionStore()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._model_names = self._current_model_names()

    @property
    def model_names(self) -> tuple[str, ...]:
        """Models known without requiring a provider request on every keystroke."""
        return self._model_names

    async def execute(self, command: str, args: str) -> CommandOutcome:
        spec = BUILTIN_COMMAND_REGISTRY.require(command)
        if spec.owner is not CommandOwner.CORE:
            raise KeyError(command)
        handler = getattr(self, spec.handler)
        result = handler(args.strip())
        return await result if asyncio.iscoroutine(result) else result

    def _init(self, args: str) -> CommandOutcome:
        if args:
            return CommandOutcome("Usage: /init")
        memory_file = self._cwd / "AGENTS.md"
        if memory_file.exists() or memory_file.is_symlink():
            return CommandOutcome(f"Project memory already exists: {memory_file}")
        body = (
            "# Project Memory\n\n"
            "## Purpose\n\n"
            "Describe what this project does and who it serves.\n\n"
            "## Commands\n\n"
            "Record the build, test, lint, and run commands.\n\n"
            "## Conventions\n\n"
            "Record repository-specific engineering and review rules.\n"
        )
        try:
            with memory_file.open("x", encoding="utf-8") as handle:
                handle.write(body)
        except FileExistsError:
            return CommandOutcome(f"Project memory already exists: {memory_file}")
        except OSError as error:
            return CommandOutcome(f"Could not initialize project memory: {error}")
        return CommandOutcome(f"Project memory initialized: {memory_file}")

    async def _model(self, args: str) -> CommandOutcome:
        providers = self._mounted("providers")
        if not isinstance(providers, dict) or not providers:
            return CommandOutcome("No model providers are mounted in this session.")
        if not args or args == "list":
            lines = ["Active model"]
            for name, provider in providers.items():
                model = getattr(provider, "default_model", None) or "provider default"
                lines.append(f"  {name} · {model}")
                advertised = await _advertised_models(provider)
                if advertised:
                    self._remember_models(advertised)
                    lines.append(f"  available · {', '.join(advertised)}")
            lines.append("Set: `/model <model>` | `/model <provider> <model>`")
            return CommandOutcome("\n".join(lines))

        parts = args.split(maxsplit=1)
        if len(parts) == 2 and parts[0] in providers:
            provider_name, model = parts
        else:
            provider_name = self._active_provider_name(providers)
            model = args
            if not provider_name:
                return CommandOutcome(
                    "Multiple providers are mounted. Use `/model <provider> <model>`."
                )
        model = _clean_value(model, 200)
        if not model:
            return CommandOutcome("Model name cannot be empty.")
        provider = providers[provider_name]
        self._remember_models((model,))
        setattr(provider, "default_model", model)
        config = getattr(provider, "config", None)
        if isinstance(config, dict):
            config["default_model"] = model
        self._set_session_state(
            "ui.model_override", {"provider": provider_name, "model": model}
        )
        profile = self._session_state().get("ui.mode_profile")
        if isinstance(profile, dict):
            profile.update({"provider": provider_name, "model": model})
        self._persist_metadata({"model": model, "provider": provider_name})
        return CommandOutcome(f"Model: {provider_name} · {model}", transient=True)

    def _effort(self, args: str) -> CommandOutcome:
        orchestrator = self._mounted("orchestrator")
        config = getattr(orchestrator, "config", None)
        if not isinstance(config, dict):
            return CommandOutcome(
                "The mounted orchestrator has no mutable reasoning-effort configuration."
            )
        if not args:
            current = config.get("reasoning_effort") or "provider default"
            return CommandOutcome(
                f"Reasoning effort: {current}\nUsage: `/effort <{'|'.join(_EFFORTS)}>`"
            )
        effort = _EFFORT_ALIASES.get(args.lower(), args.lower())
        if effort not in _EFFORTS:
            return CommandOutcome(
                f"Unknown strength. Choose: {', '.join(_EFFORTS)} (max is an alias for xhigh)."
            )
        config["reasoning_effort"] = effort
        self._set_session_state("ui.effort_override", effort)
        profile = self._session_state().get("ui.mode_profile")
        if isinstance(profile, dict):
            profile["reasoning_effort"] = effort
        self._persist_metadata({"reasoning_effort": effort})
        return CommandOutcome(f"Reasoning effort: {effort}", transient=True)

    async def _btw(self, args: str) -> CommandOutcome:
        if not args:
            return CommandOutcome("Usage: `/btw <side question>`")
        providers = self._mounted("providers")
        if not isinstance(providers, dict) or not providers:
            return CommandOutcome("No provider is available for a side question.")
        provider_name = self._active_provider_name(providers) or next(iter(providers))
        provider = providers[provider_name]
        if not hasattr(provider, "complete"):
            return CommandOutcome(f"Provider {provider_name} cannot run completions.")
        request = ChatRequest(
            messages=[Message(role="user", content=args)],
            reasoning_effort="low",
            stream=False,
            metadata={"amplifier_command": "btw", "context_messages": 0},
        )
        try:
            response = await provider.complete(request)
        except Exception as error:
            logger.debug("Side question failed", exc_info=True)
            return CommandOutcome(f"Side question failed: {error}")
        answer = _response_text(response)
        return CommandOutcome(answer or "The provider returned no text response.")

    async def _compact(self, args: str) -> CommandOutcome:
        context = self._mounted("context")
        if context is None or not hasattr(context, "compact"):
            return CommandOutcome(
                "Manual compaction is unavailable: the mounted context has no compact capability."
            )
        before = await _message_count(context)
        try:
            if args:
                try:
                    result = context.compact(focus=args)
                    if asyncio.iscoroutine(result):
                        result = await result
                except TypeError:
                    result = None
            else:
                result = context.compact()
                if asyncio.iscoroutine(result):
                    result = await result
        except Exception as error:
            return CommandOutcome(f"Context compaction failed: {error}")
        after = await _message_count(context)
        if before is not None and after is not None and after < before:
            return CommandOutcome(
                f"Context compacted · {before - after} messages removed · {after} retained"
            )
        if result:
            return CommandOutcome(f"Context compacted: {result}")
        persistent = await self._persistent_compact(context, focus=args)
        if persistent is not None:
            removed, retained = persistent
            return CommandOutcome(
                f"Context compacted persistently · {removed} messages summarized · "
                f"{retained} retained"
            )
        return CommandOutcome(
            "The context backend made no persistent change. This backend compacts "
            "ephemerally on provider requests; forced /compact is not supported."
        )

    async def _persistent_compact(
        self, context: Any, *, focus: str
    ) -> tuple[int, int] | None:
        if not hasattr(context, "get_messages") or not hasattr(context, "set_messages"):
            return None
        messages = await context.get_messages()
        if len(messages) <= 6:
            return None
        providers = self._mounted("providers")
        if not isinstance(providers, dict) or not providers:
            return None
        provider_name = self._active_provider_name(providers) or next(iter(providers))
        provider = providers[provider_name]
        if not hasattr(provider, "complete"):
            return None
        retained = messages[-4:]
        source = json.dumps(
            [sanitize_message(message) for message in messages[:-4]],
            ensure_ascii=False,
            default=str,
        )[:50_000]
        focus_line = f" Preserve details relevant to: {focus}." if focus else ""
        request = ChatRequest(
            messages=[
                Message(
                    role="user",
                    content=(
                        "Summarize this earlier conversation for durable context."
                        f"{focus_line}\n\n{source}"
                    ),
                )
            ],
            reasoning_effort="low",
            stream=False,
            metadata={"amplifier_command": "compact"},
        )
        response = await provider.complete(request)
        summary = _response_text(response)
        if not summary:
            return None
        replacement = [
            {
                "role": "system",
                "content": f"Compacted conversation summary:\n{summary}",
            },
            *retained,
        ]
        result = context.set_messages(replacement)
        if asyncio.iscoroutine(result):
            await result
        self._persist_metadata(
            {
                "compacted_at": datetime.now(UTC).isoformat(),
                "compaction_focus": focus,
            }
        )
        return len(messages) - len(retained), len(replacement)

    async def _fork(self, args: str) -> CommandOutcome:
        if not args:
            return CommandOutcome("Usage: `/fork <directive>`")
        if self._session is None or self._coordinator is None:
            return CommandOutcome("Background session spawning is unavailable.")
        spawn = self._capability("session.spawn")
        if not callable(spawn):
            return CommandOutcome(
                "Background session spawning is unavailable: session.spawn is not registered."
            )
        context = self._mounted("context")
        messages = (
            await context.get_messages() if hasattr(context, "get_messages") else []
        )
        child_id = f"{self._session_id}-{uuid4().hex[:16]}_self"
        effective = _fork_instruction(messages, args)
        coordinator_config = getattr(self._coordinator, "config", None)
        agents = (
            coordinator_config.get("agents", {})
            if isinstance(coordinator_config, dict)
            else {}
        )
        current_depth = self._capability("self_delegation_depth") or 0
        spawn_async = cast(Callable[..., Coroutine[Any, Any, Any]], spawn)
        task = asyncio.create_task(
            spawn_async(
                agent_name="self",
                instruction=effective,
                parent_session=self._session,
                agent_configs=agents if isinstance(agents, dict) else {},
                sub_session_id=child_id,
                parent_messages=messages,
                self_delegation_depth=int(current_depth) + 1,
                session_metadata={"agent_name": "self", "directive": args[:500]},
            ),
            name=f"amplifier-fork-{child_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._fork_done)
        return CommandOutcome(
            f"Fork started · {child_id[:18]} · /tasks to follow", transient=True
        )

    def _fork_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("Background fork failed")

    def _background(self, args: str) -> CommandOutcome:
        if args:
            return CommandOutcome("Usage: /background")
        background = self._capability("ui.background")
        if not callable(background):
            return CommandOutcome(
                "Background notifications are unavailable in this terminal."
            )
        detached = background()
        if detached is False:
            return CommandOutcome(
                "Completion notification armed; terminal detach requires the active TUI.",
                transient=True,
            )
        return CommandOutcome(
            "Session detached to a shell · exit that shell to return",
            transient=True,
        )

    async def _clear(self, args: str) -> CommandOutcome:
        if args and not _NAME_PATTERN.fullmatch(args):
            return CommandOutcome(
                "Invalid session name. Use letters, numbers, spaces, dot, dash, or underscore."
            )
        context = self._mounted("context")
        if context is None or not hasattr(context, "clear"):
            return CommandOutcome("The mounted context cannot be cleared.")
        count = await _message_count(context)
        await context.clear()
        updates: dict[str, Any] = {"cleared_at": datetime.now(UTC).isoformat()}
        if args:
            updates["name"] = args
        self._persist_metadata(updates)
        suffix = f" · session named {args}" if args else ""
        return CommandOutcome(
            f"Context cleared · {count or 0} messages removed{suffix}"
        )

    def _resume(self, args: str) -> CommandOutcome:
        if args:
            try:
                session_id = self._store.find_session(args)
            except (FileNotFoundError, ValueError) as error:
                return CommandOutcome(str(error))
            resume = self._capability("ui.resume")
            if not callable(resume):
                return CommandOutcome(
                    "In-place resume is unavailable in this terminal. Run: "
                    f"amplifier session resume {session_id}"
                )
            resume(session_id)
            return CommandOutcome(
                f"Switching to session {session_id[:12]}", transient=True
            )
        sessions = [
            item for item in self._store.list_sessions() if item != self._session_id
        ]
        if not sessions:
            return CommandOutcome(
                "No other resumable sessions were found for this project."
            )
        lines = ["Recent sessions"]
        for session_id in sessions[:8]:
            try:
                name = self._store.get_metadata(session_id).get("name") or "unnamed"
            except (FileNotFoundError, OSError, ValueError):
                name = "unnamed"
            lines.append(f"{session_id[:12]} · {name}")
        lines.append("Usage: `/resume <id-or-prefix>`")
        return CommandOutcome("\n".join(lines))

    async def _branch(self, args: str) -> CommandOutcome:
        if args and not _NAME_PATTERN.fullmatch(args):
            return CommandOutcome(
                "Invalid branch name. Use letters, numbers, spaces, dot, dash, or underscore."
            )
        context = self._mounted("context")
        if context is None or not hasattr(context, "get_messages"):
            return CommandOutcome(
                "Cannot branch: the mounted context cannot export messages."
            )
        messages = await context.get_messages()
        branch_id = str(uuid4())
        metadata = self._metadata()
        metadata.update(
            {
                "session_id": branch_id,
                "parent_id": self._session_id,
                "branched_at": datetime.now(UTC).isoformat(),
                "bundle": metadata.get("bundle") or self._bundle_name,
                "name": args or f"branch-{branch_id[:8]}",
            }
        )
        if self._session is not None:
            metadata.setdefault("config", getattr(self._session, "config", {}))
        try:
            self._store.save(branch_id, messages, metadata)
        except (OSError, ValueError) as error:
            return CommandOutcome(f"Could not create branch: {error}")
        return CommandOutcome(
            f"Branch created · {branch_id[:12]} · resume with: "
            f"amplifier session resume {branch_id}"
        )

    async def _export(self, args: str) -> CommandOutcome:
        context = self._mounted("context")
        if context is None or not hasattr(context, "get_messages"):
            return CommandOutcome(
                "Cannot export: the mounted context cannot read messages."
            )
        parts = args.split(maxsplit=1) if args else []
        export_format = parts[0].lower() if parts else "markdown"
        if export_format == "md":
            export_format = "markdown"
        if export_format not in {"markdown", "json"}:
            return CommandOutcome("Usage: /export [markdown|json] [filename]")
        suffix = ".md" if export_format == "markdown" else ".json"
        filename = (
            parts[1] if len(parts) == 2 else f"export-{self._session_id[:8]}{suffix}"
        )
        if Path(filename).name != filename or not filename.endswith(suffix):
            return CommandOutcome(f"Export filename must be a local {suffix} basename.")
        session_dir = (self._store.base_dir / self._session_id).resolve()
        export_dir = session_dir / "exports"
        messages = (await context.get_messages())[:_MAX_EXPORT_MESSAGES]
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            if not export_dir.resolve().is_relative_to(session_dir):
                return CommandOutcome("Export directory resolves outside the session.")
            path = export_dir / filename
            if path.is_symlink():
                return CommandOutcome("Refusing to overwrite a symlinked export file.")
            if export_format == "json":
                payload = [sanitize_message(message) for message in messages]
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            else:
                path.write_text(_markdown_export(messages), encoding="utf-8")
        except (OSError, TypeError, ValueError) as error:
            return CommandOutcome(f"Could not export session: {error}")
        return CommandOutcome(f"Session exported: {path}")

    def _feedback(self, args: str) -> CommandOutcome:
        title = quote(f"CLI feedback: {args[:80]}" if args else "CLI feedback")
        body = quote(
            f"Session: {self._session_id[:8]}\nBundle: {self._bundle_name}\n\n"
            f"Feedback:\n{args or '[describe what happened and what you expected]'}"
        )
        return CommandOutcome(
            f"Open feedback issue: {_FEEDBACK_URL}?title={title}&body={body}"
        )

    def _active_provider_name(self, providers: dict[str, Any]) -> str:
        override = self._session_state().get("ui.model_override")
        if isinstance(override, dict) and override.get("provider") in providers:
            return str(override["provider"])
        profile = self._session_state().get("ui.mode_profile")
        if isinstance(profile, dict) and profile.get("provider") in providers:
            return str(profile["provider"])
        return next(iter(providers)) if len(providers) == 1 else ""

    def _current_model_names(self) -> tuple[str, ...]:
        providers = self._mounted("providers")
        if not isinstance(providers, dict):
            return ()
        return tuple(
            dict.fromkeys(
                str(getattr(provider, "default_model", "") or "")
                for provider in providers.values()
                if getattr(provider, "default_model", None)
            )
        )

    def _remember_models(self, models: tuple[str, ...]) -> None:
        self._model_names = tuple(dict.fromkeys((*self._model_names, *models)))[:64]

    def _mounted(self, name: str) -> Any:
        return self._coordinator.get(name) if self._coordinator is not None else None

    def _capability(self, name: str) -> Any:
        getter = getattr(self._coordinator, "get_capability", None)
        return getter(name) if callable(getter) else None

    def _session_state(self) -> dict[str, Any]:
        state = getattr(self._coordinator, "session_state", None)
        return state if isinstance(state, dict) else {}

    def _set_session_state(self, key: str, value: Any) -> None:
        state = getattr(self._coordinator, "session_state", None)
        if isinstance(state, dict):
            state[key] = value

    def _metadata(self) -> dict[str, Any]:
        try:
            return dict(self._store.get_metadata(self._session_id))
        except (FileNotFoundError, OSError, ValueError):
            return {"session_id": self._session_id, "bundle": self._bundle_name}

    def _persist_metadata(self, updates: dict[str, Any]) -> None:
        try:
            if self._store.exists(self._session_id):
                self._store.update_metadata(self._session_id, updates)
        except (OSError, ValueError):
            logger.debug("Could not persist interactive command state", exc_info=True)


async def _advertised_models(provider: Any) -> tuple[str, ...]:
    """Return a bounded, display-safe model list from the mounted provider."""
    list_models = getattr(provider, "list_models", None)
    if not callable(list_models):
        return ()
    try:
        models = list_models()
        if asyncio.iscoroutine(models):
            models = await models
    except Exception:
        logger.debug("Could not list models from mounted provider", exc_info=True)
        return ()
    if not isinstance(models, Iterable):
        return ()

    names: list[str] = []
    for model in models or ():
        if isinstance(model, dict):
            raw_name = model.get("id") or model.get("name")
        else:
            raw_name = getattr(model, "id", None) or getattr(model, "name", None)
            if raw_name is None and isinstance(model, str):
                raw_name = model
        name = _clean_value(str(raw_name or ""), 100)
        if name and name not in names:
            names.append(name)
        if len(names) == 12:
            break
    return tuple(names)


async def _message_count(context: Any) -> int | None:
    if not hasattr(context, "get_messages"):
        return None
    messages = await context.get_messages()
    return len(messages)


def _clean_value(value: str, limit: int) -> str:
    return "".join(character for character in value.strip() if ord(character) >= 32)[
        :limit
    ]


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", ()) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts).strip()


def _fork_instruction(messages: list[Any], directive: str) -> str:
    payload = [sanitize_message(message) for message in messages]
    context = json.dumps(payload, ensure_ascii=False, default=str)
    return (
        "The following JSON is a full copy of the parent conversation. Treat it as "
        f"prior context, then complete the directive.\n\n{context}\n\n[DIRECTIVE]\n{directive}"
    )


def _markdown_export(messages: list[Any]) -> str:
    lines = ["# Amplifier Session Export", ""]
    for raw in messages:
        message = sanitize_message(raw)
        role = str(message.get("role") or "message").title()
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        lines.extend((f"## {role}", "", content, ""))
    return "\n".join(lines)


__all__ = ["CommandOutcome", "CoreCommandService"]
