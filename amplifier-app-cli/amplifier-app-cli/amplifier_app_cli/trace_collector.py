"""Execution trace collector for json-trace output format.

Captures tool calls, agent delegations, and execution metadata.
"""

from __future__ import annotations

import time
from datetime import UTC
from datetime import datetime
from typing import Any


class TraceCollector:
    """Collects execution trace for json-trace output format.

    Hooks into tool:pre and tool:post events to capture:
    - Tool calls with arguments and results
    - Agent delegations (via task tool)
    - Timing information
    - Execution sequence
    """

    def __init__(self):
        """Initialize trace collector."""
        self.trace: list[dict[str, Any]] = []
        self.start_times: dict[str, float] = {}
        self.sequence = 0
        self.start_time = time.time()

    async def on_tool_pre(self, event: str, data: dict[str, Any]):
        """Capture tool call start."""
        from amplifier_core.models import HookResult

        tool_name = data.get("tool_name", "unknown")
        tool_input = data.get("tool_input", {})

        # Generate unique ID for this tool call
        call_id = f"{tool_name}_{self.sequence}"
        self.sequence += 1

        # Store start time
        self.start_times[call_id] = time.time()

        # Store in trace (will update with result in post hook)
        self.trace.append(
            {
                "type": "tool_call",
                "tool": tool_name,
                "arguments": tool_input,
                "result": None,  # Will be filled in post hook
                "timestamp": datetime.now(UTC).isoformat(),
                "duration_ms": None,  # Will be filled in post hook
                "sequence": self.sequence,
                "call_id": call_id,
            }
        )

        return HookResult(action="continue")

    async def on_tool_post(self, event: str, data: dict[str, Any]):
        """Capture tool call completion."""
        from amplifier_core.models import HookResult

        tool_name = data.get("tool_name", "unknown")
        tool_result = data.get("result")

        # Find the matching trace entry (most recent with this tool name and no result)
        for entry in reversed(self.trace):
            if entry["tool"] == tool_name and entry["result"] is None:
                call_id = entry["call_id"]
                start_time = self.start_times.get(call_id)

                # Update with result and timing
                entry["result"] = tool_result
                if start_time:
                    duration = (time.time() - start_time) * 1000  # ms
                    entry["duration_ms"] = round(duration, 2)

                # Clean up call_id (internal only)
                del entry["call_id"]
                break

        return HookResult(action="continue")

    def get_trace(self) -> list[dict[str, Any]]:
        """Get complete execution trace."""
        # Remove internal call_id field from all entries
        cleaned_trace = []
        for entry in self.trace:
            cleaned = {k: v for k, v in entry.items() if k != "call_id"}
            cleaned_trace.append(cleaned)
        return cleaned_trace

    def get_metadata(self) -> dict[str, Any]:
        """Get execution metadata summary."""
        tool_calls = [e for e in self.trace if e["type"] == "tool_call"]
        agent_calls = [e for e in tool_calls if e["tool"] == "task"]

        return {
            "total_tool_calls": len(tool_calls),
            "total_agents_invoked": len(agent_calls),
            "duration_ms": round((time.time() - self.start_time) * 1000, 2),
        }
