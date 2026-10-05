"""Telling somebody what a run is doing.

A second boundary, and deliberately not under ``interfaces/``. That one answers
"who executes this graph"; this one answers "who hears about it". They are
different axes — a run on any engine can report to any audience — and giving
the second its own package is what stops Slack's spelling drifting into a
backend, or Conductor's into a message.

Nothing here knows an engine. A ``SignalEvent`` arrives already engine-neutral,
and one module per destination keeps each system's own shape in one file, the
way ``stdlib/`` keeps one primitive per module.

**A report never breaks a run.** Delivery happens outside the engine, after the
fact, and a channel being unreachable says nothing about whether the work
succeeded. So every failure is collected and returned rather than raised: the
caller decides how loudly to complain, and the watcher keeps watching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.graph.requirements import NotifierKind
from ictus.notify.slack import message as slack_message
from ictus.notify.webhook import DeliveryError, post

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ictus.graph.requirements import Notifier
    from ictus.interfaces import SignalEvent

__all__ = ["Delivered", "DeliveryError", "body_for", "deliver", "endpoint_for"]


@dataclass(frozen=True, slots=True)
class Delivered:
    """What happened to one report."""

    notifier: str
    signal: str
    sent: bool
    detail: str = ""
    """Why it did not go, when it did not. Never contains the endpoint."""


def body_for(target: Notifier, event: SignalEvent, *, dashboard: str = "") -> dict[str, object]:
    """The payload ``target`` wants for ``event``."""
    if target.kind is NotifierKind.SLACK:
        return slack_message(event, dashboard=dashboard)
    return {
        "signal": event.signal.value,
        "run_id": event.run_id,
        "workflow": event.workflow,
        "at": event.at,
        "event_type": event.event_type,
        "data": event.data,
        **({"dashboard": dashboard} if dashboard else {}),
    }


def endpoint_for(target: Notifier, env: Mapping[str, str] | None = None) -> str | None:
    """Where ``target`` posts, read from the environment at the last moment.

    The first declared variable holding a value wins. Returning ``None`` rather
    than raising keeps an unconfigured notifier from taking a watcher down —
    preflight is where an unset one is supposed to be refused, loudly and before
    anything runs.
    """
    source = os.environ if env is None else env
    for var in target.required_env:
        value = source.get(var.name)
        if value:
            return value
    return None


def deliver(
    event: SignalEvent,
    targets: Iterable[Notifier],
    *,
    dashboard: str = "",
    env: Mapping[str, str] | None = None,
) -> list[Delivered]:
    """Report ``event`` to every target that asked for its signal.

    Returns one result per attempt, successes included, so a caller can say what
    it did as well as what it could not. Raises nothing: see the module note.
    """
    results: list[Delivered] = []
    for target in targets:
        if not target.wants(event.signal):
            continue
        endpoint = endpoint_for(target, env)
        if endpoint is None:
            wanted = ", ".join(var.name for var in target.required_env) or "(none declared)"
            results.append(
                Delivered(
                    notifier=target.name,
                    signal=event.signal.value,
                    sent=False,
                    detail=f"no endpoint: {wanted} is unset",
                )
            )
            continue
        try:
            post(endpoint, body_for(target, event, dashboard=dashboard), name=target.name)
        except DeliveryError as exc:
            results.append(Delivered(target.name, event.signal.value, sent=False, detail=str(exc)))
        else:
            results.append(Delivered(target.name, event.signal.value, sent=True))
    return results
