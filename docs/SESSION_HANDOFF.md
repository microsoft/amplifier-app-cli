# Continue a session in another application

CLI and compatible hosts such as Amplifier Unified share Foundation's local
session ownership lock. When another app owns a session, interactive startup
identifies the owner and asks whether to request takeover. The current owner
saves and releases automatically; the CLI starts only after acquiring ownership
and reading the latest native history.

For scripts, request takeover explicitly:

```sh
amplifier run --resume SESSION_ID --takeover --handoff-timeout 30 "Continue the work"
```

`--takeover` and `--handoff-timeout` are also available on `continue` and
`session resume`. Noninteractive runs never ask an unexpected takeover question.
The wait is bounded; another contender, unsupported owner, failed save, or
timeout produces an error without launching resumed execution. A release already
underway may finish after the requester times out.

While running, CLI serves release requests at an acquisition-specific Foundation
endpoint. A request wakes idle input, stops steering input, and requests graceful
cancellation of current execution. The normal cleanup path saves native history,
flushes/cleans up, and releases last. It then names the requesting application in
its exit notice. If a tool cannot finish yet, CLI retains ownership while waiting;
it never force-unlocks to meet the requester's timeout.

The current transcript/metadata format and configurable Foundation state root
remain in use. Native-only platforms/identities remain outside shared ownership.
This requires the matching Foundation handoff API. A crashed lock holder releases
its OS lock automatically; history recovery still depends on the last valid save.

Cross-repository development tests must install an explicit Foundation override
into the test virtualenv. Do not inject another checkout through `sys.path`.
