# Session History & Replay - Testing Guide

## What Was Implemented

### Features
1. **Full conversation history display** when resuming sessions
2. **Replay mode** with configurable speed to simulate live playback
3. **Consistent labeling**: "You:" and "Amplifier:" in both live and resumed sessions
4. **Unified banner** with session metadata at the top
5. **Content-aware replay timing** when timestamps are missing
6. **Streamlined UX** with no redundant separators

### Changes Made
- **NEW**: `amplifier_app_cli/ui/message_renderer.py` - Single source of truth for message display
- **MODIFIED**: `amplifier_app_cli/main.py` - Uses shared renderer, echoes user input with label
- **MODIFIED**: `amplifier_app_cli/commands/session.py` - History/replay functions with integrated banner
- **MODIFIED**: `amplifier_app_cli/ui/__init__.py` - Exports render_message

---

## Testing Commands

### Test 1: Live Chat with Labels
**Purpose**: Verify "You:" and "Amplifier:" labels appear in live chat

```bash
# Start interactive session (requires actual TTY, not piped)
amplifier run --profile dev --mode chat

# Type a question
What is the capital of Germany?

# Observe:
# - Should see "You: What is the capital of Germany?"
# - Should see "Amplifier:" prefix before response
# - Response should be markdown formatted

# Exit
Ctrl-D
```

**Expected**: Both user and assistant messages have labels, consistent formatting.

### Test 2: Single-Shot Session Creation
**Purpose**: Create a test session for history testing

```bash
echo "What is 10 * 10?" | amplifier run --profile dev
```

**Expected**: Session created, response displayed, session ID shown.

### Test 3: History Display (Default)
**Purpose**: Verify full history renders with new banner

```bash
amplifier continue
# Or
amplifier session resume <session-id>

# Observe:
# - Banner at TOP with session info (ID, time ago, profile, model)
# - Complete conversation history below banner
# - "You:" and "Amplifier:" labels on all messages
# - No redundant separator after history
# - Smooth flow into prompt

Ctrl-D  # Exit
```

**Expected**: Seamless experience, banner first, history flows naturally.

### Test 4: Skip History
**Purpose**: Verify --no-history flag works

```bash
amplifier continue --no-history

# Observe:
# - No banner
# - No history
# - Goes straight to prompt
```

**Expected**: Fast resume, no display (like original behavior).

### Test 5: Replay Mode (Default 2x Speed)
**Purpose**: Verify replay with timing simulation

```bash
amplifier continue --replay

# Observe:
# - Banner shows "Replaying at 2.0x"
# - Messages appear with brief pauses between them
# - Pauses proportional to message length (content-based timing)
# - Flows into prompt after replay complete
```

**Expected**: Messages display with delays, smoother than instant but faster than real-time.

### Test 6: Replay Custom Speeds
**Purpose**: Verify speed control works

```bash
# Slow motion (half speed)
amplifier continue --replay --replay-speed=0.5

# Real-time
amplifier continue --replay --replay-speed=1.0

# Fast (5x speed)
amplifier continue --replay --replay-speed=5.0

# Very fast (10x speed)
amplifier continue --replay --replay-speed=10.0
```

**Expected**: Different pause durations between messages based on speed multiplier.

### Test 7: Replay Interruption
**Purpose**: Verify Ctrl-C skips to end

```bash
amplifier continue --replay --replay-speed=0.5

# During replay, press Ctrl-C

# Observe:
# - Shows "⚡ Skipped to end"
# - Remaining messages display instantly
# - Prompt appears
```

**Expected**: Clean interruption, no crash, remaining messages shown.

### Test 8: Multi-Turn Session Test
**Purpose**: Create and replay a longer conversation

```bash
# Create multi-turn session (interactive mode - requires TTY)
amplifier run --profile dev --mode chat

# Have a conversation:
What is the capital of France?
What is the capital of Germany?
List the capitals of Italy and Spain
exit

# Replay it
amplifier continue --replay

# Or with speed control
amplifier continue --replay --replay-speed=5.0
```

