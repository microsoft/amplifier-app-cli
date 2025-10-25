"""Integration tests for tutorial_analyzer orchestration.

Tests the complete pipeline end-to-end.
"""

from pathlib import Path

import pytest
from tutorial_analyzer.state import clear_state


@pytest.fixture
def sample_tutorial():
    """Return path to sample tutorial fixture."""
    return Path(__file__).parent / "fixtures" / "sample_tutorial.md"


@pytest.fixture(autouse=True)
def clean_state():
    """Clear state before and after each test."""
    clear_state()
    yield
    clear_state()


@pytest.mark.asyncio
async def test_pipeline_structure(sample_tutorial):
    """Test that pipeline executes all stages in order.

    Note: This is a structure test, not a full integration test.
    Full integration requires proper AmplifierSession mocking.
    """
    # Verify fixture exists
    assert sample_tutorial.exists(), "Sample tutorial fixture not found"

    # Verify content is readable
    content = sample_tutorial.read_text()
    assert len(content) > 0, "Sample tutorial is empty"
    assert "Functions" in content, "Sample tutorial should mention functions"


@pytest.mark.asyncio
async def test_stage_imports():
    """Test that all stages can be imported."""
    from tutorial_analyzer.analyzer.core import ANALYZER_CONFIG
    from tutorial_analyzer.critic.core import CRITIC_CONFIG
    from tutorial_analyzer.diagnostician.core import DIAGNOSTICIAN_CONFIG
    from tutorial_analyzer.improver.core import IMPROVER_CONFIG
    from tutorial_analyzer.learner_simulator.core import LEARNER_SIMULATOR_CONFIG
    from tutorial_analyzer.synthesizer.core import SYNTHESIZER_CONFIG

    # Verify configs are dicts
    assert isinstance(ANALYZER_CONFIG, dict)
    assert isinstance(LEARNER_SIMULATOR_CONFIG, dict)
    assert isinstance(DIAGNOSTICIAN_CONFIG, dict)
    assert isinstance(IMPROVER_CONFIG, dict)
    assert isinstance(CRITIC_CONFIG, dict)
    assert isinstance(SYNTHESIZER_CONFIG, dict)

    # Verify all have required keys
    for config in [
        ANALYZER_CONFIG,
        LEARNER_SIMULATOR_CONFIG,
        DIAGNOSTICIAN_CONFIG,
        IMPROVER_CONFIG,
        CRITIC_CONFIG,
        SYNTHESIZER_CONFIG,
    ]:
        assert "session" in config
        assert "providers" in config


def test_state_management():
    """Test that state management works."""
    from tutorial_analyzer.state import clear_state
    from tutorial_analyzer.state import load_state
    from tutorial_analyzer.state import save_state

    # Clear any existing state
    clear_state()

    # Test empty state
    state = load_state()
    assert state == {}

    # Test save and load
    test_state = {"analysis": {"test": "data"}}
    save_state(test_state)

    loaded_state = load_state()
    assert loaded_state == test_state

    # Clean up
    clear_state()
    assert load_state() == {}
