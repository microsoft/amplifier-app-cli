#!/usr/bin/env bash
# Reproduction for model_performance-q99f: AMPLIFIER_HOME does NOT isolate the
# uv tool venv. Two AMPLIFIER_HOME directories, ONE shared venv -- the venv's
# editable .pth pointers are silently rewritten to whichever home ran last.
#
# Run INSIDE a DTU. Never on a host with a real Amplifier install.
set -uo pipefail

SP="$(ls -d /root/.local/share/uv/tools/amplifier/lib/*/site-packages)"
echo "shared venv site-packages: $SP"

snap() {
  for f in "$SP"/*.pth; do
    [ -e "$f" ] || continue
    printf '%s\t%s\n' "$(basename "$f")" "$(head -1 "$f")"
  done | sort
}

setup_home() {
  local H="$1"
  mkdir -p "$H"
  cat > "$H/settings.yaml" <<EOF
config:
  providers:
    - module: provider-anthropic
      source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
      config:
        api_key: sk-ant-dummy-key-no-llm-traffic-is-expected
EOF
}

setup_home /root/home-a
setup_home /root/home-b

echo "=== BEFORE: $(snap | wc -l) .pth entries ==="
snap > /tmp/pth-before.txt

echo "=== RUN 1: AMPLIFIER_HOME=/root/home-a ==="
AMPLIFIER_HOME=/root/home-a amplifier run "hi" >/tmp/run-a.log 2>&1
echo "exit=$? (an LLM auth failure here is expected and irrelevant)"
snap > /tmp/pth-after-a.txt
echo "after run A: $(wc -l < /tmp/pth-after-a.txt) .pth entries"
echo "  pointing into /root/home-a/cache: $(grep -c '/root/home-a/cache' /tmp/pth-after-a.txt)"
echo "  pointing into /root/home-b/cache: $(grep -c '/root/home-b/cache' /tmp/pth-after-a.txt)"

echo
echo "=== RUN 2: AMPLIFIER_HOME=/root/home-b (no warning is printed) ==="
AMPLIFIER_HOME=/root/home-b amplifier run "hi" >/tmp/run-b.log 2>&1
echo "exit=$?"
snap > /tmp/pth-after-b.txt
echo "after run B: $(wc -l < /tmp/pth-after-b.txt) .pth entries"
echo "  pointing into /root/home-a/cache: $(grep -c '/root/home-a/cache' /tmp/pth-after-b.txt)"
echo "  pointing into /root/home-b/cache: $(grep -c '/root/home-b/cache' /tmp/pth-after-b.txt)"

echo
echo "=== THE FLIP: .pth files whose target changed between run A and run B ==="
diff /tmp/pth-after-a.txt /tmp/pth-after-b.txt | head -40
echo
echo "flipped-file count: $(diff /tmp/pth-after-a.txt /tmp/pth-after-b.txt | grep -c '^<')"
echo
echo "=== Did run B warn the user about any of this? ==="
grep -iE 'AMPLIFIER_HOME|venv|\.pth|shared|different cache' /tmp/run-b.log || echo "NO WARNING FOUND in run B output."
