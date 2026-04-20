#!/usr/bin/env bash
set -euo pipefail

# Smoke test for amplifier-app-cli local changes.
#
# Follows the same Docker pattern as amplifier-core's e2e-smoke-test.sh:
#   1. Start a fresh container
#   2. Install amplifier from GitHub (published core from PyPI)
#   3. Override amplifier-app-cli with the local checkout
#   4. Run targeted unit tests for the specific fix
#   5. Run a full session smoke test to confirm nothing is broken
#
# Prerequisites:
#   - Docker installed and running
#   - ANTHROPIC_API_KEY set (or in ~/.amplifier/keys.env)
#
# Usage:
#   ./scripts/smoke-test.sh                     # Test local checkout at repo root
#   SMOKE_PROMPT="..." ./scripts/smoke-test.sh  # Override session prompt

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTAINER_NAME="amplifier-app-cli-smoke-$$"
SMOKE_PROMPT="${SMOKE_PROMPT:-Say exactly: smoke-test-ok}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${YELLOW}[smoke-test]${NC} $*"; }
info() { echo -e "${CYAN}[smoke-test]${NC} $*"; }
pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

cleanup() {
    log "Cleaning up container $CONTAINER_NAME..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 0: Resolve API keys
# ---------------------------------------------------------------------------

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    KEYS_ENV="$HOME/.amplifier/keys.env"
    if [[ -f "$KEYS_ENV" ]]; then
        log "Loading API keys from $KEYS_ENV..."
        set -a; source "$KEYS_ENV"; set +a
    fi
fi

[[ -z "${ANTHROPIC_API_KEY:-}" ]] && fail "ANTHROPIC_API_KEY not set. Set it in your environment or ~/.amplifier/keys.env"
command -v docker &>/dev/null || fail "Docker not installed or not in PATH"

# ---------------------------------------------------------------------------
# Step 1: Start container
# ---------------------------------------------------------------------------

log "Starting isolated Docker container..."
docker run -d --name "$CONTAINER_NAME" \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    python:3.12-slim \
    sleep 3600 \
    || fail "Container creation failed"

info "Container: $CONTAINER_NAME"

# ---------------------------------------------------------------------------
# Step 2: Bootstrap (git + uv)
# ---------------------------------------------------------------------------

log "Installing prerequisites (git, uv)..."
docker exec "$CONTAINER_NAME" bash -c "
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1
    pip install -q uv
    echo 'Bootstrap OK'
" || fail "Bootstrap failed"

# ---------------------------------------------------------------------------
# Step 3: Install amplifier
# ---------------------------------------------------------------------------

log "Installing amplifier from GitHub..."
docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    uv tool install git+https://github.com/microsoft/amplifier@main 2>&1 | tail -3
    echo 'Install OK'
" || fail "Amplifier install failed"

INSTALLED_VERSION=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    amplifier --version 2>&1
")
info "Baseline: $INSTALLED_VERSION"

# ---------------------------------------------------------------------------
# Step 4: Override amplifier-app-cli with local checkout
# ---------------------------------------------------------------------------

log "Copying local amplifier-app-cli into container..."
docker cp "$REPO_DIR" "$CONTAINER_NAME:/tmp/amplifier-app-cli" \
    || fail "Failed to copy repo into container"

log "Installing local amplifier-app-cli (--force-reinstall --no-deps)..."
OVERRIDE_OUTPUT=$(docker exec "$CONTAINER_NAME" bash -c "
    uv pip install \
        --python /root/.local/share/uv/tools/amplifier/bin/python3 \
        --force-reinstall --no-deps \
        /tmp/amplifier-app-cli 2>&1
") || fail "Local app-cli install failed"
log "Override: $(echo "$OVERRIDE_OUTPUT" | tail -1)"

# ---------------------------------------------------------------------------
# Step 5: Unit tests — _build_include_source_resolver
# ---------------------------------------------------------------------------

echo ""
log "============================================================"
log " UNIT TESTS: _build_include_source_resolver"
log "============================================================"
echo ""

UNIT_EXIT=0
UNIT_OUTPUT=$(docker exec "$CONTAINER_NAME" bash -c "
    /root/.local/share/uv/tools/amplifier/bin/python3 - << 'PYEOF'
from amplifier_app_cli.lib.bundle_loader.prepare import _build_include_source_resolver

GREEN = '\033[0;32m'
RED   = '\033[0;31m'
NC    = '\033[0m'
failures = []

def check(label, got, expected):
    if got == expected:
        print(f'{GREEN}[PASS]{NC} {label}')
        print(f'       {got}')
    else:
        print(f'{RED}[FAIL]{NC} {label}')
        print(f'  got:      {got!r}')
        print(f'  expected: {expected!r}')
        failures.append(label)

# Test 1: local path + #subdirectory= -> convert to path component (the fix)
resolver = _build_include_source_resolver({'superpowers': '/local/superpowers'})
check(
    'local path + #subdirectory= -> path component',
    resolver('git+https://github.com/microsoft/amplifier-bundle-superpowers@main'
             '#subdirectory=behaviors/superpowers-methodology.yaml'),
    '/local/superpowers/behaviors/superpowers-methodology.yaml'
)

# Test 2: git URL override + #subdirectory= -> fragment preserved (existing behaviour)
resolver2 = _build_include_source_resolver(
    {'superpowers': 'git+https://github.com/myfork/superpowers@dev'}
)
check(
    'git URL override + #subdirectory= -> fragment preserved',
    resolver2('git+https://github.com/microsoft/amplifier-bundle-superpowers@main'
              '#subdirectory=behaviors/superpowers-methodology.yaml'),
    'git+https://github.com/myfork/superpowers@dev#subdirectory=behaviors/superpowers-methodology.yaml'
)

# Test 3: local path, no fragment -> pass through unchanged
check(
    'local path + no fragment -> pass through',
    resolver('git+https://github.com/microsoft/amplifier-bundle-superpowers@main'),
    '/local/superpowers'
)

# Test 4: no matching key -> None
result4 = resolver('git+https://github.com/microsoft/amplifier-foundation@main')
if result4 is None:
    print(f'{GREEN}[PASS]{NC} no key match -> None')
else:
    print(f'{RED}[FAIL]{NC} no key match -> None  (got: {result4!r})')
    failures.append('no match returns None')

print()
if failures:
    print(f'FAILED: {len(failures)} test(s) failed')
    exit(1)
else:
    print('All unit tests passed.')
PYEOF
") || UNIT_EXIT=$?

echo "$UNIT_OUTPUT"
[[ "$UNIT_EXIT" -ne 0 ]] && fail "Unit tests FAILED"

# ---------------------------------------------------------------------------
# Step 6: Session smoke test
# ---------------------------------------------------------------------------

echo ""
log "============================================================"
log " SESSION SMOKE TEST"
log " Prompt: '$SMOKE_PROMPT'"
log "============================================================"
echo ""

SMOKE_EXIT=0
SMOKE_OUTPUT=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    timeout 120 amplifier run '$SMOKE_PROMPT' 2>&1
") || SMOKE_EXIT=$?

echo "$SMOKE_OUTPUT" | tail -30
echo ""

# Fail on the specific regression we fixed
if echo "$SMOKE_OUTPUT" | grep -q "#subdirectory="; then
    fail "REGRESSION: '#subdirectory=' appeared in output — fix did not take effect"
fi

# Fail on Python exceptions
if echo "$SMOKE_OUTPUT" | grep -qE "Traceback|AttributeError|ImportError|ModuleNotFoundError"; then
    fail "Python exception detected in session output"
fi

[[ "$SMOKE_EXIT" -eq 124 ]] && fail "Session timed out after 120s"

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

pass "============================================================"
pass " SMOKE TEST PASSED"
pass " $INSTALLED_VERSION"
pass " Unit tests: 4/4   Session: OK"
pass "============================================================"
echo ""
