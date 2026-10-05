#!/usr/bin/env python3
"""Attach to a live run, answer its gates, and leave before it reaps.

The harness behind ``docs/run-events.md``: it drives a real run unattended so
the event surface can be checked end to end, and it is what recorded the
fixtures in ``tests/fixtures/``.

It uses the library rather than reimplementing it — ``live_runs`` to find the
run, ``WebSocket`` to talk to it, ``history`` to seed. What is left here is the
part the library does not do yet: *answering* a gate, which is phase 4. When
that lands this file should shrink again rather than grow.

    python3 smoke/subscribe.py                       # take the first option
    python3 smoke/subscribe.py smoke_gate=rejected:no thanks
    python3 smoke/subscribe.py --run a1b2c3d4 ship_it=approved

Without ``--run`` it takes the most recently started run, which is wrong the
moment two are live — both invocations would answer the same one.

Each argument is ``<agent>=<value>``, with an optional ``:<free text>`` for a
choice that declares ``prompt_for``.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import TYPE_CHECKING

from ictus.interfaces.conductor.events import history
from ictus.interfaces.conductor.runs import live_runs, token_for
from ictus.websocket import WebSocket

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ictus.interfaces.conductor.runs import LiveRun

TERMINAL = ("workflow_completed", "workflow_failed")

Answers = dict[str, tuple[str, str | None]]


def answer(data: dict[str, object], answers: Answers) -> dict[str, object]:
    """A ``gate_response`` for the gate in ``data``.

    The field is ``selected_value``, not ``value``, and ``additional_input``
    goes up as a bare string even though it reads back keyed by ``prompt_for``.
    """
    agent = str(data.get("agent_name", ""))
    options = data.get("options")
    first = str(options[0]) if isinstance(options, list) and options else ""
    choice, note = answers.get(agent, (first, None))
    message: dict[str, object] = {
        "type": "gate_response",
        "agent_name": agent,
        "selected_value": choice,
    }
    if note is not None:
        message["additional_input"] = note
    return message


def events(socket: WebSocket, run: LiveRun) -> Iterator[dict[str, object]]:
    """The run's history, then everything after it.

    History comes *after* the socket is open: the socket replays nothing, so
    seeding first would lose whatever was emitted in between, and a run already
    parked at a gate would look like a run that is merely quiet.
    """
    yield from history(run)
    for raw in socket.messages():
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            yield loaded


def parse(argv: list[str]) -> Answers:
    answers: Answers = {}
    for argument in argv:
        agent, _, rest = argument.partition("=")
        choice, separator, note = rest.partition(":")
        answers[agent] = (choice, note if separator else None)
    return answers


def main(argv: list[str]) -> int:
    wanted = ""
    if argv and argv[0] == "--run":
        wanted, argv = argv[1], argv[2:]
    answers = parse(argv)
    runs = live_runs()
    if not runs:
        print("no run is serving a dashboard; start one with `ictus run <folder>`")
        return 1
    if wanted:
        matched = [r for r in runs if r.run_id.startswith(wanted)]
        if not matched:
            print(f"no live run starts with {wanted!r}; live: {[r.run_id for r in runs]}")
            return 1
        run = matched[0]
    else:
        run = runs[-1]
    out = pathlib.Path(f"smoke-events-{run.run_id}.jsonl")
    seen: set[tuple[str, float]] = set()

    print(f"run {run.run_id}  port {run.port}  workflow {run.workflow}")
    token = token_for(run.port)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with WebSocket("127.0.0.1", run.port, "/ws", headers=headers) as socket, out.open("w") as log:
        print("connected")
        for event in events(socket, run):
            kind = str(event.get("type", ""))
            at = event.get("timestamp")
            moment = float(at) if isinstance(at, (int, float)) else 0.0
            if (kind, moment) in seen:
                continue
            seen.add((kind, moment))
            log.write(json.dumps(event) + "\n")
            log.flush()
            print(f"  <- {kind}")
            if kind == "gate_presented":
                data = event.get("data")
                reply = answer(data if isinstance(data, dict) else {}, answers)
                socket.send(json.dumps(reply))
                print(f"  -> {reply['agent_name']} = {reply['selected_value']}")
            if kind in TERMINAL:
                print("terminal event; closing so the run can reap")
                break

    print(f"\nclosed. {len(seen)} events -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
