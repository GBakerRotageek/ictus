"""Answering a run's gate from outside it.

The other direction. ``events.py`` reads what a run is doing; this decides what
it does next, which is a different thing and deliberately a separate module: one
needs no credential and the other is the whole of the authority over a live run.

``POST /api/gate-respond`` rather than the socket. Both work and both need the
token, but the socket is a stream whose gate responses are fire-and-forget,
while the REST call answers — 200 accepted, 409 if the gate moved on, 403 if the
token is wrong. Something acting on a person's click has to be able to say which
of those happened.

The engine refuses a response that does not match the gate currently waiting
(``_validate_gate_target``), so a button pressed twice, or pressed after somebody
answered in the dashboard, is rejected rather than applied to whatever came next.
That check is the engine's and this does not duplicate it; it reports it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.interfaces.conductor.runs import token_for

if TYPE_CHECKING:
    from ictus.interfaces.conductor.runs import LiveRun

__all__ = ["Answered", "answer_gate", "waiting_gate"]

TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Answered:
    """What became of one answer."""

    accepted: bool
    detail: str = ""
    """Why not, when not. Phrased for whoever clicked, not for a log."""


def waiting_gate(run: LiveRun, *, timeout: float = TIMEOUT_SECONDS) -> str | None:
    """The gate this run is parked on, or ``None`` if it is not waiting.

    Needs no token: reading a run's state is Origin/Host-guarded only.
    """
    try:
        with urllib.request.urlopen(
            f"{run.dashboard}/api/gate-status", timeout=timeout
        ) as response:
            state = json.loads(response.read())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or not state.get("waiting"):
        return None
    agent = state.get("agent_name")
    return agent if isinstance(agent, str) else None


def answer_gate(
    run: LiveRun,
    *,
    gate: str,
    choice: str,
    note: str | None = None,
    token: str | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> Answered:
    """Resolve ``gate`` on ``run`` with ``choice``.

    Returns rather than raises: this is driven by somebody pressing a button, and
    every way it can fail is something they need telling about rather than a
    traceback in a daemon's log.
    """
    resolved = token if token is not None else token_for(run.port)
    if resolved is None:
        return Answered(False, "no dashboard token on this machine, so the run would refuse it")

    body: dict[str, object] = {"agent_name": gate, "selected_value": choice}
    if note is not None:
        body["additional_input"] = note
    request = urllib.request.Request(
        f"{run.dashboard}/api/gate-respond",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {resolved}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status < 300:
                return Answered(True)
            return Answered(False, f"the run answered {response.status}")
    except urllib.error.HTTPError as exc:
        return Answered(False, _why(exc.code, gate))
    except urllib.error.URLError:
        return Answered(False, "the run is no longer listening; it has probably finished")
    except TimeoutError:
        return Answered(False, "the run did not answer in time")


def _why(status: int, gate: str) -> str:
    """What a status code means to the person who pressed the button."""
    if status == 409:
        return f"{gate!r} has already been answered, or the run has moved past it"
    if status == 403:
        return "the token was refused"
    if status == 422:
        return "the run could not read that answer"
    return f"the run answered {status}"
