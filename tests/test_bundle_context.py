from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from amplifier_app_cli.runtime.bundle_context import build_bundle_context
from amplifier_app_cli.runtime.bundle_context import normalize_bundle_context


class _PublicResolver:
    def __init__(self, paths: dict[str, Path]) -> None:
        self.paths = paths
        self.requests: list[str] = []

    def get_module_source(self, module_id: str) -> str | None:
        self.requests.append(module_id)
        path = self.paths.get(module_id)
        return str(path) if path is not None else None


def test_bundle_context_uses_public_mount_plan_resolver_and_bundle_paths(
    tmp_path,
) -> None:
    resolver = _PublicResolver(
        {
            "provider-openai": tmp_path / "provider",
            "tool-filesystem": tmp_path / "tool",
            "loop-basic": tmp_path / "loop",
        }
    )
    bundle = SimpleNamespace(
        name="foundation",
        base_path=tmp_path / "foundation",
        source_base_paths={"recipes": tmp_path / "recipes"},
    )
    mount_plan = {
        "providers": [{"module": "provider-openai"}],
        "tools": [{"module": "tool-filesystem"}],
        "session": {"orchestrator": {"module": "loop-basic"}},
    }

    context = build_bundle_context(
        mount_plan,
        resolver,
        bundle=bundle,
        bundle_package_paths=[tmp_path / "bundle-src"],
    )

    assert context["module_paths"] == {
        "loop-basic": str(tmp_path / "loop"),
        "provider-openai": str(tmp_path / "provider"),
        "tool-filesystem": str(tmp_path / "tool"),
    }
    assert resolver.requests == ["loop-basic", "provider-openai", "tool-filesystem"]
    assert context["mention_mappings"] == {
        "foundation": str(tmp_path / "foundation"),
        "recipes": str(tmp_path / "recipes"),
    }
    assert context["bundle_package_paths"] == [str(tmp_path / "bundle-src")]


def test_bundle_context_extends_and_copies_serialized_parent_state(tmp_path) -> None:
    base = {
        "module_paths": {"parent": str(tmp_path / "parent")},
        "mention_mappings": {"base": str(tmp_path)},
        "bundle_package_paths": [str(tmp_path / "src")],
    }
    resolver = _PublicResolver({"child": tmp_path / "child"})

    context = build_bundle_context(
        {"tools": [{"module": "child"}]},
        resolver,
        base_context=base,
    )

    assert context["module_paths"] == {
        "parent": str(tmp_path / "parent"),
        "child": str(tmp_path / "child"),
    }
    assert context is not base
    assert normalize_bundle_context(context) == context
