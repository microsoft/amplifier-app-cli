#!/usr/bin/env bash
# Two homes, one base environment: does the overlay actually isolate them?
#
# This is the q99f shape -- the SAME module name installed editable from two
# different AMPLIFIER_HOME cache roots, which is what produces two writes to one
# `_editable_impl_<name>.pth` filename -- run through
# `amplifier_app_cli.lib.home_env.uv_target_args()` instead of `sys.executable`.
#
# SAFETY: this never invokes `amplifier`, never sets AMPLIFIER_HOME for a CLI
# run, and writes nothing outside its own temp directory. It only READS the base
# environment's `.pth` count, before and after, to prove it did not move. That
# is why it is safe on a host, unlike the q99f behavioural reproduction, which
# needed a DTU.
#
# Usage:  bash probe-overlay-isolation.sh
#         PROBE_PYTHON=<interpreter> bash probe-overlay-isolation.sh
#
# PROBE_PYTHON selects the BASE environment under test. Point it at the uv tool
# venv (`~/.local/share/uv/tools/amplifier/bin/python`) to run the claims
# against the environment that actually collided.
#
# FAIL-BEFORE: set AMPLIFIER_HOME_ENV=0 to get the shipped behaviour (installs
# target the base environment) and watch the same claims fail. Do that ONLY
# against a throwaway base -- see probe-fail-before.sh, which builds one.
#
# Exit:   0 = isolated, 1 = a claim failed (each failure prints what it saw)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
D="$(mktemp -d "${TMPDIR:-/tmp}/xq95-overlay.XXXXXX")"
export REPO
export AMPLIFIER_HOME_ENV="${AMPLIFIER_HOME_ENV:-1}"
export UV_OFFLINE="${UV_OFFLINE:-1}"
PY_BIN="${PROBE_PYTHON:-python3}"
fail=0

say() { printf '\n== %s\n' "$*"; }
claim() { # claim <description> <actual> <expected>
  if [ "$2" = "$3" ]; then printf '  PASS  %-52s %s\n' "$1" "$2"
  else printf '  FAIL  %-52s got %s, want %s\n' "$1" "$2" "$3"; fail=1; fi
}

# The module is loaded by file path, not by importing the `amplifier_app_cli`
# package: the package's __init__ pulls in click and the whole CLI, which a
# throwaway base environment does not have. Same file either way.
LOADER='
import importlib.util, os, sys
from pathlib import Path
_spec = importlib.util.spec_from_file_location(
    "xq95_home_env",
    Path(os.environ["REPO"]) / "amplifier_app_cli" / "lib" / "home_env.py",
)
home_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(home_env)
'

BASE_SP="$("$PY_BIN" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
pth_count() { ls "$BASE_SP"/*.pth 2>/dev/null | wc -l | tr -d ' '; }

say "base environment"
echo "  $BASE_SP"
BEFORE="$(pth_count)"
echo "  .pth files before: $BEFORE"

# One module source per home, under a cache path shaped exactly like
# foundation's: <home>/cache/<repo>-<16 hex>.
for home in home-a home-b; do
  SRC="$D/$home/cache/amplifier-module-probe-0123456789abcdef"
  mkdir -p "$SRC/src/amplifier_module_probe"
  cat > "$SRC/pyproject.toml" <<EOF
[project]
name = "amplifier-module-probe"
version = "0.1.0"
requires-python = ">=3.11"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/amplifier_module_probe"]
EOF
  printf 'HOME_NAME = "%s"\n' "$home" > "$SRC/src/amplifier_module_probe/__init__.py"
done

say "install the same module name from both homes"
for home in home-a home-b; do
  SRC="$D/$home/cache/amplifier-module-probe-0123456789abcdef"
  mapfile -t UVARGS < <("$PY_BIN" -c "$LOADER"'
print("\n".join(home_env.uv_target_args(Path(sys.argv[1]))))
' "$D/$home")
  printf '  %s -> %s\n' "$home" "${UVARGS[1]:-<none>}"
  uv pip install -e "$SRC" "${UVARGS[@]}" >"$D/$home-install.log" 2>&1 \
    || { echo "  install failed; see $D/$home-install.log"; fail=1; }
done

say "claims"
claim "base .pth count unchanged" "$(pth_count)" "$BEFORE"

overlay_pth() { # overlay_pth <home-dir>
  "$PY_BIN" -c "$LOADER"'
sp = home_env.env_site_packages(home_env.home_env_root(Path(sys.argv[1])))
if sp is None:
    print("")
else:
    p = sp / "_editable_impl_amplifier_module_probe.pth"
    print(p.read_text().strip() if p.exists() else "")
' "$1"
}

for home in home-a home-b; do
  TARGET="$(overlay_pth "$D/$home")"
  case "$TARGET" in
    *"/$home/cache/"*) claim "$home .pth points into $home" yes yes ;;
    "") claim "$home has its own editable .pth" missing present ;;
    *) claim "$home .pth points into $home" "no ($TARGET)" yes ;;
  esac
done

# The failure q99f measured: one filename, two homes, second write wins.
A="$(overlay_pth "$D/home-a")"
case "$A" in
  *"/home-a/cache/"*) claim "home-a survived home-b's install" yes yes ;;
  *) claim "home-a survived home-b's install" "no (${A:-missing})" yes ;;
esac

claim "base environment has no probe .pth" \
  "$(ls "$BASE_SP"/_editable_impl_amplifier_module_probe.pth 2>/dev/null | wc -l | tr -d ' ')" 0

printf '\nartifacts: %s\n' "$D"
exit "$fail"
