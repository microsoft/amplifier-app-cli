# Amplifier Output Formats

**Structured output for automation and evaluation platforms**

---

## Overview

The `amplifier run` command supports three output formats:

- **`text`** (default): Human-readable markdown output
- **`json`**: Structured JSON with response and metadata for automation
- **`json-trace`**: Complete execution trace with all tool calls and timing

---

## Usage

```bash
# Default: Human-readable markdown
amplifier run "analyze this code"

# JSON: Response and metadata (for automation)
amplifier run --output-format json "analyze this code"

# JSON-Trace: Complete execution trace (for evals/debugging)
amplifier run --output-format json-trace "analyze this code"

# Clean output (suppress diagnostics to stderr)
amplifier run --output-format json "..." 2>/dev/null

# Pipe to processing tool
amplifier run --output-format json-trace "..." 2>/dev/null | python analyze_trace.py
```

---

## Format: `text` (Default)

**Purpose**: Human consumption

**Output**: Markdown-rendered response text printed to console

**Example**:
```
Session ID: abc123...

Here's the analysis:

1. Code structure looks good
2. Consider adding error handling
...
```

**Use when**: Interactive use, human reading output

---

## Format: `json`

**Purpose**: Simple automation and scripting

**Output**: JSON object with response and session metadata

**Schema**:
```json
{
  "status": "success" | "error",
  "response": "The assistant's response text (markdown string)",
  "session_id": "uuid-of-session",
  "profile": "profile-name-used",
  "model": "provider/model-name",
  "timestamp": "2025-11-10T12:34:56.789Z"
}
```

**Example**:
```json
{
  "status": "success",
  "response": "Here's the analysis:\n\n1. Code structure...",
  "session_id": "abc123-def456-...",
  "profile": "dev",
  "model": "anthropic/claude-sonnet-4-5",
  "timestamp": "2025-11-10T12:34:56.789Z"
}
```

**Use when**:
- Scripts need to parse the response
- CI/CD pipelines
- Evaluation platforms
- Automation tools
- Just need the answer and metadata

**Error format**:
```json
{
  "status": "error",
  "error": "Error message here",
  "session_id": "uuid-if-session-created",
  "timestamp": "2025-11-10T12:34:56.789Z"
}
```

**Output Streams**:
- **stdout**: JSON data only
- **stderr**: Hook output, diagnostics, progress indicators

**For clean JSON** (automation/evals):
```bash
# Suppress stderr to get only JSON
amplifier run --output-format json "prompt" 2>/dev/null

# Or capture separately
amplifier run --output-format json "prompt" 2>diagnostics.log 1>response.json
```

---

## Format: `json-trace`

**Purpose**: Complete execution trace for evaluation platforms and debugging

**Output**: JSON object with response, metadata, and complete execution trace

**Schema**:
```json
{
  "status": "success",
  "response": "The AI's final response text (markdown)",
  "session_id": "uuid",
  "profile": "profile-name",
  "model": "provider/model-name",
  "timestamp": "2025-11-10T12:34:56.789Z",

  "metadata": {
    "total_tool_calls": 6,
    "total_agents_invoked": 0,
    "duration_ms": 33199.72
  },

  "execution_trace": [
    {
      "type": "tool_call",
      "tool": "todo",
      "arguments": {"action": "create", "todos": [...]},
      "result": {"success": true, "output": {...}},
      "timestamp": "2025-11-10T12:34:56.100Z",
      "duration_ms": 0.28,
      "sequence": 1
    },
    {
      "type": "tool_call",
      "tool": "glob",
      "arguments": {"pattern": "**/*.md"},
      "result": {"success": true, "output": {"count": 45, "matches": [...]}},
      "timestamp": "2025-11-10T12:34:56.500Z",
      "duration_ms": 234.56,
      "sequence": 2
    },
    {
      "type": "tool_call",
      "tool": "grep",
      "arguments": {"pattern": "Amplifier", "output_mode": "content"},
      "result": {"success": true, "output": {"total_matches": 20, ...}},
      "timestamp": "2025-11-10T12:34:57.000Z",
      "duration_ms": 156.78,
      "sequence": 3
    }
  ]
}
```

**Key Fields**:

- **`execution_trace`**: Array of all tool calls in chronological order
- **`type`**: Always "tool_call" (agent delegations captured as task tool calls)
- **`tool`**: Tool name (e.g., "grep", "glob", "todo", "task")
- **`arguments`**: Full tool input parameters
- **`result`**: Complete tool output (includes `success`, `output`, `error`)
- **`sequence`**: Order of execution (1-indexed)
- **`duration_ms`**: Milliseconds this tool took to execute
- **`timestamp`**: When this tool was called
- **`metadata.total_tool_calls`**: How many tools were invoked total
- **`metadata.duration_ms`**: Total execution time for entire session

**Use when**:
- Evaluating agent performance and tool usage
- Debugging complex multi-tool executions
- Analyzing execution patterns for optimization
- Building evaluation dashboards
- Research on agent decision-making
- Verifying tool call correctness

---

## Implementation Details

### Output Destination

All formats write to **stdout** to enable piping:

```bash
# Pipe to jq for processing
amplifier run --output-format json "..." | jq '.response'

# Save to file
amplifier run --output-format json-trace "..." > execution.json

# Chain with other tools
amplifier run --output-format json "..." | python process.py
```

### Error Handling

