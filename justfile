# Task runner for amplifier-app-cli — see AGENTS.md for the verify loop.

# Lint, type-check, and run the default test suite (~35s total).
check:
    uv run ruff check amplifier_app_cli tests
    uv run pyright
    uv run pytest -q

# check + the PTY integration suite (needs a real POSIX terminal).
check-full: check
    uv run pytest -m integration -q

# Auto-format and apply safe lint fixes.
fmt:
    uv run ruff format amplifier_app_cli tests
    uv run ruff check --fix amplifier_app_cli tests

# Regenerate the readable snapshot goldens (tests/goldens/**/*.txt) after an
# intentional presentation change (docs/designs/tui-v3-cohesive.md), then
# re-verify them. Review the resulting golden diff as a UI diff.
regen-goldens:
    uv run python tests/regen_goldens.py --write
    uv run pytest tests/test_transcript_golden_widths.py tests/test_footer_golden_widths.py -q

# List goldens that would change without writing them (exit 1 if any).
goldens-status:
    uv run python tests/regen_goldens.py
