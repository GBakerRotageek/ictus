# Smoke test: the run event surface

Proves what [run-events.md](../run-events.md) claims, against a live engine:
a run is discoverable, its event stream is readable, and its gates are
answerable from outside the process.

**It costs nothing.** `demo_work/pipelines/smoke-events/` is two gates and one
`set` step — no node emits `type: agent`, so no provider is called. Check for
yourself:

    grep 'type:' demo_work/pipelines/smoke-events/build/smoke-events.yaml

That folder is also the one pipeline committed past the `demo_work/` ignore,
because three tests and every Makefile target below `soundcheck` read that
directory and fail on a clone with nothing in it.

`subscribe.py` is standard library only. Nothing to install beyond Conductor.

## What you need

`conductor` and `ictus`, both on PATH.

> **Not `pip install conductor-cli`.** That name on PyPI is an unrelated research
> computing orchestrator whose command is `cond`. It installs cleanly, gives you
> no `conductor`, and wastes an afternoon. Conductor is not published to PyPI;
> it is installed from its repository.

The documented setup, which installs `uv` for you if it is missing:

    curl -sSfL https://aka.ms/conductor/install.sh | sh
    uv sync

### Without uv, and without touching anything outside this directory

Two virtualenvs, kept apart on purpose — `import conductor` must keep failing
from the project's own interpreter, or the guidance in `AGENTS.md` about which
interpreter you asked stops being true:

    python3 -m venv .venv
    .venv/bin/pip install -e .

    python3 -m venv .venv-conductor
    .venv-conductor/bin/pip install 'git+https://github.com/microsoft/conductor.git'
    ln -s "$PWD/.venv-conductor/bin/conductor" .venv/bin/conductor

    source .venv/bin/activate

Both `ictus` and `conductor` are now on PATH, and `import conductor` from
`.venv` still raises `ModuleNotFoundError` — only the console script is linked,
and its shebang points back at the other environment. Both directories are
gitignored; delete them to undo.

With the venv activated, drop the `uv run` prefix from the commands below.

## Run it

From the repository root, one terminal, two commands. `ictus run` detaches and
returns straight away; the run is left parked at its first gate, waiting.

**One — launch.** Detached, serving a dashboard:

    uv run ictus run demo_work/pipelines/smoke-events

It prints `Dashboard: http://127.0.0.1:<port>`. Open it if you want to watch;
the subscriber works whether or not a browser is attached.

**Two — subscribe and answer:**

    python3 smoke/subscribe.py 'confirm_start=start' 'smoke_gate=approved'

Approve both and the `set` step runs. To exercise free text instead:

    python3 smoke/subscribe.py 'confirm_start=start' \
      'smoke_gate=rejected:not this time'

## What you should see

    run 4f3a1c20  port 50984  workflow smoke-events
    connected
      .. workflow_started
      .. agent_started
      .. gate_presented
      -> confirm_start = start
      <- gate_resolved
      <- checkpoint_saved
      <- agent_started
      <- gate_presented
      -> smoke_gate = approved
      <- gate_resolved
      ...
      <- set_started
      <- set_completed
      <- route_taken
      <- agent_completed
      <- workflow_completed

    closed. 17 events -> smoke-events-4f3a1c20.jsonl

`..` is replayed history, `<-` is live off the socket, `->` is a response going
back up it. The events are written to `smoke-events-<run_id>.jsonl` in the
working directory, which `.gitignore` already covers.

## Seeing a report arrive

A notifier is declared in `pipeline.py`, never in the emitted YAML — the engine
runs no side effect at a step boundary, so a declaration that compiled to
something would be a lie in the diff. `ictus watch <folder>` reads it from the
source and delivers.

You do not need Slack to watch this work. `fake_channel.py` accepts the same
JSON POST an incoming webhook does and prints what it was sent:

    python3 smoke/fake_channel.py
    export SMOKE_WEBHOOK_URL=http://127.0.0.1:8723/not-a-real-hook

Add this to the bottom of `demo_work/pipelines/smoke-events/pipeline.py`:

```python
from ictus import EnvVar, Notifier, NotifierKind, RunSignal

pipeline.require_notifier(
    Notifier(
        name="channel",
        purpose="Tell whoever is on call that this run is parked on a gate",
        kind=NotifierKind.SLACK,
        signals=(RunSignal.DECISION_NEEDED, RunSignal.DECISION_MADE, RunSignal.RUN_FINISHED),
        env=(EnvVar("SMOKE_WEBHOOK_URL", "a Slack incoming-webhook URL"),),
        setup_hint="create an incoming webhook, then export SMOKE_WEBHOOK_URL",
    )
)
```

Then, in three terminals — the fake channel, the run, and the watcher:

    ictus run demo_work/pipelines/smoke-events
    ictus watch demo_work/pipelines/smoke-events
    python3 smoke/subscribe.py 'confirm_start=start' 'smoke_gate=rejected:not tonight'

What lands in the channel:

    :raising_hand:  *smoke-events* needs a decision
    > step: `smoke_gate`
    > waiting on: `approved`, `rejected`
    > Approve to continue, or reject and leave a note.
    run `e6d616c3` · http://127.0.0.1:52829

Point `SMOKE_WEBHOOK_URL` at a real `hooks.slack.com` URL and the same messages
arrive in the channel instead. Nothing else changes.

**Unset the variable and `ictus preflight` refuses the run** before anything is
spent — which is the point of declaring it. The committed pipeline carries no
notifier so the gate needs no configuration; add one when you want to watch it.

## What it demonstrates

- **Discovery.** The run is found from `~/.conductor/runs/<run_id>.json` — port,
  pid and event log path — with no argument passed to the subscriber.
- **Seeding.** The socket replays nothing on connect. Without the `/api/state`
  seed the already-open gate is invisible and the subscriber waits forever on a
  run that is waiting for it. Delete the `history(port)` call to watch it hang.
- **Unauthenticated reads.** `/api/state` is fetched with no token. Only the
  WebSocket handshake needs one.
- **Answering.** `gate_response` carries `selected_value`, and `additional_input`
  goes up as a bare string and comes back on `gate_resolved` keyed by the
  option's `prompt_for`.
- **Reaping.** The subscriber disconnects on `workflow_completed`. Watch the run
  exit about 30 seconds later:

      curl -s http://127.0.0.1:<port>/api/info    # answers, then stops answering

  Hold the socket open instead and it never exits — which is the hazard
  [run-events.md](../run-events.md) records under Reaping.

## Re-recording the fixtures

`tests/fixtures/run-events-{approved,rejected}.jsonl` were produced by exactly
this, one run each. Replace them with a fresh `smoke-events-<run_id>.jsonl` if
the engine's vocabulary changes under us.
