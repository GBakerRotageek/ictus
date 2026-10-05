# Run events: build plan

Implements [run-events.md](run-events.md). Phases ship in order; each is useful alone.

## Phase 0 — spike — **done**

Proven against two live runs of a gates-and-one-`set`-step pipeline, which emits
no `type: agent` at all and so cost nothing. Fixtures:
`tests/fixtures/run-events-{approved,rejected}.jsonl`.

Confirmed: run discovery from the fleet record, token from the token file,
`Authorization: Bearer` on the handshake, both gates answered over the socket,
free text round-tripped, disconnect on `workflow_completed`, process gone 31.5s
later with the port closed.

What it corrected in [run-events.md](run-events.md):

- The socket **replays nothing on connect**. A subscriber attaching to a run
  already parked at a gate waits forever. Connect, then seed `GET /api/state`,
  then dedupe on `(type, timestamp)`.
- Observation needs **no token** — `/api/state`, `/api/gate-status`, `/api/info`
  and `/api/logs` are Origin/Host only. Only answering needs a credential.
- A human gate's `gate_presented` carries **no `prompt_id`**; that is the
  questions variant. `agent_name` is the match that matters.
- The response field is **`selected_value`**, not `value`. `additional_input` is
  sent as a string and read back keyed by `prompt_for`.
- The run record is **archived to `terminal/`** on reap, so globbing for live
  runs needs no staleness filter.
- `route_taken` exists and was missing from the vocabulary.
- Socket and JSONL carry byte-identical sequences.

Unrelated defect found and fixed: an empty frontmatter pair (`---\n---\n`) failed
`_FRONTMATTER` in `runspec.py`, which required a newline before the closing
`---`. A failed match makes the delimiters the body, so a pipeline with no inputs
was refused for carrying prose it did not have. The pattern now takes an empty
first branch; an optional newline was rejected because it would also let
`foo---` close a block.

## Phase 1 — declaration — **done**

Engine-neutral. `graph/` names no Conductor event.

`graph/signals.py` — `RunSignal`, a `StrEnum` mirroring `NodeKind`:

| Signal | Conductor events |
|---|---|
| `RUN_STARTED` | `workflow_started` |
| `RUN_FINISHED` | `workflow_completed` |
| `RUN_FAILED` | `workflow_failed` |
| `DECISION_NEEDED` | `gate_presented`, `questions_presented` |
| `DECISION_MADE` | `gate_resolved`, `questions_completed` |
| `RUN_PAUSED` | `agent_paused` |
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

Endpoints are declared as `EnvVar`, not literals, so preflight's existing env
check covers notifiers with no signature change. Offline only, unlike MCP's
`--probe`: proving an endpoint means posting to it, and a preflight that
announced itself in a channel every time anyone checked a pipeline would be
muted — taking the real notification with it.

## Phase 2 — the watcher — **done**

Settled the dependency: **no `websockets`.** `interfaces/conductor/websocket.py`
is ~60 lines of RFC 6455 covering the subset actually used — one connection, text
frames, no extensions. The maintained clients are asyncio-first and ictus is
synchronous end to end, so the dependency would have dragged an event loop into
the CLI to carry less code than it replaced. `ictus watch` runs a thread per run.

Found while testing: a read deadline on the socket is a **bug**, not a
safeguard. A run parked at a gate emits nothing for as long as the person takes,
so any timeout drops precisely the connection worth holding and the gate it was
about to report goes unreported. The socket blocks after the handshake; only
reaching the dashboard is bounded.

`interfaces/conductor/events.py`, beside `trace.py`. Conductor's vocabulary is
already permitted there.

- Discover runs from fleet run records (`run_id`, `port`, `pid`, `mode`,
  `workflow_name`, `event_log_path`).
- Resolve token and connect `/ws` per run.
- Map event → `RunSignal`. Unmapped events are dropped, not forwarded.
- **Close the socket on `workflow_completed` / `workflow_failed`.** Non-negotiable;
  a held connection stops the run reaping.
- Reconnect on drop is **not** implemented and is deliberately deferred: a drop
  mid-run is unobserved so far, and the right behaviour depends on whether the
  run ended underneath us. `ictus watch` reports the run as detached with the
  reason rather than silently reattaching to something that is gone.

`cli.py` — `ictus watch`. Long-lived, attaches to every discoverable run, detaches
per run on terminal event. Does not change `ictus run`; ictus stays a compiler
that exits.

## Phase 3 — delivery — **done**

`src/ictus/notify/` — a new top-level package. Not under `interfaces/`: that
boundary is the engine, this one is the audience.

`SignalEvent` moved to `interfaces/__init__.py` on the way, so `notify/`
imports no engine. Both sides need it and neither owns it.

`NotifierKind` picks the payload — `WEBHOOK` or `SLACK` — and sits beside
`McpTransport`, since it is a property of what the pipeline asked for rather
than of whatever delivers it. The difference between the two is one JSON body.

The endpoint is treated as the credential it is: an incoming-webhook URL is
the entire authorisation to post as whatever it points at, and `urllib` names
the URL in its own exceptions, so every failure is rewritten to carry the
notifier's name and the status and nothing else. Tested by asserting a known
secret path never appears in any message.

`smoke/fake_channel.py` accepts the same POST an incoming webhook does,
so the whole path is watchable before anyone creates a Slack app.

