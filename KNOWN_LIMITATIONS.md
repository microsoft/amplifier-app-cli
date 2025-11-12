# Known Limitations

## Session History Replay

### Thinking Blocks and Tool Calls Not in History Replay

**Status**: Known limitation (as of 2025-11-12)

**Symptom**: When resuming sessions with `amplifier continue` or `amplifier session resume`, thinking blocks and tool execution details are not shown in the conversation history, even though they were displayed during live execution.

**Why This Happens**:

1. **Thinking blocks and tool calls are ephemeral display** - They're shown during live execution by the `hooks-streaming-ui` module, which listens to real-time events and displays them to the console

2. **Transcript contains final text only** - The `transcript.jsonl` file saves the conversation (user/assistant messages), but these messages contain only the final text responses, not the intermediate thinking or tool execution details

3. **Events vs Transcript** - There are two separate logs:
   - `events.jsonl`: Complete event stream including thinking blocks, tool calls, everything (hooks-logging)
   - `transcript.jsonl`: Conversation messages only (session-store)

**Current Behavior**:
- ✅ Live chat shows: User input → Thinking blocks → Tool calls → Final response
- ❌ History replay shows: User input → Final response (missing thinking and tools)

**Workaround**:
- Use `--show-thinking` flag to show thinking blocks IF they're embedded in the response content (currently not the case for most messages)
- Review `events.jsonl` file directly to see complete execution log

**Potential Solutions** (for future implementation):

**Option A: Parse events.jsonl for replay**
- Reconstruct display from event log
- Pro: Complete recreation of live experience
- Con: Complex parsing, tightly couples to hooks-streaming-ui event format

**Option B: Save display information to transcript**
- Enhance transcript format to include display events
- Pro: Self-contained transcript with all display info
- Con: Larger transcript files, mixing conversation with display data

**Option C: Modify context manager to preserve structured content**
- Keep thinking blocks and tool calls in message content structure
- Modify message renderer to display them inline
- Pro: Clean architecture, content preserved semantically
- Con: Requires changes to amplifier-core context modules

**Recommendation**: Option C is most aligned with architecture (preserve semantic content, render appropriately based on context).

**Related Files**:
- `amplifier-dev/amplifier-app-cli/amplifier_app_cli/ui/message_renderer.py` - Display logic
- `amplifier-dev/amplifier-module-hooks-streaming-ui/` - Live display during execution
- `amplifier-dev/amplifier-app-cli/amplifier_app_cli/session_store.py` - Transcript persistence
- `amplifier-dev/amplifier-module-context-simple/` - Message storage in context

**See Also**: Issue tracking in `.beads/amplifier-dev.jsonl` for planned work on this enhancement.
