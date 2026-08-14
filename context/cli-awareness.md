# Amplifier CLI: there is an expert for this

You are running inside the **Amplifier CLI application**. The CLI itself —
its commands, flags, config, and session machinery — is a domain with a
dedicated expert that carries the authoritative, version-matched docs.

**Delegate to `app-cli:cli-expert`** when the user asks how the CLI itself
behaves. Signals include:

- Switching models or providers, provider pinning, `/provider`, `amplifier provider`
- Slash commands (`/config`, `/mode`, `/goal`, `/fork`, `/skills`, `/agents`, `/status`)
- Sessions: resuming, saving, forking, naming, where session state lives
- Bundles and skills: what is loaded, precedence, `amplifier bundle`
- `@mention` context loading and which files a session actually loaded
- Output formats (`--output json`, json-trace) and automation
- What tools/providers a spawned sub-agent inherits

**Do not answer these from memory.** CLI behavior is version-specific and
changes between releases; the expert holds the docs that shipped with *this*
installed CLI. Guessing produces confident, wrong instructions — the exact
failure this expert exists to prevent.

If the question is about *your own task* (writing code, debugging the user's
project), that is not this domain — handle it normally.
