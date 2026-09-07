#!/usr/bin/env bash
# After-fix verification for model_performance-q99f, run INSIDE the same DTU
# that produced the reproduction. Same two-homes scenario, patched CLI.
set -uo pipefail

SP="$(ls -d /root/.local/share/uv/tools/amplifier/lib/*/site-packages)"
snap() { for f in "$SP"/*.pth; do [ -e "$f" ] || continue; printf '%s\t%s\n' "$(basename "$f")" "$(head -1 "$f")"; done | sort; }

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

rm -rf /root/home-a /root/home-b
setup_home /root/home-a
setup_home /root/home-b

echo "=== RUN 1: AMPLIFIER_HOME=/root/home-a (claims the venv) ==="
AMPLIFIER_HOME=/root/home-a amplifier run "hi" >/tmp/fix-a.log 2>&1
echo "exit=$?"
snap > /tmp/fix-pth-a.txt
echo "  home-a targets: $(grep -c '/root/home-a/cache' /tmp/fix-pth-a.txt)  home-b targets: $(grep -c '/root/home-b/cache' /tmp/fix-pth-a.txt)"

echo
echo "=== RUN 2: AMPLIFIER_HOME=/root/home-b (must REFUSE) ==="
AMPLIFIER_HOME=/root/home-b amplifier run "hi" >/tmp/fix-b.log 2>&1
echo "exit=$?  (expected 1)"
sed -n '1,45p' /tmp/fix-b.log
snap > /tmp/fix-pth-b.txt
echo "  home-a targets: $(grep -c '/root/home-a/cache' /tmp/fix-pth-b.txt)  home-b targets: $(grep -c '/root/home-b/cache' /tmp/fix-pth-b.txt)"
echo "  .pth files changed by the refused run: $(diff /tmp/fix-pth-a.txt /tmp/fix-pth-b.txt | grep -c '^>')  (expected 0)"

echo
echo "=== Diagnostic/repair paths must stay reachable while tripped ==="
AMPLIFIER_HOME=/root/home-b amplifier version; echo "  version exit=$?"
AMPLIFIER_HOME=/root/home-b amplifier reset --help >/dev/null 2>&1; echo "  reset --help exit=$?"

echo
echo "=== Override must warn and continue ==="
AMPLIFIER_HOME=/root/home-b AMPLIFIER_ALLOW_SHARED_VENV=1 amplifier run "hi" >/tmp/fix-b-override.log 2>&1
echo "exit=$? (LLM auth failure expected)"
grep -c "AMPLIFIER_HOME changed" /tmp/fix-b-override.log

echo
echo "=== Remedy #1 from the message: does a per-home environment actually work? ==="
UV_TOOL_DIR=/root/home-b/uv-tools UV_TOOL_BIN_DIR=/root/home-b/bin \
  uv tool install -q git+https://github.com/microsoft/amplifier >/tmp/fix-isolated-install.log 2>&1
echo "install exit=$?"
AMPLIFIER_HOME=/root/home-b /root/home-b/bin/amplifier run "hi" >/tmp/fix-isolated.log 2>&1
echo "isolated run exit=$? (LLM auth failure expected, NOT a refusal)"
grep -c "Refusing to run" /tmp/fix-isolated.log
echo "  shared venv untouched? changed .pth since run 1: $(snap > /tmp/fix-pth-final.txt; diff /tmp/fix-pth-a.txt /tmp/fix-pth-final.txt | grep -c '^>')  (expected 0)"
echo "  isolated venv .pth owned by home-b: $(ls /root/home-b/uv-tools/amplifier/lib/*/site-packages/*.pth 2>/dev/null | wc -l) file(s)"
grep -l '/root/home-b/cache' /root/home-b/uv-tools/amplifier/lib/*/site-packages/*.pth 2>/dev/null | wc -l
