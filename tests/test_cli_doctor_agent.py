"""CLI-only doctor registration and on-demand composition, without preparation."""

from pathlib import Path

import pytest
import yaml
from amplifier_foundation import load_bundle


ROOT = Path(__file__).resolve().parents[1]


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    header, body = text.split("---", 2)[1:]
    return yaml.safe_load(header), body


def test_cli_doctor_routing_and_existing_skill_boundary():
    metadata, body = _frontmatter(ROOT / "agents" / "cli-doctor.md")
    description = metadata["meta"]["description"]
    assert metadata["meta"]["name"] == "cli-doctor"
    assert metadata["model_role"] == "general"
    assert len(description) <= 400
    assert "USE WHEN" in description and "DO NOT USE" in description
    assert "app-cli:cli-expert" in description
    assert "amplifier-config" in body and "`load_skill`" in body
    assert "child session" in body and "otherwise mark it unknown" in body
    assert "Exit 0 means collection completed, not healthy" in body
    assert "no repair verdict from passive collection" in body
    assert "not that nothing needs fixing" in body
    assert "does not authorize repair" in body
    assert "Success criteria" in body
    assert "amplifier doctor --format json" in body
    skill = ROOT / "amplifier_app_cli" / "data" / "skills" / "amplifier-config" / "SKILL.md"
    assert skill.is_file()
    assert not (ROOT / "amplifier_app_cli" / "data" / "skills" / "cli-doctor").exists()


@pytest.mark.asyncio
async def test_cli_behavior_adds_agent_without_replacing_root(tmp_path):
    # Local-only parsing/composition: do not prepare/install/mount anything.
    root_file = tmp_path / "bundle.md"
    root_file.write_text(
        "---\nbundle:\n  name: synthetic-root\n  version: 0.1.0\n"
        "providers:\n  - module: provider-synthetic\n"
        "    config:\n      priority: 7\n"
        "session:\n  synthetic: retained\n"
        "---\nROOT-INSTRUCTION-MARKER\n",
        encoding="utf-8",
    )
    root = await load_bundle(root_file.as_uri())
    behavior = await load_bundle((ROOT / "behaviors" / "cli-expertise.yaml").as_uri())
    composed = root.compose(behavior)
    assert composed.instruction == root.instruction
    assert composed.providers == root.providers
    assert composed.session == root.session
    assert "app-cli:cli-doctor" in composed.agents
    assert "Amplifier CLI Doctor" not in (composed.instruction or "")
    parsed = yaml.safe_load((ROOT / "behaviors" / "cli-expertise.yaml").read_text())
    assert parsed["agents"]["include"] == ["app-cli:cli-expert", "app-cli:cli-doctor"]
    assert parsed["context"]["include"] == ["app-cli:context/cli-awareness.md"]