**Expected**: All exchanges display in order with appropriate timing.

### Test 9: Session with Specific ID
**Purpose**: Verify session resume by ID works

```bash
# List sessions
amplifier session list

# Pick a session ID from the list
amplifier session resume <full-session-id> --replay
```

**Expected**: Specified session replays correctly.

---

## Expected Behavior Summary

### Live Chat
- ✅ "You:" label when user types message
- ✅ "Amplifier:" label for responses
- ✅ Markdown formatting in responses
- ✅ Tool calls displayed by hooks-streaming-ui (cyan boxes)
- ✅ Standard banner at session start

### History Display (amplifier continue)
- ✅ Banner at TOP with session metadata
- ✅ Complete conversation history
- ✅ "You:" and "Amplifier:" labels
- ✅ Markdown formatting preserved
- ✅ No redundant separators
- ✅ Smooth transition to prompt
- ❌ Thinking blocks NOT shown (see KNOWN_LIMITATIONS.md)
- ❌ Tool calls NOT shown (see KNOWN_LIMITATIONS.md)

### Replay Mode (amplifier continue --replay)
- ✅ Banner shows replay speed
- ✅ Messages display with timing delays
- ✅ Content-based timing when timestamps missing
- ✅ Ctrl-C interruption works
- ✅ Speed control via --replay-speed flag
- ❌ Thinking blocks NOT shown (see KNOWN_LIMITATIONS.md)
- ❌ Tool calls NOT shown (see KNOWN_LIMITATIONS.md)

---

## Known Issues

### 1. Thinking Blocks and Tool Calls Missing from History

**Status**: Documented in `KNOWN_LIMITATIONS.md`

**Impact**: History replay doesn't show the complete execution flow that users saw during live chat.

**Workaround**: Review `events.jsonl` for complete execution log.

**Future Work**: Requires architectural changes to preserve structured content through the save/load cycle.

### 2. Piped Input in Interactive Mode

**Status**: Terminal limitation

**Impact**: Piped input doesn't work well with interactive prompt_toolkit

**Workaround**: Use single-shot mode (`amplifier run "prompt"`) instead of chat mode with piped input.

---

## Performance Metrics

**Tested configurations**:
- ✅ Sessions with 2-6 messages: Instant history display
- ✅ Replay at 0.5x, 1x, 2x, 5x, 10x speeds: All work smoothly
- ✅ Content-based timing: Reasonable delays for messages of varying lengths

**Not yet tested**:
- Very long sessions (100+ messages)
- Sessions with large tool outputs
- Sessions with many rapid exchanges

---

## Architecture Notes

### Single Source of Truth ✅
All message rendering goes through `ui/message_renderer.py`:
- `render_message()` function used by live chat, history, and replay
- Change formatting once, propagates everywhere
- Zero duplication between display modes

### Files Modified
1. `ui/message_renderer.py` (NEW) - 80 lines
2. `ui/__init__.py` (MODIFIED) - Export render_message
3. `main.py` (MODIFIED) - Use shared renderer, echo user input
4. `commands/session.py` (MODIFIED) - History/replay with integrated banner

**Total**: ~200 lines of new code, zero duplication

---

## Quick Reference

```bash
# Resume with full history (default)
amplifier continue

# Resume without history
amplifier continue --no-history

# Replay at 2x speed (default)
amplifier continue --replay

# Replay at custom speed
amplifier continue --replay --replay-speed=5.0

# Show thinking blocks (if available in content)
amplifier continue --show-thinking

# All flags work with session resume too
amplifier session resume <id> --replay --replay-speed=10.0
```

---

## Verification Checklist

- [ ] Live chat shows "You:" labels
- [ ] Live chat shows "Amplifier:" labels
- [ ] History display shows banner at top
- [ ] History shows complete conversation
- [ ] History has no redundant separator
- [ ] Replay mode works with timing
- [ ] Replay speed control works
- [ ] Ctrl-C interruption works
- [ ] --no-history flag works
- [ ] All checks pass (`make check`)
