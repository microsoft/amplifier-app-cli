#!/usr/bin/env bash
# lane_turns.sh <transcript.jsonl> [N] [CHARS]
#
# Print the last N TOP-LEVEL turns of an Amplifier session as one line each.
# This is the "what is this lane actually doing" probe.
#
# Why this and not `tmux capture-pane`: the pane is a TUI. Its last line is
# box-drawing characters, and the "Processing..." spinner VANISHES while a
# sub-agent runs, so absent-spinner reads as idle when the lane is busy.
# Measured cost of getting this wrong: 3 of 5 lanes assessed incorrectly,
# one declared dead while it was actively landing red-proven fixes.
#
# Never touches events.jsonl (reaches 750MB with 100k+ token single lines).

set -uo pipefail

T="${1:?usage: lane_turns.sh <transcript.jsonl> [N] [CHARS]}"
N="${2:-20}"
CH="${3:-300}"

if [ ! -f "$T" ]; then
  echo "(no transcript yet: $T)"
  exit 0
fi

# The select(($txt|length) > 0) filter is load-bearing: roughly 60% of assistant
# records are tool-call-only with empty text. Without it you get blank lines and
# no narrative.
jq -r --argjson w "$CH" '
  select(.role=="user" or .role=="assistant")
  | ((.metadata | if type=="string" then (fromjson? // {}) else . end).timestamp // "?") as $ts
  | (if (.content|type)=="string" then .content
     else ([.content[]? | select(.type=="text").text] | join("\n")) end) as $txt
  | select(($txt|length) > 0)
  | "\($ts[5:19]) [\(.role[0:5])] \($txt | gsub("\\s+";" ") | .[0:$w])"
' "$T" 2>/dev/null | tail -n "$N"
