# Run events: build plan

Implements [run-events.md](run-events.md). Phases ship in order; each is useful alone.

## Phase 0 — spike

Nothing below is written until the loop is proven by hand against a live run.
The source has been read, never run.

- Install `uv`, `uv tool install conductor-cli`, restore a pipeline folder.
- `ictus run -b`, read `port` and `event_log_path` from the fleet run record.
- Resolve the token: `~/.conductor/runs/dashboard-<port>.token`, else
  `CONDUCTOR_GATE_TOKEN`.
- Connect `/ws` presenting the token as an `Authorization` header or `?token=`
  query param, with a `Host` the `OriginHostGuard` accepts.
- Observe `gate_presented`. Send `gate_response` carrying its `agent_name` and
  `prompt_id`. Confirm the gate resolves and `gate_resolved` arrives.
- Disconnect on `workflow_completed`. Confirm the process exits ~30s later.

Output: a throwaway script and a recorded event log, kept as a test fixture.

## Phase 1 — declaration

Engine-neutral. `graph/` names no Conductor event.

`graph/signals.py` — `RunSignal`, a `StrEnum` mirroring `NodeKind`:

| Signal | Conductor events |
|---|---|
| `RUN_STARTED` | `workflow_started` |
| `RUN_FINISHED` | `workflow_completed` |
| `RUN_FAILED` | `workflow_failed` |
| `DECISION_NEEDED` | `gate_presented`, `questions_presented` |
| `DECISION_MADE` | `gate_resolved`, `questions_completed` |
| `STEP_BLOCKED` | `agent_paused` |
| `STEP_FAILED` | `agent_failed`, `agent_validation_failed`, `script_failed`, `set_failed`, `wait_failed`, `mcp_failed`, `subworkflow_failed` |
| `BUDGET_EXCEEDED` | `budget_exceeded` |

The mapping lives in `interfaces/conductor/`, not beside the enum.

`graph/requirements.py` — add `Notifier(name, purpose, signals, env, setup_hint)`.

- `pipeline.require_notifier(...)`, parallel to `require_mcp`.
- `Pipeline.notifiers` and `all_notifiers` (walks stages), mirroring
  `all_mcp_servers`.
- `purpose` required, same rule as `McpServer` and `Executable`.

`interfaces/__init__.py` — `Capabilities.signals: frozenset[RunSignal]`.
A pipeline subscribing to a signal the backend cannot report is refused at
composition, like an unsupported `NodeKind`.

Endpoints are declared as `EnvVar`, not literals. Preflight's existing env check
then covers notifiers with no signature change; `--probe` additionally POSTs a
test payload.

## Phase 2 — the watcher

`interfaces/conductor/events.py`, beside `trace.py`. Conductor's vocabulary is
already permitted there.

- Discover runs from fleet run records (`run_id`, `port`, `pid`, `mode`,
  `workflow_name`, `event_log_path`).
- Resolve token and connect `/ws` per run.
- Map event → `RunSignal`. Unmapped events are dropped, not forwarded.
- **Close the socket on `workflow_completed` / `workflow_failed`.** Non-negotiable;
  a held connection stops the run reaping.
- Reconnect with backoff on drop. A new connection cancels a pending grace timer.

`cli.py` — `ictus watch`. Long-lived, attaches to every discoverable run, detaches
per run on terminal event. Does not change `ictus run`; ictus stays a compiler
that exits.

## Phase 3 — delivery

`src/ictus/notify/` — a new top-level package. Not under `interfaces/`: that
boundary is the engine, this one is the audience.

- `webhook.py` first. HTTP POST via `urllib.request`. No dependency.
- `slack.py` is a webhook URL plus payload shape. No Slack SDK for outbound.
- One module per destination, one destination per module, as `stdlib/` does.
- A delivery failure is logged and never raised into the watcher loop.

## Phase 4 — inbound

Answering from Slack. Needs a Slack app; scope separately.

- `gate_response` with `agent_name` + `prompt_id` from the originating
  `gate_presented`. A mismatch is dropped by `_validate_gate_target`.
- `dialog_message` / `dialog_decline` drive `remediate` conversations.
- `iteration_limit_response` answers the `max_iterations` prompt.
- Either `/ws` or `POST /api/gate-respond`; both need the token.

## Tests

- Signal mapping is total: every `RunSignal` maps to at least one event, every
  mapped event name exists in the Phase 0 fixture.
- `require_notifier` refuses an empty `purpose`; a signal outside
  `Capabilities.signals` refuses at composition.
- Watcher against a fake local WebSocket server: closes on terminal event,
  reconnects on drop, drops unmapped events, rejects a stale `prompt_id`.
- Preflight reports an unset notifier `EnvVar` as blocking.
- `test_docs.py` already enforces the catalogue — `STDLIB.md` and `README.md`
  must list anything new.

## Decisions

- **WebSocket client dependency.** No stdlib WS client. `websockets` is the
  candidate. Alternative is polling `GET /api/state`, which needs no token and
  no dependency and cannot block reaping, at the cost of push and of
  re-transferring the full history each poll. Recommend `websockets`; the
  pin rule applies.
- **Phase 4 transport.** Slack Socket Mode (`slack_sdk`, no public endpoint) or
  an HTTP endpoint (public hosting, signature verification). Not needed before
  Phase 4.
- **Watcher supervision.** Out of scope here. systemd or equivalent.
