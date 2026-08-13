# Switching models mid-conversation

**Experimental.** `/provider use <name>` changes which model answers you, without
restarting your session. `/provider auto` puts it back.

---

## The idea in one line

Each entry in your provider list is **one account plus one model**, and you give it
a short name you can type.

That means "my work account's Opus" and "my personal account's Opus" are two
entries, not one. So are "Opus" and "Haiku" on the same account.

---

## Step 1 — set up your providers

Providers live in `~/.amplifier/settings.yaml` under `config.providers`. Here is a
real, working example: **two different Anthropic accounts**, four models, four
names you can type.

```yaml
config:
  providers:
    # --- main account ---
    - id: opus
      module: provider-anthropic
      config:
        api_key: ${ANTHROPIC_API_KEY}
        default_model: claude-opus-5
        priority: 1              # lowest number wins -> this is your default

    - id: sonnet
      module: provider-anthropic
      config:
        api_key: ${ANTHROPIC_API_KEY}
        default_model: claude-sonnet-5
        priority: 2

    - id: haiku
      module: provider-anthropic
      config:
        api_key: ${ANTHROPIC_API_KEY}
        default_model: claude-haiku-4-5
        priority: 3

    # --- second account, different key ---
    - id: fable
      module: provider-anthropic
      config:
        api_key: ${ANTHROPIC_FABLE_API_KEY}   # <- the only thing that differs
        default_model: claude-fable-5
        priority: 4
```

Two things are doing all the work:

| Field | What it does |
|---|---|
| `id` | The name you type: `/provider use fable` |
| `priority` | Lowest number is your default. Here that's `opus`. |

Everything else — the key, the model, the module — is just configuration for that
one entry.

Check it worked:

```bash
amplifier provider list     # should show all four, ★ on your default
amplifier provider test     # confirms each key actually works
```

---

## Step 2 — use it

Inside an interactive session:

```
> /provider
   ...shows what's available; ★ marks what your priority order favors

> /provider use fable
📌 pinned: fable
   experimental · scope: this conversation only · /provider for details

[📌 fable]> what do you think of this design?
   ...answered by claude-fable-5

[📌 fable]> /provider auto
unpinned (was fable)

>              ← back to your default (opus)
```

While pinned, your prompt shows `[📌 fable]` and every reply's usage line ends with
`· 📌 pinned`. You never have to guess which model you're talking to.

Note the usage line names the **model** (`claude-fable-5`), while the prompt names
your **id** (`fable`). If you ever give two entries the same model, the prompt is
the one that tells them apart.

---

## Naming your providers

The `id` is yours to choose. Pick names you'd actually want to type.

```yaml
id: opus              # short — good when you have one account
id: fable             # short — the model IS the reason this entry exists

id: opus-work         # when the same model exists on two accounts
id: opus-home

id: anthropic-home    # when the account matters more than the model
```

A few things that help:

- **Short beats descriptive.** You'll type this a lot.
- **Name what actually differs.** If you have one account, name by model
  (`opus`, `haiku`). If you have several, add the account (`opus-work`).
- **`id` must be unique.** Two entries with the same `id` will collide.
- **`id` is optional** — without it the name defaults to the module
  (`anthropic`), which is fine for exactly one entry and confusing for more.
  If you have more than one entry per provider, set `id` on all of them.

---

## Setting your default

Your default is simply the entry with the **lowest `priority` number**.

To make Opus your default and keep Fable on hand, give Opus `priority: 1` and
Fable a higher number. `/provider auto` always returns you to whatever this says.

Nothing about pinning changes your default — a pin lasts for the session and
disappears when you exit. To change your default permanently, edit
`settings.yaml` (or use `amplifier provider edit`).

---

## Limits worth knowing

**Same vendor only.** You can switch freely among Anthropic entries, or among
OpenAI entries — but not from Anthropic to OpenAI mid-conversation. Your
conversation history contains vendor-specific data that other vendors reject,
and switching across vendors can leave a session unable to continue. Amplifier
refuses the switch up front and tells you why.

To use a different vendor, start a new session with `amplifier run -p <name>`.

**Session only.** A pin lasts until you exit. It does not persist and does not
change your saved settings.

**Top-level only.** Pinning affects the conversation you're having. Sub-agents,
model-role routing, and `/goal` are unaffected — they keep using whatever your
configuration says.

**Depends on your orchestrator.** Pinning is a capability the orchestrator
provides, and not every orchestrator does. If yours doesn't, `/provider` tells
you plainly instead of pretending the switch worked. The default orchestrator
(`loop-streaming`) supports it.

---

## Two `provider` commands, two jobs

They look similar and do different things:

| | Where | What it does |
|---|---|---|
| `amplifier provider ...` | Your shell | Manages saved configuration — `add`, `edit`, `list`, `test`, `remove` |
| `/provider ...` | Inside a session | Pins the current conversation — `use`, `auto`. Changes nothing on disk. |
| `amplifier run -p <name>` | Your shell | Starts one session on a specific provider |

Rule of thumb: **`amplifier provider`** changes what's available tomorrow.
**`/provider`** changes who's answering right now.

---

## Quick reference

```
/provider                 show providers and what's active
/provider use <name>      pin this conversation to <name>
/provider auto            unpin — back to your default

amplifier provider list   show configured providers
amplifier provider test   check every key works
amplifier run -p <name>   start a session on <name>
```
