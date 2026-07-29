import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from amplifier_core import ToolResult
from prompt_toolkit.document import Document

from amplifier_app_cli.main import CommandProcessor
from amplifier_app_cli.ui.mcp_commands import McpCommandService
from amplifier_app_cli.ui.repl import SlashCommandCompleter


class _Prompt:
    server_name = "github"
    prompt_name = "triage"
    description = "Triage one issue"
    input_schema = {
        "type": "object",
        "properties": {"issue": {"type": "string"}},
        "required": ["issue"],
    }

    def __init__(self):
        self.input = None

    async def execute(self, input):
        self.input = input
        return ToolResult(
            success=True,
            output={"messages": f"[user]\nTriage {input['issue']}"},
        )


def _service(tmp_path):
    prompt = _Prompt()
    coordinator = SimpleNamespace(
        get=lambda name: {"prompt": prompt} if name == "tools" else None
    )
    return McpCommandService(coordinator, tmp_path), prompt


@pytest.mark.asyncio
async def test_mounted_mcp_prompt_is_discoverable_and_executable(tmp_path):
    service, prompt = _service(tmp_path)

    assert service.palette_prompts == (("github", "triage", "Triage one issue"),)
    result = await service.execute("/github:triage", "#42")

    assert result.prompt == "[user]\nTriage #42"
    assert prompt.input == {"issue": "#42"}


@pytest.mark.asyncio
async def test_required_mcp_prompt_argument_is_enforced(tmp_path):
    service, _ = _service(tmp_path)

    result = await service.execute("/github:triage", "")

    assert result.text == "Required MCP prompt arguments: issue"


@pytest.mark.asyncio
async def test_mcp_add_and_remove_update_project_config(tmp_path):
    service, _ = _service(tmp_path)

    added = await service.execute("/mcp", "add docs uvx docs-server --stdio")
    path = tmp_path / ".amplifier" / "mcp.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    removed = await service.execute("/mcp", "remove docs")

    assert added.transient is True
    assert config["mcpServers"]["docs"] == {
        "command": "uvx",
        "args": ["docs-server", "--stdio"],
    }
    assert removed.transient is True
    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"] == {}


@pytest.mark.asyncio
async def test_mcp_reload_reports_real_runtime_limitation(tmp_path):
    service, _ = _service(tmp_path)

    result = await service.execute("/mcp", "reload")

    assert "hot reload is not exposed" in result.text


def test_mcp_prompt_is_tagged_in_palette_and_routes_as_session_command(tmp_path):
    service, _ = _service(tmp_path)
    session = MagicMock()
    session.coordinator.session_state = {"active_mode": None}
    session.coordinator.get_capability.side_effect = lambda name: (
        service if name == "ui.session_commands" else None
    )
    processor = CommandProcessor(session, "foundation")
    completer = SlashCommandCompleter(
        processor.COMMANDS,
        mcp_prompts=service.palette_prompts,
    )

    action, data = processor.process_input("/github:triage #42")
    completions = list(completer.get_completions(Document("/github:tri"), None))

    assert action == "session_ui"
    assert data == {"command": "/github:triage", "args": "#42"}
    assert (
        str(completions[0].display_meta)
        == "FormattedText([('', 'mcp · Triage one issue')])"
    )


def test_normative_core_commands_are_registered_for_session_dispatch():
    expected = {
        "/init",
        "/mcp",
        "/model",
        "/effort",
        "/btw",
        "/compact",
        "/fork",
        "/background",
        "/clear",
        "/resume",
        "/branch",
        "/export",
        "/feedback",
    }

    assert expected <= CommandProcessor.COMMANDS.keys()
    assert {CommandProcessor.COMMANDS[command]["action"] for command in expected} == {
        "session_ui"
    }
