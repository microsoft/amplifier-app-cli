---
name: doctor
description: >-
  Diagnose an Amplifier CLI environment through the CLI doctor agent.
  USE WHEN a user requests CLI runtime, settings or missing-capability
  inspection. DO NOT USE for project bugs or other hosts.
user-invocable: true
disable-model-invocation: true
argument-hint: "[symptom or diagnostic focus]"
---

# CLI Doctor

## Request

$ARGUMENTS

Treat the request as natural-language diagnostic input, not shell commands
or CLI arguments. An empty request means passive CLI inspection.

## 1. Delegate the diagnosis

Delegate to `app-cli:cli-doctor`. Include the request verbatim as request
data, relevant conversation context, and the intended executable,
invocation directory and affected session when already known.

The agent owns diagnosis and loads `amplifier-config` when needed.
Do not duplicate either workflow here.

If delegation or the agent is unavailable, report that limitation.
Do not install a replacement or silently substitute another workflow.

**Success criteria:** The doctor agent returns evidence or a concrete
collection limitation for the intended target.

## 2. Present the result

Return the agent's findings, unknowns and smallest supported next step
faithfully. Diagnosis does not authorize changes or live probes.

Begin with the agent's limitation: **Health not assessed; no repair verdict
from passive collection.** Preserve that meaning in your own summary.
Successful collection or an empty issues list does not establish that
nothing needs repairing. Do not add a health or repair verdict of your own.

**Success criteria:** The response distinguishes collected declarations
from effective settings and live behavior, without inventing a health
or repair verdict.