- `webhook.py` first. HTTP POST via `urllib.request`. No dependency.
- `slack.py` is a webhook URL plus payload shape. Slack outbound — Incoming
  Webhooks and `chat.postMessage` — is HTTPS POST and never a WebSocket, so no
  Slack SDK is needed here.
- One module per destination, one destination per module, as `stdlib/` does.
- A delivery failure is logged and never raised into the watcher loop.

## Phase 4 — inbound — **done**

> Assessed and built. Socket Mode, because it needs no inbound rule, no
> certificate and no signature check on a box behind a firewall. What that
> costs is real and is written down below: a press arriving while nothing is
> listening is **lost**, with no replay, which is why the dashboard stays the
> thing of record and a button is a convenience.
>
> _Original note:_ **Stop here and assess before starting.** Phases 1–3 need nothing from Slack
> and can be built and checked on their own. This one needs a configured Slack
> app, it is the first thing that can act on a live run from outside, and the
> questions it answers — what a message says, what a stale button does, whether
> Socket Mode reconnects cleanly — are worth deciding deliberately rather than
> on the way past. Phase 0 is the precedent: proving the surface first bought
> six corrections.

Answering from Slack. Needs a Slack app; scope separately.

Sent to Conductor:

- `gate_response` with `agent_name` + `prompt_id` from the originating
  `gate_presented`. A mismatch is dropped by `_validate_gate_target`.
- `dialog_message` / `dialog_decline` drive `remediate` conversations.
- `iteration_limit_response` answers the `max_iterations` prompt.
- Either `/ws` or `POST /api/gate-respond`; both need the token.

Received from Slack — two transports, HTTP is Slack's default:

- **HTTP**, an Events API request URL. Slack: "To have the highest possible
  reliability for application connectivity, we recommend using HTTP for
  production applications." Stateless. Needs a public HTTPS endpoint and request
  signature verification.
- **Socket Mode**, an outbound-initiated WebSocket. Slack recommends it for local
  development and for apps that cannot expose an HTTP endpoint. Needs an `xapp-`
  app-level token and no public endpoint.

Socket Mode constraints:

- Requires a long-lived process; a serverless timeout kills the socket.
- 10 concurrent connections per app.
- URLs rotate. Reconnect handling is mandatory.
- **Events can be lost in a reconnect gap.** Load-bearing for alerting.
- Not permitted in the Slack Marketplace. Irrelevant for an internal app.

Slack's WebSocket shares nothing with Conductor's but the transport: different
auth, different URL lifetime, and `slack_sdk` ships its own Socket Mode client
that does not generalise. No dependency is amortised across the two.

Under Socket Mode the watcher and the Slack listener are one daemon.

### What building it settled

- **Acknowledge before acting.** Slack retries an envelope it believes did not
  arrive, and a retry is indistinguishable from a second press.
- **A disconnect is routine.** The URL is single-use and Slack replaces the
  connection; treating that as a failure stops the listener within the hour.
- **One socket, not one per run.** An app may hold 10 connections and Slack
  delivers each event to *one* of them, so a per-run listener would drop clicks
  meant for its neighbours. The run id travels in the button instead.
- **`response_url` posts to the channel, not the thread.** For a button inside a
  thread that puts the answer beside every other run's. The parent is in the
  payload; `chat.postMessage` with it is the only way to reply under the
  question. A button on a thread root has no `thread_ts` — its own `ts` is the
  thread.
- **Who may press is a decision.** `--allow` takes Slack user ids; the default is
  anyone in the channel, which is right for an invited channel and wrong for a
  deploy.

## Phase 5 — the trigger

Not started. The end state: an event arrives, a run is spawned to handle it, and
it reports into the thread the event is already in.

- **Ingress.** The listener subscribes to Slack `message` events as well as
  presses, and shells out to `ictus run <folder> -i alert=... -i thread=...`.
  Nothing in the current design blocks it: a press and a message come down the
  same socket.
- **Replying to somebody else's thread** needs no new mechanism. `announce`
  takes a `Ref` for its thread, and a `WorkflowInput.ref()` is one — so the
  triggering `thread_ts` arrives as a declared input.
- **Hosting it elsewhere** is the open problem, and it is not ictus's. Conductor
  hardcodes `host="127.0.0.1"` at both `WebDashboard` call sites in `cli/run.py`
  and exposes no `--web-host`, so a dashboard cannot be bound anywhere reachable.
  Runs execute on the host and something there maps `run-id -> 127.0.0.1:port`,
  or a `--web-host` flag is upstreamed.
- **A run announcing its own URL** needs its port, which it can read from its own
  fleet record using `CONDUCTOR_RUN_ID`, plus a public base from the environment
  — `http://127.0.0.1:50984` means nothing to anyone else.
- **`ictus run --web-port`** is not exposed and would make proxy routing
  predictable.

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

- ~~WebSocket client dependency.~~ Settled in phase 2: no dependency, a
  standard-library client. See that phase.
- **Phase 4 transport.** HTTP is Slack's recommendation for production and needs
  a public HTTPS endpoint with signature verification. Socket Mode needs neither
  and can lose events in a reconnect gap. Decide on whether a public endpoint is
  available, not on the ictus side. Not needed before Phase 4.
- **Watcher supervision.** Out of scope here. systemd or equivalent.
