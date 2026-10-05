"""Listening to Slack, so a button in a channel can answer a gate on a run.

Socket Mode: the app opens a websocket *outward* to Slack and events arrive down
it, so nothing has to be publicly reachable — no inbound rule, no certificate,
no request-signature check. Slack recommends HTTP for production and is right
about it at scale; this is the shape that works on a box behind a firewall, and
everything above ``listen`` is transport-agnostic so the other can replace it.

Two properties of Socket Mode that are not preferences:

* **Every envelope must be acknowledged**, by sending its ``envelope_id`` back
  down the socket. Slack retries an unacknowledged event, so a slow answer
  becomes a second click nobody made. The ack goes first, before the gate is
  answered, because the two are about different things: one says the message
  arrived, the other says what was done about it.
* **The connection is replaced, not kept.** Slack sends ``disconnect`` when it
  wants the client to reconnect, and the URL it hands out is single-use. A
  client that treats a disconnect as a failure stops working within the hour.

Events that arrive while no socket is open are **lost** — there is no replay.
That is the trade against an HTTP endpoint, and it is why a button is a
convenience rather than the only way to answer a gate: the dashboard is always
there, and it is the thing of record.
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
from ictus.interfaces.conductor.websocket import connect

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

__all__ = ["Click", "SlackError", "clicks", "open_socket", "resolve"]

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

    response_url: str = ""
    """Where to say what happened. Good for 30 minutes, five uses, no token."""

    envelope_id: str = ""


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
    """Every button press, reconnecting as Slack asks.

    Loops forever by design: this is a daemon, and the socket being replaced is
    routine rather than exceptional.
    """
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
                    # Acknowledged before anything is done about it: Slack
                    # retries what it thinks did not arrive, and a retry looks
                    # exactly like a second press.
                    socket.send(json.dumps({"envelope_id": envelope_id}))
                yield from _pressed(envelope)
        finally:
            socket.close()


def _parsed(raw: str) -> dict[str, object] | None:
    """One envelope, or ``None`` if it is not one we can read."""
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
            response_url=str(payload.get("response_url", "")),
            envelope_id=str(envelope.get("envelope_id", "")),
        )


def resolve(click: Click, *, allowed: frozenset[str] = frozenset()) -> str:
    """Answer the gate the click names, and say what happened in one line.

    ``allowed`` is Slack user ids. Empty means anyone in the channel may answer,
    which is the right default for a channel people were invited to and the wrong
    one for a deploy. There is no middle setting on purpose: "who may approve
    this" is a decision somebody has to make rather than inherit.
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


def say(response_url: str, text: str) -> None:
    """Reply where the button was, in its thread. Needs no token.

    Best effort: a report about a report is not worth failing a daemon over, and
    the gate has already been answered by the time this runs.
    """
    if not response_url:
        return
    request = urllib.request.Request(
        response_url,
        data=json.dumps({"text": text, "response_type": "in_channel"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=REPLY_TIMEOUT_SECONDS).read()
    except OSError:
        logger.warning("could not post back to Slack about %s", text)
