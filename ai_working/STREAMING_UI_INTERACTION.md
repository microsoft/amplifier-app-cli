# Streaming UI Hook Interaction Issue

**Date**: 2025-01-21
**Context**: TinkerTasker-inspired UI improvements
**For**: amplifier-module-hooks-streaming-ui maintainer

---

## Issue Summary

The `hooks-streaming-ui` module (used in dev and full profiles) displays LLM responses in **centered panels with raw markdown syntax**, which conflicts with the new markdown rendering feature in amplifier-app-cli.

---

## What Happens

### With hooks-streaming-ui (dev/full profiles)

**User sees**:
```
┌─────────────────────────────────────┐
│   ## The Title                      │ ← Centered, raw markdown
│   **Bold text** appears as-is       │ ← Raw ** symbols visible
│   Content is in a panel box         │ ← Centered in panel
└─────────────────────────────────────┘
```

### Without hooks-streaming-ui (base/production profiles)

**User sees**:
```
● The Title (rendered as header)
  Bold text (rendered with formatting)
  Content is left-aligned
```

---

## Root Cause

**hooks-streaming-ui displays content blocks during streaming**:

1. Hooks into: `content_block:start` and `content_block:end`
2. Displays **thinking blocks** (the 🧠 Thinking... sections) ✅ **This works great!**
3. But also seems to display final **response content** in panels
4. Uses direct `print()` statements with ANSI codes, not Rich formatting
5. No markdown rendering - shows raw `**bold**` and `##` syntax

**The panel/centering**:
- Actually comes from amplifier-app-cli's `display_assistant_message()` using Rich's `Markdown()` renderer
- Rich.Markdown centers `##` headers by default (markdown spec behavior)
- When LLM uses structured markdown (`##` headers, `---` rules), Rich adds visual structure

---

## Current Workaround (in amplifier-app-cli)

We added **streaming suppressor hooks** that:

```python
# In main.py, after session.initialize()
async def streaming_suppressor(event: str, data: dict) -> HookResult:
    """Suppress streaming-ui content display for markdown rendering."""
    if event in ("content_block:start", "content_block:delta", "content_block:end"):
        return HookResult(action="deny")  # Prevent streaming-ui from showing raw
    return HookResult(action="continue")

# Register at priority -1000 (very high, runs before streaming-ui)
hooks.register("content_block:start", streaming_suppressor, priority=-1000, name="markdown-suppressor")
hooks.register("content_block:delta", streaming_suppressor, priority=-1000, name="markdown-suppressor")
hooks.register("content_block:end", streaming_suppressor, priority=-1000, name="markdown-suppressor")
```

**This suppresses streaming-ui's content display**, letting our markdown-rendering handlers show the final output.

**Fixed centering** by adding `justify="left"` to `Markdown()`:
```python
Markdown(content, justify="left")  # Left-align all markdown content
```

---

## Better Long-Term Solution (for hooks-streaming-ui)

### Option 1: Add Markdown Rendering to hooks-streaming-ui

**Modify hooks-streaming-ui to**:
1. Use Rich Console instead of raw `print()`
2. Render markdown with `Markdown(content, justify="left")`
3. Add config option: `render_markdown: bool`

**Benefits**:
- Streaming markdown display during token generation
- No need for suppression workarounds
- Better visual consistency

**Code change** (in hooks-streaming-ui):
```python
from rich.console import Console
from rich.markdown import Markdown

console = Console()

async def handle_content_block_end(self, _event: str, data: dict) -> HookResult:
    """Display complete content block."""
    block_data = data.get("data", {})
    block = block_data.get("block", {})
    block_type = block.get("type")

    if block_type == "text":
        text = block.get("text", "")
        # NEW: Render with markdown if configured
        if self.render_markdown:
            console.print(Markdown(text, justify="left"))
        else:
            print(text)  # Current behavior

    return HookResult(action="continue")
```

### Option 2: Split Responsibilities

**hooks-streaming-ui** should **only handle streaming display** (thinking blocks, tool calls during execution).

**amplifier-app-cli** handles **final response formatting** (markdown, layout, styling).

This is cleaner separation - streaming-ui shows "what's happening now", app layer shows "final result".

---

## Configuration Needed

If going with Option 1, extend hooks-streaming-ui config:

```yaml
# In profile
hooks:
  - module: hooks-streaming-ui
    config:
      ui:
        show_thinking_stream: true
        show_tool_lines: 5
        render_markdown: true        # NEW: Enable markdown rendering
        justify: "left"               # NEW: Text justification
```

---

## Current Status

**Workaround works** but requires:
- Suppressor hooks in 3 places in main.py (~60 lines)
- Priority -1000 to run before streaming-ui
- Blank line to clear status artifacts

**Cleaner long-term**: Modify hooks-streaming-ui to support markdown rendering natively.

---

## Questions for Maintainer

1. Should hooks-streaming-ui use Rich Console instead of raw print()?
2. Should markdown rendering be an option in hooks-streaming-ui?
3. Should streaming-ui only show "in-progress" state (thinking, tools), not final responses?
4. What's the intended division between hook display vs CLI layer display?

---

## Files Affected

**In amplifier-app-cli** (our workaround):
- `amplifier_app_cli/main.py` - Streaming suppressor hooks
- `amplifier_app_cli/display/handlers.py` - justify="left" in Markdown()

**Would affect in hooks-streaming-ui** (if implementing Option 1):
- `amplifier_module_hooks_streaming_ui/__init__.py` - Add Rich markdown rendering

---

**Recommendation**: Discuss with kernel team whether display formatting belongs in hooks layer or app layer. Current architecture suggests app layer (profiles control UI config), which argues for Option 2 (streaming-ui shows progress, app shows final formatted result).
