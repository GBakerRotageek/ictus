"""Receiving a button press, and answering the gate it names.

Socket Mode: the app dials *out* to Slack, so nothing has to be publicly
reachable — no inbound rule, no certificate, no signature check. Slack prefers
HTTP at scale; everything above ``resolve`` is transport-agnostic, so that can
replace this.

Two properties of it are not preferences:

* Every envelope is acknowledged, and *before* the gate is answered. Slack
  retries what it believes did not arrive, and a retry is indistinguishable
  from a second press.
* A ``disconnect`` is routine. The URL is single-use and Slack replaces the
  connection; treating that as a failure stops the listener within the hour.

Presses arriving while no socket is open are **lost** — there is no replay.
That is the trade against an HTTP endpoint, and why the dashboard stays the
thing of record.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.errors import IctusError
from ictus.interfaces.conductor.respond import answer_gate
from ictus.interfaces.conductor.runs import live_runs
from ictus.notify.slack.send import reply
from ictus.websocket import connect

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

__all__ = ["Click", "SlackError", "clicks", "open_socket", "resolve", "say"]

OPEN_URL = "https://slack.com/api/apps.connections.open"
REPLY_TIMEOUT_SECONDS = 10.0


class SlackError(IctusError):
    """Slack refused the connection, usually the app-level token or its scope."""


@dataclass(frozen=True, slots=True)
class Click:
    """Somebody pressed a button that answers a gate."""

    run_id: str
    gate: str
    choice: str
    who: str
    """The Slack user id. Who pressed it is the only authorisation signal there is."""

    channel: str = ""
    thread_ts: str = ""
    """The thread the button is in, so the answer goes back under the question.

    A button in a thread root has no ``thread_ts`` of its own — its own ``ts``
    *is* the thread — so both are read and the first that exists wins.
    """


def open_socket(app_token: str) -> str:
    """Ask Slack for a websocket URL. Single use, and it expires quickly."""
    request = urllib.request.Request(
        OPEN_URL,
        data=b"",
        headers={"Authorization": f"Bearer {app_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REPLY_TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read())
    except OSError as exc:
        raise SlackError(f"could not reach Slack: {exc}") from None
    if not isinstance(answer, dict) or not answer.get("ok"):
        why = str(answer.get("error")) if isinstance(answer, dict) else "unreadable reply"
        raise SlackError(
            f"Slack refused the connection: {why}"
            + (
                " - the app-level token needs connections:write, and Socket Mode has to be "
                "enabled under Settings > Socket Mode"
                if why in ("invalid_auth", "not_allowed_token_type", "missing_scope")
                else ""
            )
        )
    url = answer.get("url")
    if not isinstance(url, str):
        raise SlackError("Slack opened a connection but named no url")
    return url


def clicks(app_token: str) -> Iterator[Click]:
    """Every button press, reconnecting as Slack asks. Loops forever by design."""
    while True:
        socket = connect(open_socket(app_token))
        try:
            for raw in socket.messages():
                envelope = _parsed(raw)
                if envelope is None:
                    continue
                kind = envelope.get("type")
                if kind == "disconnect":
                    break  # asked to reconnect; the outer loop opens a new one
                if kind == "hello":
                    continue
                envelope_id = envelope.get("envelope_id")
                if isinstance(envelope_id, str):
                    # Before anything is done about it: see the module note.
                    socket.send(json.dumps({"envelope_id": envelope_id}))
                yield from _pressed(envelope)
        finally:
            socket.close()


def _parsed(raw: str) -> dict[str, object] | None:
    """One envelope, or ``None`` if it cannot be read."""
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _pressed(envelope: dict[str, object]) -> Iterator[Click]:
    """The button presses in one envelope, if it holds any."""
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "block_actions":
        return
    actions = payload.get("actions")
    user = payload.get("user")
    who = str(user.get("id", "")) if isinstance(user, dict) else ""
    channel, thread = _where(payload)
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        try:
            value = json.loads(str(action.get("value", "")))
        except json.JSONDecodeError:
            continue  # somebody else's button, in a channel we also watch
        if not isinstance(value, dict) or not {"run", "gate", "choice"} <= set(value):
            continue
        yield Click(
            run_id=str(value["run"]),
            gate=str(value["gate"]),
            choice=str(value["choice"]),
            who=who,
            channel=channel,
            thread_ts=thread,
        )


def _where(payload: dict[str, object]) -> tuple[str, str]:
    """The channel and thread a press came from.

    A button on a thread root has no ``thread_ts``; its own ``ts`` is the thread.
    """
    channel = payload.get("channel")
    where = str(channel.get("id", "")) if isinstance(channel, dict) else ""
    message = payload.get("message")
    if not isinstance(message, dict):
        container = payload.get("container")
        message = container if isinstance(container, dict) else {}
    thread = message.get("thread_ts") or message.get("ts") or message.get("message_ts") or ""
    return where, str(thread)


def resolve(click: Click, *, allowed: frozenset[str] = frozenset()) -> str:
    """Answer the gate the click names, and say what happened in one line.

    ``allowed`` is Slack user ids; empty means anyone in the channel. No middle
    setting on purpose — who may approve something is a decision to make rather
    than inherit.
    """
    if allowed and click.who not in allowed:
        return f"<@{click.who}> is not allowed to answer {click.gate}, so nothing was done"
    matched = [run for run in live_runs() if run.run_id == click.run_id]
    if not matched:
        return (
            f"run `{click.run_id}` is no longer running, so {click.gate} cannot be "
            "answered — it finished, or it was on another machine"
        )
    outcome = answer_gate(matched[0], gate=click.gate, choice=click.choice)
    if outcome.accepted:
        return f"<@{click.who}> answered *{click.choice}*"
    return f"could not answer {click.gate}: {outcome.detail}"


def say(click: Click, text: str, *, token: str) -> None:
    """Answer under the question, not beside it.

    Best effort: the gate is already answered by now, so this is not worth
    failing a daemon over — but it is logged, because a thread that goes quiet
    after a press looks like nothing happened.
    """
    if not token or not click.channel:
        return
    why = reply(token=token, channel=click.channel, thread_ts=click.thread_ts, text=text)
    if why:
        logger.warning("could not say what happened to %s: %s", click.gate, why)
