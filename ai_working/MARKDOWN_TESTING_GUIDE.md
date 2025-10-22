# Markdown Rendering Testing Guide

**Created**: 2025-01-21
**Purpose**: Debug and verify markdown rendering in amplifier-app-cli

---

## Quick Test Commands

### Test 1: Single Mode with Base Profile (No Streaming)

```bash
cd /Users/robotdad/Source/dev/amplifier.cli/amplifier-dev/amplifier-app-cli

# Test with explicit markdown
amplifier run --mode single --profile base 'Return this text with markdown: **bold**, *italic*, and `code`'

# Save output to file
amplifier run --mode single --profile base 'Return text with **bold**' > /tmp/test-base.txt 2>&1
cat /tmp/test-base.txt
```

**Expected**:
```
● bold, italic, and code (formatted, no raw ** or * visible)
```

### Test 2: Single Mode with Dev Profile (Has Streaming)

```bash
amplifier run --mode single --profile dev 'Return text with **bold** and *italic*'

# Save output
amplifier run --mode single --profile dev 'Test **bold**' > /tmp/test-dev.txt 2>&1
cat /tmp/test-dev.txt
```

**Expected**: Same as base - markdown renders, no raw syntax

### Test 3: Interactive Mode (The Problem Case)

```bash
# Start interactive mode
amplifier

# Check which profile is active
> /status

# Try markdown
> Say: **bold** and *italic*

# Exit and save for comparison
> /save markdown_test.json
> exit
```

**Expected**: Markdown should render (no `**` or `*` visible)

**Actual** (per your screenshot): Raw markdown syntax showing

---

## Debugging Steps

### Step 1: Check Active Profile

```bash
# In interactive mode
> /status

# Should show which profile is active
```

**Question**: Is interactive mode using the same profile as single mode?

### Step 2: Check UIConfig

```python
# Run this to see what config interactive mode has
python3 << 'EOF'
from amplifier_app_cli.profile_system import ProfileLoader

# Check default profile
loader = ProfileLoader()

# What's the system default?
from amplifier_app_cli.data.profiles import get_system_default_profile
default = get_system_default_profile()
print(f"System default profile: {default}")

# Load it
profile = loader.load_profile(default)
print(f"UI Config: {profile.ui.model_dump() if profile.ui else 'None'}")
print(f"render_markdown: {profile.ui.render_markdown if profile.ui else 'N/A'}")
EOF
```

### Step 3: Check Event Bus is Initialized

Add logging to see if event bus is being created:

```python
# In main.py, line ~1087, add:
logger.info(f"Event bus initialized with config: {ui_config.model_dump() if ui_config else 'None'}")
logger.info(f"render_markdown setting: {ui_config.render_markdown if ui_config else 'N/A'}")
```

Then run interactive mode and check logs.

---

## Save and Compare Script

Create this script to capture and compare outputs:

```bash
#!/bin/bash
# test-markdown.sh

TEST_PROMPT='Say exactly: **bold** and *italic* and `code`'
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="/tmp/amplifier-markdown-tests/$TIMESTAMP"

mkdir -p "$OUTPUT_DIR"

echo "Testing markdown rendering..."
echo "Output directory: $OUTPUT_DIR"

# Test 1: Base profile
echo "Test 1: Base profile (no streaming)"
amplifier run --mode single --profile base "$TEST_PROMPT" \
  > "$OUTPUT_DIR/base.txt" 2>&1

# Test 2: Dev profile
echo "Test 2: Dev profile (with streaming)"
amplifier run --mode single --profile dev "$TEST_PROMPT" \
  > "$OUTPUT_DIR/dev.txt" 2>&1

# Test 3: Interactive mode
echo "Test 3: Interactive mode"
echo "$TEST_PROMPT" | amplifier \
  > "$OUTPUT_DIR/interactive.txt" 2>&1

# Show results
echo ""
echo "=== BASE PROFILE ==="
cat "$OUTPUT_DIR/base.txt"

echo ""
echo "=== DEV PROFILE ==="
cat "$OUTPUT_DIR/dev.txt"

echo ""
echo "=== INTERACTIVE MODE ==="
cat "$OUTPUT_DIR/interactive.txt"

echo ""
echo "Files saved to: $OUTPUT_DIR"
```

**Usage**:
```bash
chmod +x test-markdown.sh
./test-markdown.sh
```

---

## Expected vs Actual Comparison

