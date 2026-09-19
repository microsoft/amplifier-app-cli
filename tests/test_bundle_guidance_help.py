"""Regression coverage for behavior-first bundle guidance and add-command help."""

import importlib
import re
from pathlib import Path

from click.testing import CliRunner
import yaml

from amplifier_app_cli.lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES


ANCHORS_CANONICAL_BASE = (
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=bundles/anchors/bundle.md"
)
BEHAVIOR_URI = (
    "git+https://github.com/microsoft/amplifier-bundle-recipes@main"
    "#subdirectory=behaviors/recipes.yaml"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
GOAL_BATCH_SKILL = (
    REPO_ROOT / "amplifier_app_cli" / "data" / "skills" / "goal-batch" / "SKILL.md"
)
bundle_module = importlib.import_module("amplifier_app_cli.commands.bundle")


def test_goal_batch_description_is_bounded_and_keeps_routing_and_safety_guards():
    """The compact discovery description preserves the skill's routing contract."""
    _, frontmatter, body = GOAL_BATCH_SKILL.read_text(encoding="utf-8").split(
        "---", 2
    )
    description = yaml.safe_load(frontmatter)["description"]
    description_lower = description.lower()
    body_lower = body.lower()

    assert len(description) <= 400
    assert all(
        term in description_lower
        for term in (
            "independent",
            "lane",
            "approval",
            "launch",
            "verify",
            "landing",
            "mass-change",
            "ten-lane-highway",
            "bounded",
            "continuous",
            "run these in parallel",
            "goal-batch",
            "launch lanes for these",
            "work these n tasks simultaneously",
            "batch these as goals",
        )
    )
    assert all(
        term in body_lower
        for term in ("approval", "nothing launches", "never infer", "re-verify")
    )


def test_bundle_add_help_leads_with_behavior_then_root_registration():
    """Help keeps app behaviors distinct from registering selectable roots."""
    result = CliRunner().invoke(bundle_module.bundle, ["add", "--help"])

    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.split())
    assert "Use --app for a behavior" in help_text
    assert "Omit --app to register a selectable root" in help_text
    assert "behaviors/my-capability.yaml --app" in help_text
    assert (
        "github.com/org/my-host@main#subdirectory=bundle.md --name my-host"
        in help_text
    )
    assert "amplifier bundle use my-host" in help_text
    assert "Anchors is built in and the default root" in help_text
    assert "amplifier bundle use anchors" in help_text
    assert "use Anchors as its canonical base" in help_text
    assert "--name anchors" not in help_text
    assert help_text.index("behaviors/my-capability.yaml --app") < help_text.index(
        "github.com/org/my-host@main#subdirectory=bundle.md --name my-host"
    )


def test_anchors_is_builtin_and_cannot_be_removed(monkeypatch):
    """The documented built-in/default status matches the current CLI behavior."""
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: object())

    result = CliRunner().invoke(bundle_module.bundle, ["remove", "anchors"])

    assert "anchors" in WELL_KNOWN_BUNDLES
    assert result.exit_code == 1, result.output
    assert "Cannot remove well-known bundle 'anchors'" in result.output
    assert "built into amplifier" in result.output


def test_cli_docs_use_behavior_first_and_distinguish_user_roots_from_anchors():
    """Published guidance distinguishes user root registration from built-in Anchors."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    context_loading = (REPO_ROOT / "docs" / "CONTEXT_LOADING.md").read_text(
        encoding="utf-8"
    )

    assert BEHAVIOR_URI in readme
    assert "--app" in readme
    assert (
        "github.com/org/my-host@main#subdirectory=bundle.md' --name my-host"
        in readme
    )
    assert "amplifier bundle use my-host" in readme
    assert "Anchors is built in and the default root" in readme
    assert "`amplifier bundle use anchors` is sufficient" in readme
    assert "use Anchors as its canonical base" in readme
    assert "--name anchors" not in readme
    assert readme.index("## Add a Capability Behavior") < readme.index(
        "github.com/org/my-host@main#subdirectory=bundle.md' --name my-host"
    )

    assert "extends:" not in context_loading


def _complete_root_examples(markdown: str):
    """Yield parseable root frontmatter and body from Markdown example fences."""
    for fenced_source in re.findall(
        r"```markdown\n(.*?)```", markdown, flags=re.DOTALL
    ):
        for match in re.finditer(
            r"(?m)^---\n(.*?)\n---\n", fenced_source, flags=re.DOTALL
        ):
            frontmatter = yaml.safe_load(match.group(1))
            if isinstance(frontmatter, dict) and "bundle" in frontmatter:
                yield frontmatter, fenced_source[match.end() :]


def test_context_loading_root_examples_are_complete_anchors_compositions():
    """Every complete root example has valid metadata and preserves Anchors once."""
    context_loading = (REPO_ROOT / "docs" / "CONTEXT_LOADING.md").read_text(
        encoding="utf-8"
    )
    examples = list(_complete_root_examples(context_loading))

    assert examples
    for frontmatter, body in examples:
        assert frontmatter["bundle"]["name"]
        assert ANCHORS_CANONICAL_BASE in [
            include["bundle"] for include in frontmatter["includes"]
        ]
        assert body.strip()
        assert body.count("@anchors:context/system.md") == 1

    authoring_example = next(
        (frontmatter, body)
        for frontmatter, body in examples
        if frontmatter["bundle"]["name"] == "my-host"
    )
    _, body = authoring_example
    assert "@foundation:context/shared/common-agent-base.md" in body
    assert "@foundation:context/IMPLEMENTATION_PHILOSOPHY.md" in body
