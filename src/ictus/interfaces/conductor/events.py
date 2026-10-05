"""Reading a live run as signals.

Two halves, kept apart because only one of them needs a running engine: turning
a stream of Conductor events into ``RunSignal``\\ s is a pure function over
dicts, tested against streams recorded from real runs, and the transport is
thin enough to read.

Five things here are not preference, and each cost a run to learn:

* **Connect before seeding.** The socket carries live events only — it replays
  nothing — so a watcher that attaches to a run already parked at a gate hears
  silence and waits forever, on a run that is waiting for *it*. Seeding after
  connecting means the overlap is duplicated rather than lost, which a dedupe
  on ``(type, timestamp)`` absorbs.
* **Reading needs no token.** ``GET /api/state`` is guarded by Origin/Host
  alone. Only the socket handshake and anything that changes the run need one,
  so a watcher that only listens still works where no token can be found.
* **Let go on a terminal event — the run's own.** A held socket keeps
  ``_connections`` non-empty, the grace timer never arms, and the detached run
  never exits. But a stage is a child workflow that emits its own
  ``workflow_completed`` into the same stream, stamped with ``subworkflow_path``;
  read as the run finishing, it ended the watch partway through, after
  reporting "finished". The engine's own dashboard filters those out, and so
  does this.
* **A dropped socket is not an ended run.** While the run's process lives, the
  watch dials again and picks up where it was, with one dedupe across every
  connection. When the process is gone without having said so — killed,
  crashed, the machine slept — that is reported as the run failing, because it
  is the one ending nothing inside the run can announce.
* **Holding the socket keeps a paused agent paused.** The engine resumes a
  paused agent by itself once every client has disconnected, and a watcher is
  a client. A run being watched waits for a person to resume it, and the
  watcher says that it is waiting.
"""

from __future__ import annotations

import http.client
import json
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from ictus.graph.signals import RunSignal
from ictus.interfaces import SignalEvent
from ictus.interfaces.conductor.runs import LOOPBACK, alive, token_for
from ictus.interfaces.conductor.signals import signal_for
from ictus.websocket import WebSocket

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from ictus.interfaces.conductor.runs import LiveRun

__all__ = ["STATE_TIMEOUT_SECONDS", "history", "offered", "signals_from", "step_outputs", "watch"]

STATE_TIMEOUT_SECONDS = 15.0

#: Consecutive failures to reach a dashboard whose process is alive, before
#: giving up. A pid can be reused by something that is not a run.
RECONNECT_ATTEMPTS = 5

#: The run's own lifecycle. From a stage, these are the stage's, not the run's.
_LIFECYCLE = frozenset({"workflow_started", "workflow_completed", "workflow_failed"})

#: Moments a step stands in front of, when an integration is attached: every
#: gate and question is announced before it is asked, and every way the
#: top-level graph ends is announced as it ends.
_ANNOUNCED = frozenset(
    {"workflow_started", "workflow_completed", "gate_presented", "questions_presented"}
)


def signals_from(
    events: Iterable[dict[str, object]],
    run: LiveRun,
    *,
    seen: set[tuple[str, float]] | None = None,
    live_from: float | None = None,
) -> Iterator[SignalEvent]:
    """Every reportable moment in ``events``, in order, stopping when the run ends.

    Pure, so the whole of what a watcher decides can be checked against a
    recorded run without an engine. Events that stand for no signal are dropped
    rather than raising: an engine upgrade that adds an event type must not
    break a watcher that is already mid-run.

    ``seen`` carries the dedupe across reconnects; ``live_from`` is when the
    watcher attached, and anything older is marked as replayed.
    """
    seen = set() if seen is None else seen
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
        data = payload if isinstance(payload, dict) else {}
        if kind in _LIFECYCLE and data.get("subworkflow_path"):
            continue  # a stage starting or finishing, not the run
        reported = _neutral(signal, kind, moment, data, run)
        if live_from is not None and moment < live_from:
            reported = replace(reported, replayed=True)
        yield reported
        if reported.ends_the_run:
            return


def _neutral(
    signal: RunSignal, kind: str, moment: float, data: dict[object, object], run: LiveRun
) -> SignalEvent:
    """The engine-neutral facts in one Conductor payload."""
    notes = data.get("additional_input")
    options = data.get("options")
    step = data.get("agent_name") or data.get("group_name") or ""
    prompt = data.get("prompt") or data.get("opening_question") or ""
    reason = data.get("termination_reason") or data.get("message") or data.get("error") or ""
    explicit = kind == "workflow_failed" and data.get("is_explicit") is True
    return SignalEvent(
        signal=signal,
        run_id=run.run_id,
        workflow=run.workflow,
        at=moment,
        event_type=kind,
        step=str(step),
        options=tuple(str(o) for o in options) if isinstance(options, list) else (),
        prompt=str(prompt),
        choice=str(data.get("selected_option") or ""),
        notes=tuple((str(k), str(v)) for k, v in notes.items() if v)
        if isinstance(notes, dict)
        else (),
        reason=str(reason),
        # A failed terminate is an explicit exit, which a step announced; a
        # failure the engine raised is not, and nothing inside the run said it.
        at_a_step=kind in _ANNOUNCED or explicit,
    )


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


def watch(
    run: LiveRun,
    *,
    token: str | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> Iterator[SignalEvent]:
    """Attach to ``run`` and yield its signals until it ends.

    Dials again whenever the connection drops while the run's process lives,
    and reports the run failing if the process disappears without saying so.
    Closes the socket on the way out, however it leaves — a terminal event, an
    exception, or a caller that stops consuming — because a run cannot reap
    while anything is still connected to it.
    """
    resolved = token if token is not None else token_for(run.port)
    headers = {"Authorization": f"Bearer {resolved}"} if resolved else {}
    seen: set[tuple[str, float]] = set()
    live_from = time.time()
    failures = 0
    while True:
        try:
            with WebSocket("127.0.0.1", run.port, "/ws", headers=headers) as socket:
                failures = 0
                for event in signals_from(
                    _seeded(socket, run), run, seen=seen, live_from=live_from
                ):
                    yield event
                    if event.ends_the_run:
                        return
        except (OSError, ValueError, http.client.HTTPException):
            failures += 1
        if not alive(run.pid):
            yield SignalEvent(
                signal=RunSignal.RUN_FAILED,
                run_id=run.run_id,
                workflow=run.workflow,
                at=time.time(),
                event_type="engine_gone",
                reason="the engine stopped without reporting why: killed, crashed, or asleep",
            )
            return
        if failures >= RECONNECT_ATTEMPTS:
            raise ConnectionError(
                f"run {run.run_id} is alive but its dashboard on port {run.port} stopped "
                f"answering after {failures} attempts"
            )
        pause(min(2.0**failures, 30.0))


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
