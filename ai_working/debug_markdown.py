#!/usr/bin/env python3
"""Debug script to trace markdown rendering in interactive mode."""

import subprocess


def test_direct_rendering():
    """Test event bus and display handler directly."""
    print("=" * 80)
    print("TEST 1: Direct Event Bus Test")
    print("=" * 80)

    from amplifier_app_cli.display import handle_event
    from amplifier_app_cli.events import AssistantMessage
    from amplifier_app_cli.events import EventBus
    from amplifier_app_cli.profile_system.schema import UIConfig

    # Create event bus with markdown enabled
    config = UIConfig(render_markdown=True)
    print(f"UIConfig render_markdown: {config.render_markdown}")

    bus = EventBus(config=config)
    bus.subscribe(handle_event)
    print("Subscribed handle_event to bus")

    # Test message
    test_content = "Test **bold** and *italic* and `code` and ## Header"
    event = AssistantMessage(content=test_content)

    print(f"\nEmitting event with content: {test_content}\n")
    bus.publish(event)
    print("\n✓ Direct test complete")


def test_profile_loading():
    """Test which profile interactive mode would use."""
    print("\n" + "=" * 80)
    print("TEST 2: Profile Loading")
    print("=" * 80)

    from amplifier_app_cli.data.profiles import get_system_default_profile
    from amplifier_app_cli.profile_system import ProfileLoader

    default = get_system_default_profile()
    print(f"System default profile: {default}")

    loader = ProfileLoader()
    profile = loader.load_profile(default)

    print(f"Profile loaded: {profile.profile.name}")
    if profile.ui:
        print("UI Config present: Yes")
        ui_dict = profile.ui.model_dump()
        for key, value in ui_dict.items():
            print(f"  {key}: {value}")
    else:
        print("UI Config present: No")


def test_single_mode():
    """Test single mode output."""
    print("\n" + "=" * 80)
    print("TEST 3: Single Mode Output")
    print("=" * 80)

    prompt = 'Return exactly: "**bold** and *italic*"'

    print(f"Running: amplifier run --mode single --profile base '{prompt}'")
    print("Output:")
    print("-" * 80)

    result = subprocess.run(
        ["amplifier", "run", "--mode", "single", "--profile", "base", prompt], capture_output=True, text=True
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    # Check for raw markdown
    if "**" in result.stdout or "*italic*" in result.stdout:
        print("\n❌ FAIL: Raw markdown syntax found")
    else:
        print("\n✓ PASS: Markdown appears to be rendered")


def check_interactive_code_path():
    """Check the interactive_chat function."""
    print("\n" + "=" * 80)
    print("TEST 4: Interactive Code Analysis")
    print("=" * 80)

    import inspect

    from amplifier_app_cli.main import interactive_chat

    source = inspect.getsource(interactive_chat)

    # Check for key elements
    checks = {
        "EventBus initialization": "EventBus(" in source,
        "handle_event subscription": "event_bus.subscribe(handle_event)" in source,
        "AssistantMessage emission": "AssistantMessage(content=response)" in source,
        "TurnContext usage": "TurnContext(event_bus)" in source,
    }

    print("Code path checks:")
    for check, passed in checks.items():
        status = "✓" if passed else "✗"
        print(f"  {status} {check}")

    if not all(checks.values()):
        print("\n❌ Missing integration in interactive_chat()")
    else:
        print("\n✓ All integration points present")


def main():
    """Run all tests."""
    print("MARKDOWN RENDERING DEBUG TESTS")
    print("=" * 80)
    print()

    try:
        test_direct_rendering()
    except Exception as e:
        print(f"❌ Test 1 failed: {e}")

    try:
        test_profile_loading()
    except Exception as e:
        print(f"❌ Test 2 failed: {e}")

    try:
        test_single_mode()
    except Exception as e:
        print(f"❌ Test 3 failed: {e}")

    try:
        check_interactive_code_path()
    except Exception as e:
        print(f"❌ Test 4 failed: {e}")

    print("\n" + "=" * 80)
    print("DEBUGGING COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("1. Review test results above")
    print("2. If Test 1 passes but Test 3 fails: integration issue")
    print("3. If Test 1 fails: display handler issue")
    print("4. Check /tmp/test-*.txt files for saved outputs")


if __name__ == "__main__":
    main()