### Expected (Markdown Rendering Working)

```
● bold and italic and code
```

- No `**` symbols (bold renders)
- No `*` symbols (italic renders)
- No `` ` `` symbols (code renders)
- Left-aligned
- No panel borders

### Actual (Per Your Screenshot)

```
## But if you want a literal story
---
- They bloom at dawn
> Some beautiful things
```

- `##` visible (headers NOT rendering)
- `---` visible (rules NOT rendering)
- `-` visible (lists NOT rendering)
- `>` visible (blockquotes NOT rendering)
- Raw markdown syntax everywhere

**Conclusion**: Markdown rendering is NOT active in interactive mode

---

## Possible Root Causes

### Hypothesis 1: Interactive Mode Uses Different Code Path

**Check**: Does `interactive_chat()` actually emit `AssistantMessage` events?

**Debug**:
```python
# In main.py, after turn.emit_event(AssistantMessage(content=response))
logger.info(f"Emitted AssistantMessage event with {len(response)} chars")
logger.info(f"First 100 chars: {response[:100]}")
```

### Hypothesis 2: Event Bus Not Subscribed in Interactive Mode

**Check**: Is `event_bus.subscribe(handle_event)` actually being called?

**Debug**:
```python
# After event_bus.subscribe(handle_event)
logger.info(f"Subscribed handle_event to event bus")
logger.info(f"UI config render_markdown: {ui_config.render_markdown if ui_config else 'N/A'}")
```

### Hypothesis 3: Wrong Profile Active

**Check**: Which profile does `amplifier` (no args) use?

```bash
amplifier profile current
```

### Hypothesis 4: Display Handler Not Being Called

**Debug**:
```python
# In display/handlers.py, at start of display_assistant_message:
console.print("[red]DEBUG: display_assistant_message called[/red]")
console.print(f"[red]DEBUG: render_markdown = {config.render_markdown if config else 'N/A'}[/red]")
```

---

## Minimal Reproduction Test

```bash
# Create a test file
cat > /tmp/test.py << 'EOF'
from amplifier_app_cli.events import EventBus, AssistantMessage
from amplifier_app_cli.display import handle_event
from amplifier_app_cli.profile_system.schema import UIConfig

# Create event bus with markdown enabled
config = UIConfig(render_markdown=True)
bus = EventBus(config=config)
bus.subscribe(handle_event)

# Emit a message with markdown
event = AssistantMessage(content="Test **bold** and *italic*")
bus.publish(event)
EOF

python3 /tmp/test.py
```

**Expected**: Bold and italic render
**If fails**: Event bus or display handler problem
**If works**: Integration with main.py problem

---

## Tomorrow's Testing Checklist

- [ ] Run minimal reproduction test (above)
- [ ] Check which profile interactive mode uses
- [ ] Add debug logging to interactive_chat()
- [ ] Add debug logging to display_assistant_message()
- [ ] Compare single mode vs interactive mode code paths
- [ ] Check if AssistantMessage events are actually being emitted
- [ ] Check if handle_event is actually being called
- [ ] Save outputs from all test modes for comparison
- [ ] Check session logs for event emissions

---

## Output Capture for Analysis

```bash
# Capture interactive session with full logging
AMPLIFIER_LOG_LEVEL=DEBUG amplifier 2>&1 | tee /tmp/interactive-debug.log

# Then in the session:
> test **bold**
> exit

# Review the debug log
cat /tmp/interactive-debug.log | grep -E "Event|event|display|markdown"
```

---

## Current Working vs Not Working

### ✅ Working

- Single mode with base profile
- Single mode with dev profile
- Python direct test of event bus
- All automated tests (172/172)

### ❌ Not Working

- Interactive mode (your screenshot)
- Markdown shows raw syntax
- Possibly default profile issue?

---

## Next Steps for Debugging

1. **Identify which profile interactive mode uses**
2. **Add debug logging to trace event flow**
3. **Compare interactive_chat() vs execute_single() code paths**
4. **Check if there's a difference in how response is handled**
5. **Verify event bus subscription happens before loop starts**

---

## Files to Check Tomorrow

- `amplifier_app_cli/main.py:1083-1190` - interactive_chat() function
- Line 1087: Event bus subscribe
- Line 1152: AssistantMessage emission
- Check if there's any early return or exception

**Most likely issue**: Interactive mode code path differs from single mode, or profile isn't loading UIConfig properly.
