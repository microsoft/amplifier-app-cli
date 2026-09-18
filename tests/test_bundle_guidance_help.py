"""Regression coverage for behavior-first bundle guidance and add-command help."""

from pathlib import Path

from click.testing import CliRunner

from amplifier_app_cli.commands.bundle import bundle


ANCHORS_URI = (
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=bundles/anchors/bundle.md"
)
BEHAVIOR_URI = (
    "git+https://github.com/microsoft/amplifier-bundle-recipes@main"
    "#subdirectory=behaviors/recipes.yaml"
)
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_bundle_add_help_leads_with_behavior_then_root_registration():
    """Help keeps app behaviors distinct from registering selectable roots."""
    result = CliRunner().invoke(bundle, ["add", "--help"])

    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.split())
    assert "Use --app for a behavior" in help_text
    assert "Omit --app to register a selectable root" in help_text
    assert "behaviors/my-capability.yaml --app" in help_text
    assert ANCHORS_URI in help_text
    assert help_text.index("behaviors/my-capability.yaml --app") < help_text.index(
        ANCHORS_URI
    )


def test_cli_docs_use_behavior_first_and_anchors_for_new_roots():
    """Published CLI examples preserve Anchors when a new root has its own body."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    context_loading = (REPO_ROOT / "docs" / "CONTEXT_LOADING.md").read_text(
        encoding="utf-8"
    )

    assert BEHAVIOR_URI in readme
    assert "--app" in readme
    assert ANCHORS_URI in readme
    assert readme.index("## Add a Capability Behavior") < readme.index(ANCHORS_URI)

    assert "extends:" not in context_loading
    assert ANCHORS_URI in context_loading
    assert context_loading.count(ANCHORS_URI) == 8
    assert context_loading.count("@anchors:context/system.md") >= 8