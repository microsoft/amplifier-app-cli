set -uo pipefail
rm -rf /root/home-c
mkdir -p /root/home-c
cat > /root/home-c/settings.yaml <<EOF
config:
  providers:
    - module: provider-anthropic
      source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
      config:
        api_key: sk-ant-dummy-key-no-llm-traffic-is-expected
EOF
SHARED="$(ls -d /root/.local/share/uv/tools/amplifier/lib/*/site-packages)"
snap() { for f in "$SHARED"/*.pth; do [ -e "$f" ] || continue; printf '%s\t%s\n' "$(basename "$f")" "$(head -1 "$f")"; done | sort; }
snap > /tmp/shared-before-c.txt

UV_TOOL_DIR=/root/home-c/uv-tools UV_TOOL_BIN_DIR=/root/home-c/bin \
  uv tool install -q git+https://github.com/microsoft/amplifier >/tmp/c-install.log 2>&1
echo "install exit=$?"
# NOTE: uv HARDLINKS package files from its cache, so the in-place patch applied
# to the shared venv already reached this fresh install's main.py. Ship the new
# module alongside it so the isolated install is complete.
ISO="$(ls -d /root/home-c/uv-tools/amplifier/lib/*/site-packages)"
cp /tmp/venv_home_guard.py "$ISO/amplifier_app_cli/lib/venv_home_guard.py"

AMPLIFIER_HOME=/root/home-c /root/home-c/bin/amplifier run "hi" >/tmp/c-run.log 2>&1
echo "run exit=$?"
echo "  refusals:                            $(grep -c 'Refusing to run' /tmp/c-run.log)  (expected 0)"
echo "  reached the LLM (auth failure):      $(grep -c 'AuthenticationError' /tmp/c-run.log)  (expected 1)"
echo "  isolated venv .pth total:            $(ls "$ISO"/*.pth 2>/dev/null | wc -l)"
echo "  isolated venv .pth -> home-c cache:  $(grep -l '/root/home-c/cache' "$ISO"/*.pth 2>/dev/null | wc -l)"
echo "  home-c cache dirs:                   $(ls /root/home-c/cache 2>/dev/null | wc -l)"
snap > /tmp/shared-after-c.txt
echo "  SHARED venv .pth changed by this:    $(diff /tmp/shared-before-c.txt /tmp/shared-after-c.txt | grep -c '^>')  (expected 0)"
