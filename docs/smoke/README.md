# Smoke test: the run event surface

Proves what [run-events.md](../run-events.md) claims, against a live engine:
a run is discoverable, its event stream is readable, and its gates are
answerable from outside the process.

**It costs nothing.** `pipeline/` is two gates and one `set` step — no node emits
`type: agent`, so no provider is called. Check for yourself:

    grep 'type:' pipeline/build/smoke-events.yaml

`subscribe.py` is standard library only. Nothing to install beyond Conductor.

## What you need

`conductor` on PATH, and `ictus` runnable. The documented setup is

    uv tool install conductor-cli
    uv sync

If you would rather not install anything globally, both work from throwaway
virtualenvs:

    python3 -m venv /tmp/venv-conductor
    /tmp/venv-conductor/bin/pip install conductor-cli
    python3 -m venv /tmp/venv-ictus
    /tmp/venv-ictus/bin/pip install 'ruamel.yaml==0.18.10' 'typer==0.20.0' 'click==8.5.0'

then prefix the `ictus` calls below with

    PATH=/tmp/venv-conductor/bin:$PATH PYTHONPATH=src /tmp/venv-ictus/bin/python \
      -c 'from ictus.cli import app; app()'

## Run it

From the repository root. Two terminals, because the run parks at a gate and
waits for the subscriber to answer it.

**One — launch.** Detached, serving a dashboard:

    uv run ictus run docs/smoke/pipeline

It prints `Dashboard: http://127.0.0.1:<port>`. Open it if you want to watch;
the subscriber works whether or not a browser is attached.

**Two — subscribe and answer:**

    python3 docs/smoke/subscribe.py 'confirm_start=start' 'smoke_gate=approved'

Approve both and the `set` step runs. To exercise free text instead:

    python3 docs/smoke/subscribe.py 'confirm_start=start' \
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
