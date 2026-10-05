# Run events

Side effects attach to a live run over the dashboard WebSocket.

Verified against conductor-cli 0.1.41 (`microsoft/conductor`). Version-specific.

## Surface

- `/ws` on the run's dashboard. Bidirectional.
- `WebDashboard` subscribes to the engine's `WorkflowEventEmitter` and
  rebroadcasts every event (`web/server.py:188`). Same feed the dashboard renders.
- An event is `{type, timestamp, data}`.
- A run-scoped `RunRedactor` scrubs payloads before dispatch. Secrets do not
  reach subscribers.
- REST alongside: `/api/state` (history), `/api/gate-status`, `/api/gate-respond`,
  `/api/guidance`, `/api/stop`, `/api/kill`, `/api/resume`, `/api/logs`.

## Events received

| Event | Carries |
|---|---|
| `gate_presented` | `agent_name`, `prompt`, `prompt_id`, `options`, `option_details`, `step_type` |
| `gate_resolved` | `agent_name`, response |
| `questions_presented` / `questions_completed` | ask-human steps |
| `workflow_started` / `workflow_completed` / `workflow_failed` | run lifecycle |
| `agent_started` / `agent_completed` / `agent_failed` | step lifecycle |
| `agent_paused`, `agent_timeout`, `agent_retry`, `agent_validation_failed` | step trouble |
| `budget_exceeded` | spend ceiling hit |
| `checkpoint_saved` / `checkpoint_save_failed` | resumability |
| `guidance_received` | mid-run steer |

Also `script_*`, `set_*`, `wait_*`, `mcp_*`, `subworkflow_*`, `parallel_*`,
`for_each_*` per step kind. ~45 types total.

`option_details` entries carry `label`, `value`, `route`, `prompt_for`,
`multiline`, so free text on a choice arrives with the gate.

Emission: `engine/workflow.py:4153` (questions), `:5755` (gates), `:5772`
(`gate_resolved`).

## Messages sent

- `gate_response` — approval, choice, free text via `prompt_for` / `multiline`.
- `dialog_message` / `dialog_decline` — multi-turn conversation. The surface
  ictus's `remediate` node uses via Conductor's `dialog`.
- `iteration_limit_response` — answers "`max_iterations` reached, continue?".

A response is rejected unless `agent_name` and `prompt_id` match the gate
currently waiting (`_validate_gate_target`); it is logged, not applied. Carry
both from `gate_presented` through to the response.

## Reaping

A `--web-bg` process exits only when all four hold
(`_maybe_start_grace_timer`, `web/server.py:1437`):

1. the run is `--web-bg`
2. `_workflow_completed` — root-level `workflow_completed` / `workflow_failed` seen
3. `_connections` is empty — zero WebSocket clients
4. no grace timer already armed

Then `_BG_GRACE_SECONDS = 30`, then `_bg_event` fires and the process exits.

- A held `/ws` connection never satisfies (3). The process never exits.
- Leaked per run: one process holding `_event_history` (uncapped, retained for
  process life), one TCP listener, one unsettled fleet run record.

**Close the socket on `workflow_completed` / `workflow_failed`.**

- A new connection cancels a pending grace timer.
- `POST /api/kill` sets `_bg_event` directly.
- The timer also arms from `_on_event` on terminal events, so a run nobody
  connected to still shuts down.

## Discovery

- Fleet run records, keyed by `run_id` (`fleet/records.py`): `workflow_name`,
  `started_at`, `pid`, `mode`, `port` (nullable), `event_log_path`.
- Dashboard port defaults to `0` — OS auto-select. Concurrent runs do not collide.
- Auth: per-run `secrets.token_urlsafe(32)`, plus Origin/Host validation on every
  HTTP and WebSocket request.
- Token file `~/.conductor/runs/dashboard-<port>.token`, mode 0600. Env var overrides.
- `fleet/summary.py` derives `GateInfo` from the most recent unresolved
  `gate_presented`, cleared on a matching `gate_resolved`.

## Rules

- A node has no `hooks=` field. The engine executes no side effect at a step
  boundary and rejects `workflow.hooks:` outright (`config/schema.py:3094`).
- A side effect that belongs in the graph is a node: costed against
  `max_iterations`, routed, visible in the dashboard and in `ictus trace`.
- A side effect that cannot be a node is a subscriber, and never enters the
  emitted YAML.
- The subscription is declared in ictus beside `require_mcp` /
  `require_executable`, and is preflight-checked.
- Event-vocabulary parsing lives in `interfaces/conductor/`, beside `trace.py`.
- Delivery — Slack, Jira, webhook — sits above that and is not Conductor-shaped.
