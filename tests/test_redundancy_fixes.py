"""Tests for redundancy fixes: session_runner.py setLevel and main.py display_validation_error guards.

These tests verify:
1. session_runner.py except block has no redundant core_logger.setLevel() before display_validation_error
2. main.py interactive_chat() uses `if not display_validation_error(...)` with fallback
3. main.py execute_single() uses `if not display_validation_error(...)` with fallback
"""

import ast
import importlib
import inspect
import textwrap


# ---------------------------------------------------------------------------
# Helpers: AST-based source inspection
# ---------------------------------------------------------------------------


def _get_function_source(module, func_name: str) -> str:
    """Return the source code of a function from a module."""
    func = getattr(module, func_name)
    return inspect.getsource(func)


def _get_except_handlers(source: str, exception_name: str) -> list[ast.ExceptHandler]:
    """Parse source and return all except handlers matching exception_name."""
    tree = ast.parse(textwrap.dedent(source))
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                continue
            # Handle both Name and Tuple exception types
            names = []
            if isinstance(node.type, ast.Name):
                names.append(node.type.id)
            elif isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name):
                        names.append(elt.id)
            if exception_name in names:
                handlers.append(node)
    return handlers


# ---------------------------------------------------------------------------
# Test 1: session_runner.py - no redundant setLevel in except block
# ---------------------------------------------------------------------------


class TestSessionRunnerRedundantSetLevel:
    """The except (ModuleValidationError, RuntimeError) block in _create_bundle_session
    should NOT have a core_logger.setLevel() call before display_validation_error.
    The finally block already handles resetting the log level."""

    def test_except_block_no_redundant_setlevel(self):
        """The except block should start with the `if not display_validation_error(...)` call,
        not with a redundant core_logger.setLevel(original_level)."""
        from amplifier_app_cli import session_runner

        source = _get_function_source(session_runner, "_create_bundle_session")
        handlers = _get_except_handlers(source, "ModuleValidationError")

        assert len(handlers) == 1, "Expected exactly one ModuleValidationError handler"
        handler = handlers[0]

        # The first statement in the except block should NOT be a setLevel call.
        # It should be an `if` statement (the `if not display_validation_error(...)` guard).
        first_stmt = handler.body[0]

        # If the redundant setLevel is still there, the first statement will be an Expr
        # with a Call to setLevel, not an If statement.
        assert not (
            isinstance(first_stmt, ast.Expr)
            and isinstance(first_stmt.value, ast.Call)
            and isinstance(first_stmt.value.func, ast.Attribute)
            and first_stmt.value.func.attr == "setLevel"
        ), "Found redundant core_logger.setLevel() as first statement in except block"

        # Positive check: first statement should be an If (the guard pattern)
        assert isinstance(first_stmt, ast.If), (
            f"Expected first statement in except block to be an If (guard pattern), "
            f"got {type(first_stmt).__name__}"
        )


# ---------------------------------------------------------------------------
# Test 2: main.py interactive_chat() - display_validation_error guard pattern
# ---------------------------------------------------------------------------


class TestInteractiveChatValidationErrorGuard:
    """In interactive_chat(), the except ModuleValidationError block should use
    `if not display_validation_error(...)` with a console.print fallback,
    not a bare display_validation_error() call."""

    def test_except_block_uses_if_not_guard(self):
        """The except ModuleValidationError block should use `if not display_validation_error(...)`."""
        main_module = importlib.import_module("amplifier_app_cli.main")

        source = _get_function_source(main_module, "interactive_chat")
        handlers = _get_except_handlers(source, "ModuleValidationError")

        assert len(handlers) >= 1, "Expected at least one ModuleValidationError handler"
        handler = handlers[0]

        # The first statement should be an If with a `not` test on display_validation_error
        first_stmt = handler.body[0]
        assert isinstance(first_stmt, ast.If), (
            f"Expected `if not display_validation_error(...)` guard, "
            f"got {type(first_stmt).__name__}"
        )

        # Check it's `if not display_validation_error(...)`
        test = first_stmt.test
        assert isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not), (
            "Expected `if not ...` pattern in the guard"
        )


# ---------------------------------------------------------------------------
# Test 3: main.py execute_single() - display_validation_error guard pattern
# ---------------------------------------------------------------------------


class TestExecuteSingleValidationErrorGuard:
    """In execute_single(), the else branch of the except ModuleValidationError block
    should use `if not display_validation_error(...)` with a console.print fallback."""

    def test_else_branch_uses_if_not_guard(self):
        """The else branch should use `if not display_validation_error(...)` guard."""
        main_module = importlib.import_module("amplifier_app_cli.main")

        source = _get_function_source(main_module, "execute_single")
        handlers = _get_except_handlers(source, "ModuleValidationError")

        assert len(handlers) >= 1, "Expected at least one ModuleValidationError handler"
        handler = handlers[0]

        # The handler should have an if/else for output_format check
        # The else body should contain `if not display_validation_error(...)`
        # Find the if statement checking output_format
        format_if = handler.body[0]
        assert isinstance(format_if, ast.If), "Expected if statement checking output_format"
        assert len(format_if.orelse) >= 1, "Expected else branch"

        # In the else branch, first statement should be `if not display_validation_error(...)`
        else_first = format_if.orelse[0]
        assert isinstance(else_first, ast.If), (
            f"Expected `if not display_validation_error(...)` in else branch, "
            f"got {type(else_first).__name__}"
        )

        test = else_first.test
        assert isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not), (
            "Expected `if not ...` pattern in the guard"
        )