**Exit codes**:
- `0`: Success
- `1`: Error (with error details in JSON if using json format)

**Error output** (all formats write to stderr for error messages unless in JSON mode):
- `text`: Human-readable error to stderr
- `json`: Error object to stdout
- `json-trace`: Error object with partial trace to stdout

---

## Examples

### Simple Script Integration

```python
import subprocess
import json

# Capture stdout (JSON) and stderr (diagnostics) separately
result = subprocess.run(
    ["amplifier", "run", "--output-format", "json", "Analyze this code"],
    capture_output=True,
    text=True
)

# Parse JSON from stdout
data = json.loads(result.stdout)

if data["status"] == "success":
    print(f"Response: {data['response']}")
    print(f"Model used: {data['model']}")
    print(f"Session ID: {data['session_id']}")
else:
    print(f"Error: {data['error']}")

# Diagnostics available in result.stderr if needed
```

### Clean JSON for Piping

```bash
# Get only the response text
amplifier run --output-format json "What is 2+2?" 2>/dev/null | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['response'])"

# Save JSON to file without diagnostics
amplifier run --output-format json "Analyze code" 2>/dev/null > response.json

# Process with jq (if installed)
amplifier run --output-format json "List issues" 2>/dev/null | jq -r '.response'
```

### Evaluation Platform Integration

```python
import subprocess
import json

def run_eval_with_trace(prompt: str) -> dict:
    """Run amplifier and capture full execution trace."""
    result = subprocess.run(
        ["amplifier", "run", "--output-format", "json-trace", prompt],
        capture_output=True,
        text=True
    )

    # Parse trace from stdout
    trace_data = json.loads(result.stdout)

    # Analyze execution
    return {
        "response": trace_data["response"],
        "status": trace_data["status"],
        "session_id": trace_data["session_id"],
        "model": trace_data["model"],
        "total_tool_calls": trace_data["metadata"]["total_tool_calls"],
        "duration_ms": trace_data["metadata"]["duration_ms"],
        "tools_used": list({t["tool"] for t in trace_data["execution_trace"]}),
        "execution_trace": trace_data["execution_trace"],
        "diagnostics": result.stderr,
        "exit_code": result.returncode
    }

# Use in eval loop
result = run_eval_with_trace("Create a plan with 3 steps")
print(f"Tools used: {result['tools_used']}")  # e.g., ['todo', 'grep', 'glob']
print(f"Tool calls: {result['total_tool_calls']}")  # e.g., 6
print(f"Duration: {result['duration_ms']}ms")  # e.g., 33199.72

# Analyze tool usage pattern
for step in result['execution_trace']:
    print(f"{step['sequence']}. {step['tool']}: {step['duration_ms']}ms")
```

### Analyzing Execution Traces

```python
import json

def analyze_trace(trace_file: str):
    """Analyze execution trace for insights."""
    with open(trace_file) as f:
        data = json.load(f)

    trace = data["execution_trace"]

    # Tool usage statistics
    tool_counts = {}
    tool_times = {}
    for step in trace:
        tool = step["tool"]
        tool_counts[tool] = tool_counts.get(tool, 0) + 1
        tool_times[tool] = tool_times.get(tool, 0) + step.get("duration_ms", 0)

    print("Tool Usage:")
    for tool, count in sorted(tool_counts.items()):
        avg_time = tool_times[tool] / count
        print(f"  {tool}: {count} calls, avg {avg_time:.2f}ms")

    # Check for agent delegations
    task_calls = [s for s in trace if s["tool"] == "task"]
    print(f"\nAgent delegations: {len(task_calls)}")

    return {
        "tool_counts": tool_counts,
        "tool_times": tool_times,
        "total_duration": data["metadata"]["duration_ms"],
        "agent_delegations": len(task_calls)
    }
```

---

## Performance Considerations

### Format Overhead

- **`text`**: Minimal overhead (markdown rendering only)
- **`json`**: ~1-2% overhead (JSON serialization of response and metadata)
- **`json-trace`**: ~2-5% overhead (trace collection + serialization)

### Data Size

Approximate output sizes (varies by complexity):

- **`text`**: 1-10 KB (response only, formatted)
- **`json`**: 2-15 KB (response + metadata)
- **`json-trace`**: 10 KB - 500 KB (includes full tool call data)

**Trace size depends on**:
- Number of tool calls (more calls = larger trace)
- Tool result sizes (grep with many matches = large)
- Agent delegations (task tool can have large results)

**Recommendation**:
- Use `json` for most automation (lightweight, fast)
- Use `json-trace` for evaluation and analysis (detailed, larger)
- Trace overhead is negligible compared to LLM request time

---

## Philosophy Alignment

This feature follows Amplifier's core principles:

### Ruthless Simplicity
- Three clear formats, each serving specific purpose
- Default (text) unchanged - no surprise for humans
- JSON formats only when explicitly requested

### Mechanism, Not Policy
- Provides capability (structured output)
- Doesn't prescribe how to use it
- Tools/eval platforms decide what to do with data

### Observability
- Builds on existing event system
- Captures what already happens
- Makes execution transparent

---

## Related Documentation

- **Session Management**: See `amplifier session list --help` for session queries
- **Event System**: See `amplifier-core/docs/HOOKS_API.md` for event details
- **Agent Delegation**: See `docs/AGENT_DELEGATION.md` for sub-session architecture

---

**Return to**: [CLI Documentation](../README.md)
