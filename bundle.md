---
bundle:
  name: app-cli
  version: 0.1.0
  description: >
    Expert consultant for the Amplifier CLI application itself — provider
    pinning, slash commands, sessions, context loading, output formats,
    and spawn precedence.

# Include this repo's own behavior by default, so loading the bundle directly
# gets the same capability the CLI auto-composes. (The CLI composes the
# BEHAVIOR yaml, not this file — see behaviors/cli-expertise.yaml.)
includes:
  - bundle: app-cli:behaviors/cli-expertise
---

# Amplifier CLI Bundle

A bundle **overlaid on the existing `amplifier-app-cli` repo** — `behaviors/`,
`agents/`, and `context/` sit at the repo root as siblings of the
`amplifier_app_cli/` Python package and of `docs/`, matching every other repo
in the ecosystem (`amplifier`, `amplifier-core`, `amplifier-foundation`).

## Why the expert lives in this repo

A CLI expert's authority is only as good as its version match with the
installed CLI. A separately-versioned bundle pinned at `@main` can document
`/provider` behavior the installed CLI does not have, or miss behavior it
does. Shipping here makes that skew structurally impossible: the expert that
answers your question is the one that shipped in your wheel.

`docs/` being a natural sibling is the payoff — `agents/cli-expert.md`
@-mentions the repo's real, canonical docs. There is no copy to drift.

## How it reaches an installed user

Sibling repos are *cloned* into `~/.amplifier/cache/`, so their repo-root
bundle dirs are on disk at runtime. **This repo is not** — it is installed
with `uv tool install`, which ships only the wheel.

So `pyproject.toml` force-includes these dirs into the wheel under
`amplifier_app_cli/_bundle/`:

```
[tool.hatch.build.targets.wheel.force-include]
"bundle.md"  = "amplifier_app_cli/_bundle/bundle.md"
"behaviors"  = "amplifier_app_cli/_bundle/behaviors"
"agents"     = "amplifier_app_cli/_bundle/agents"
"context"    = "amplifier_app_cli/_bundle/context"
"docs"       = "amplifier_app_cli/_bundle/docs"
```

The repo root stays the single source of truth; the wheel carries a copy.
`_build_app_cli_behaviors()` in `amplifier_app_cli/runtime/config.py` resolves
whichever layout is present — `_bundle/` when installed, repo root in a dev
checkout — and **raises** if neither is found, so a packaging regression is
loud rather than a silently missing expert.

The target is `_bundle/` *inside* the package deliberately: force-including
to a top-level `amplifier_app_cli/agents` would create a directory with no
`__init__.py` that shadows the real package namespace.

## Structure

```
amplifier-app-cli/
├── bundle.md                    # this file — namespace: app-cli
├── behaviors/cli-expertise.yaml # the composable capability
├── agents/cli-expert.md         # context sink: @-mentions docs/
├── context/cli-awareness.md     # thin always-on pointer
├── docs/                        # the repo's existing canonical docs
└── amplifier_app_cli/           # the Python package
```

## Scope

This bundle answers "how does the Amplifier CLI work?" It deliberately does
**not** promote features or surface tips — that is a separate concern and
belongs in a separate bundle.
