from __future__ import annotations

import ast
from pathlib import Path
import tomllib

_SOURCE_ROOT = Path(__file__).parents[1] / "amplifier_app_cli"
_PROJECT_ROOT = _SOURCE_ROOT.parent
_FOUNDATION_REVISION = "dc010423d010da9a52e1b49808a1865666008c25"
_FOUNDATION_GIT_URL = "https://github.com/microsoft/amplifier-foundation"
_FOUNDATION_API = (
    "RESULT_START_MARKER",
    "RESULT_END_MARKER",
    "AmplifierSession",
    "_build_child_env",
    "_extract_framed_result",
    "_get_semaphore",
    "_run_child_session",
    "_sanitize_error",
    "_validate_project_path",
    "serialize_subprocess_config",
)
_PRIVATE_ADAPTER_CONTRACTS = {
    Path("runtime/amplifier_compat.py"): frozenset({"_sanitize_for_json"}),
    Path("runtime/subprocess_adapter.py"): frozenset(
        {
            "_build_child_env",
            "_extract_framed_result",
            "_get_semaphore",
            "_run_child_session",
            "_sanitize_error",
            "_validate_project_path",
        }
    ),
}
_BANNED_EXTERNAL_ATTRIBUTES = {
    "_activated",
    "_activator",
    "_added_paths",
    "_build_child_env",
    "_extract_framed_result",
    "_get_semaphore",
    "_install_state",
    "_run_child_session",
    "_sanitize_for_json",
    "_sanitize_error",
    "_validate_project_path",
}


def test_private_amplifier_apis_are_quarantined_in_compat_adapters() -> None:
    violations: list[str] = []
    observed_contracts = {path: set[str]() for path in _PRIVATE_ADAPTER_CONTRACTS}
    for source_path in sorted(_SOURCE_ROOT.rglob("*.py")):
        relative = source_path.relative_to(_SOURCE_ROOT)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if _has_private_amplifier_segment(node.module):
                    violations.append(f"{relative}:{node.lineno} imports {node.module}")
                for imported in node.names:
                    if node.module.startswith("amplifier") and imported.name.startswith(
                        "_"
                    ):
                        violations.append(
                            f"{relative}:{node.lineno} imports {node.module}."
                            f"{imported.name}"
                        )
            elif isinstance(node, ast.Import):
                for imported in node.names:
                    if _has_private_amplifier_segment(imported.name):
                        violations.append(
                            f"{relative}:{node.lineno} imports {imported.name}"
                        )
            elif isinstance(node, ast.Attribute):
                if node.attr in _BANNED_EXTERNAL_ATTRIBUTES or (
                    node.attr.startswith("_")
                    and _is_adapter_runtime_reference(relative, node.value)
                ):
                    _record_private_access(
                        relative,
                        node.lineno,
                        node.attr,
                        observed_contracts,
                        violations,
                    )
                rendered = ast.unparse(node)
                if "._bundle._paths" in rendered or rendered.endswith(
                    "resolver._paths"
                ):
                    violations.append(
                        f"{relative}:{node.lineno} reaches into resolver paths"
                    )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id not in {"getattr", "setattr", "hasattr"}:
                    continue
                if len(node.args) < 2:
                    continue
                attribute = node.args[1]
                if (
                    isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                    and (
                        attribute.value in _BANNED_EXTERNAL_ATTRIBUTES
                        or (
                            attribute.value.startswith("_")
                            and _is_adapter_runtime_reference(relative, node.args[0])
                        )
                    )
                ):
                    _record_private_access(
                        relative,
                        node.lineno,
                        attribute.value,
                        observed_contracts,
                        violations,
                    )

    assert violations == [], "\n".join(violations)
    assert observed_contracts == {
        path: set(names) for path, names in _PRIVATE_ADAPTER_CONTRACTS.items()
    }


def test_foundation_subprocess_adapter_contract_is_exact() -> None:
    required_api = _literal_module_constant(
        _SOURCE_ROOT / "runtime/subprocess_adapter.py", "_REQUIRED_API"
    )
    assert required_api == _FOUNDATION_API
    assert {name for name in required_api if name.startswith("_")} == (
        _PRIVATE_ADAPTER_CONTRACTS[Path("runtime/subprocess_adapter.py")]
    )


def test_foundation_source_is_pinned_to_tested_revision() -> None:
    pyproject = tomllib.loads(
        (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    source = pyproject["tool"]["uv"]["sources"]["amplifier-foundation"]
    assert source == {"git": _FOUNDATION_GIT_URL, "rev": _FOUNDATION_REVISION}

    lock = tomllib.loads((_PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(
        package
        for package in lock["package"]
        if package["name"] == "amplifier-foundation"
    )
    assert package["source"] == {
        "git": (
            f"{_FOUNDATION_GIT_URL}?rev={_FOUNDATION_REVISION}#{_FOUNDATION_REVISION}"
        )
    }


def _record_private_access(
    relative: Path,
    line: int,
    attribute: str,
    observed_contracts: dict[Path, set[str]],
    violations: list[str],
) -> None:
    allowed = _PRIVATE_ADAPTER_CONTRACTS.get(relative, frozenset())
    if attribute in allowed:
        observed_contracts[relative].add(attribute)
        return
    violations.append(f"{relative}:{line} accesses private API {attribute}")


def _has_private_amplifier_segment(module: str) -> bool:
    return module.startswith("amplifier") and any(
        segment.startswith("_") for segment in module.split(".")
    )


def _is_adapter_runtime_reference(relative: Path, node: ast.expr) -> bool:
    expected_names = {
        Path("runtime/amplifier_compat.py"): {"hooks_logging"},
        Path("runtime/subprocess_adapter.py"): {"foundation", "runtime"},
    }.get(relative, set())
    return isinstance(node, ast.Name) and node.id in expected_names


def _literal_module_constant(source_path: Path, name: str) -> object:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not defined in {source_path}")
