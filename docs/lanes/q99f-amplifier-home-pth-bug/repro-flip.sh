set -uo pipefail
SP="$(ls -d /root/.local/share/uv/tools/amplifier/lib/*/site-packages)"
snap() { for f in "$SP"/*.pth; do [ -e "$f" ] || continue; printf '%s\t%s\n' "$(basename "$f")" "$(head -1 "$f")"; done | sort; }

echo "### E3: run home-b a SECOND time"
AMPLIFIER_HOME=/root/home-b amplifier run "hi" >/tmp/run-b2.log 2>&1
snap > /tmp/pth-b2.txt
echo "  -> home-a targets: $(grep -c '/root/home-a/cache' /tmp/pth-b2.txt)  home-b targets: $(grep -c '/root/home-b/cache' /tmp/pth-b2.txt)"

echo "### E4: delete home-a's cache (simulates the /tmp scratch dir being cleaned), then run home-b"
rm -rf /root/home-a/cache
AMPLIFIER_HOME=/root/home-b amplifier run "hi" >/tmp/run-b3.log 2>&1
snap > /tmp/pth-b3.txt
echo "  -> home-a targets: $(grep -c '/root/home-a/cache' /tmp/pth-b3.txt)  home-b targets: $(grep -c '/root/home-b/cache' /tmp/pth-b3.txt)"
echo "### FLIPPED between E3 and E4:"
diff /tmp/pth-b2.txt /tmp/pth-b3.txt | grep '^>' | head -50
echo "flip count: $(diff /tmp/pth-b2.txt /tmp/pth-b3.txt | grep -c '^>')"
echo "### warnings in run-b3?"
grep -icE 'AMPLIFIER_HOME|disagree|rewrote|overwrit' /tmp/run-b3.log
