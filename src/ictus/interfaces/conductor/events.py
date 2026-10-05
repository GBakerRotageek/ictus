"""Reading a live run as signals.

Two halves, kept apart because only one of them needs a running engine: turning
a stream of Conductor events into ``RunSignal``\\ s is a pure function over
dicts, tested against streams recorded from real runs, and the transport is
thin enough to read.

Three things here are not preference, and each cost a spike to learn:

* **Connect before seeding.** The socket carries live events only — it replays
  nothing — so a watcher that attaches to a run already parked at a gate hears
  silence and waits forever, on a run that is waiting for *it*. Seeding after
  connecting means the overlap is duplicated rather than lost, which a dedupe
  on ``(type, timestamp)`` absorbs.
* **Reading needs no token.** ``GET /api/state`` is guarded by Origin/Host
  alone. Only the socket handshake and anything that changes the run need one,
  so a watcher that only listens still works where no token can be found.
* **Let go on a terminal event.** A held socket keeps ``_connections``
  non-empty, the grace timer never arms, and the detached run never exits —
  one immortal process per run, each holding its whole event history in memory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ictus.interfaces import SignalEvent
from ictus.interfaces.conductor.runs import LOOPBACK, token_for
from ictus.interfaces.conductor.signals import signal_for
from ictus.websocket import WebSocket

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ictus.interfaces.conductor.runs import LiveRun

__all__ = ["STATE_TIMEOUT_SECONDS", "history", "offered", "signals_from", "step_outputs", "watch"]

STATE_TIMEOUT_SECONDS = 15.0


def signals_from(events: Iterable[dict[str, object]], run: LiveRun) -> Iterator[SignalEvent]:
    """Every reportable moment in ``events``, in order, stopping when the run ends.

    Pure, so the whole of what a watcher decides can be checked against a
    recorded run without an engine. Events that stand for no signal are dropped
    rather than raising: an engine upgrade that adds an event type must not
    break a watcher that is already mid-run.
    """
    seen: set[tuple[str, float]] = set()
    for event in events:
        kind = event.get("type")
        if not isinstance(kind, str):
            continue
        at = event.get("timestamp")
        moment = float(at) if isinstance(at, (int, float)) else 0.0
        if (kind, moment) in seen:
            continue
        seen.add((kind, moment))
        signal = signal_for(kind)
        if signal is None:
            continue
        payload = event.get("data")
        reported = SignalEvent(
            signal=signal,
            run_id=run.run_id,
            workflow=run.workflow,
            at=moment,
            event_type=kind,
            data=payload if isinstance(payload, dict) else {},
        )
        yield reported
        if reported.ends_the_run:
            return


def history(run: LiveRun, *, timeout: float = STATE_TIMEOUT_SECONDS) -> list[dict[str, object]]:
    """Everything the run emitted before now. Needs no token."""
    with LOOPBACK.open(f"{run.dashboard}/api/state", timeout=timeout) as response:
        loaded = json.loads(response.read())
    return (
        [event for event in loaded if isinstance(event, dict)] if isinstance(loaded, list) else []
    )


def step_outputs(events: Iterable[dict[str, object]], step: str) -> list[dict[str, object]]:
    """What ``step`` printed each time it completed, oldest first.

    A script step's output reaches the stream only as the stdout of
    ``script_completed`` — the engine merges the parsed object into what later
    steps read, and never emits it separately — so it is parsed back here. A run
    of the step that printed nothing readable counts as an empty object rather
    than being skipped, because it still happened, and "the latest" has to mean
    the latest.
    """
    found: list[dict[str, object]] = []
    for event in events:
        data = event.get("data")
        if event.get("type") != "script_completed" or not isinstance(data, dict):
            continue
        if data.get("agent_name") != step:
            continue
        stdout = data.get("stdout")
        lines = stdout.strip().splitlines() if isinstance(stdout, str) else []
        try:
            parsed = json.loads(lines[-1]) if lines else {}
        except ValueError:
            parsed = {}
        found.append(parsed if isinstance(parsed, dict) else {})
    return found


def offered(events: Iterable[dict[str, object]], gate: str) -> tuple[str, ...]:
    """The answers ``gate`` offered the last time it was asked, if it has been.

    Worth checking before answering: the engine accepts a value its gate does not
    offer with a 200, then fails the run on it.
    """
    options: tuple[str, ...] = ()
    for event in events:
        data = event.get("data")
        if event.get("type") != "gate_presented" or not isinstance(data, dict):
            continue
        if data.get("agent_name") == gate and isinstance(data.get("options"), list):
            options = tuple(str(option) for option in data["options"])
    return options


def watch(run: LiveRun, *, token: str | None = None) -> Iterator[SignalEvent]:
    """Attach to ``run`` and yield its signals until it ends.

    Closes the socket on the way out, however it leaves — a terminal event, an
    exception, or a caller that stops consuming — because a run cannot reap
    while anything is still connected to it.
    """
    resolved = token if token is not None else token_for(run.port)
    headers = {"Authorization": f"Bearer {resolved}"} if resolved else {}
    with WebSocket("127.0.0.1", run.port, "/ws", headers=headers) as socket:
        yield from signals_from(_seeded(socket, run), run)


def _seeded(socket: WebSocket, run: LiveRun) -> Iterator[dict[str, object]]:
    """The run's history, then everything after it.

    History is fetched *after* the socket is open, so an event emitted between
    the two arrives twice rather than not at all.
    """
    yield from history(run)
    for raw in socket.messages():
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            yield loaded
