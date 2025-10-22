# Testing Plan for Tomorrow

**Issue**: Markdown renders in single mode but shows raw syntax in interactive mode

---

## Quick Test

```bash
cd /Users/robotdad/Source/dev/amplifier.cli/amplifier-dev/amplifier-app-cli

# Run with debug logging
export AMPLIFIER_LOG_LEVEL=INFO

amplifier

# Then in the session:
> test **bold**
> exit
```

**Look for in output**:
- "Event bus initialized for interactive mode"
- "UI config render_markdown: True"
- "display_assistant_message called"

**If you see these logs**: Event bus IS working, something else is printing raw markdown
**If you DON'T see these logs**: Event bus not initializing properly

---

## Run Debug Script

```bash
python3 ai_working/debug_markdown.py
```

This will show if the components work in isolation.

---

## Check What's Printing Raw Markdown

The raw markdown in your screenshot must be coming from:
1. **streaming-ui hook** (but we're suppressing it)
2. **orchestrator** (maybe prints final response?)
3. **Some other hook** we don't know about

To find it:
```bash
# Check orchestrator source
ls ../amplifier-module-loop-streaming/
cat ../amplifier-module-loop-streaming/amplifier_module_loop_streaming/__init__.py | grep -A 5 "print"
```

---

## My Hypothesis

Looking at your screenshot, I think **something is printing the raw response BEFORE our event handlers run**.

The clue: You see the full response with raw markdown, which suggests:
- session.execute() completes
- Something prints the raw response immediately
- Our event handlers might run after, but output is already shown

**Test this**: Add a distinctive marker to our display handler:
```python
# In display/handlers.py
console.print("[red]>>> FROM EVENT HANDLER <<<[/red]")  # Add this temporarily
```

Then run interactive mode. If you see the marker, our handler IS running. If not, it's not being called.

---

## Files Created for You

1. **ai_working/MARKDOWN_TESTING_GUIDE.md** - Comprehensive testing guide
2. **ai_working/debug_markdown.py** - Executable debug script
3. **ai_working/STREAMING_UI_INTERACTION.md** - Issue documentation for maintainer
4. **This file** - Quick testing steps

---

## Current Status

**Working**:
- ✅ Single mode (`amplifier run --mode single`)
- ✅ Direct event bus test
- ✅ All automated tests

**Not Working**:
- ❌ Interactive mode (`amplifier` with no args)
- Markdown shows raw syntax in your screenshot

**Most Likely Cause**: Something in the orchestrator or another hook is printing the raw response, and our event handlers either:
1. Aren't being called
2. Are being called but output is invisible/suppressed
3. Run after raw output already displayed

Tomorrow: Add those debug logs and trace where the raw markdown is coming from.
