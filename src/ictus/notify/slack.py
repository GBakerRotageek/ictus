"""What a signal looks like when it arrives in a channel.

An incoming webhook takes a JSON body over ordinary HTTPS, so the whole of
Slack support is a payload shape — no SDK, no socket, no token beyond the URL.

Written for the person who reads it on a phone. A notification's job is to make
somebody decide whether to get up, so the first line says what happened and
which run it happened in, and a gate says what it is waiting for. Everything
else is in the dashboard, which the message links to.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from ictus.graph.signals import RunSignal

if TYPE_CHECKING:
    from ictus.interfaces import SignalEvent

__all__ = ["API", "HEADLINE", "message", "reply"]

#: What each signal says, in the voice of someone telling you about it.
HEADLINE: dict[RunSignal, str] = {
    RunSignal.RUN_STARTED: "started",
    RunSignal.RUN_FINISHED: "finished",
    RunSignal.RUN_FAILED: "failed",
    RunSignal.RUN_PAUSED: "is paused, waiting to be resumed",
    RunSignal.DECISION_NEEDED: "needs a decision",
    RunSignal.DECISION_MADE: "got its answer",
    RunSignal.STEP_FAILED: "had a step fail",
    RunSignal.BUDGET_EXCEEDED: "went over budget",
}

_ICON: dict[RunSignal, str] = {
    RunSignal.DECISION_NEEDED: ":raising_hand:",
    RunSignal.RUN_FAILED: ":rotating_light:",
    RunSignal.STEP_FAILED: ":warning:",
    RunSignal.BUDGET_EXCEEDED: ":moneybag:",
    RunSignal.RUN_PAUSED: ":double_vertical_bar:",
    RunSignal.RUN_FINISHED: ":white_check_mark:",
}


def message(event: SignalEvent, *, dashboard: str = "") -> dict[str, object]:
    """The Slack body for one signal.

    ``dashboard`` is the run's URL when the sender knows it. It is left out
    rather than guessed: a link to a port that has since been reused is worse
    than no link.
    """
    icon = _ICON.get(event.signal, ":information_source:")
    headline = HEADLINE.get(event.signal, event.signal.value)
    lines = [f"{icon}  *{event.workflow}* {headline}"]

    step = event.data.get("agent_name")
    if isinstance(step, str) and step:
        lines.append(f"> step: `{step}`")

    if event.signal is RunSignal.DECISION_NEEDED:
        options = event.data.get("options")
        if isinstance(options, list) and options:
            lines.append("> waiting on: " + ", ".join(f"`{o}`" for o in options))
        prompt = event.data.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            lines.append("> " + _first_line(prompt))

    if event.signal is RunSignal.DECISION_MADE:
        chosen = event.data.get("selected_option")
        if isinstance(chosen, str) and chosen:
            lines.append(f"> answered: `{chosen}`")
        note = event.data.get("additional_input")
        if isinstance(note, dict):
            lines.extend(f"> {key}: {value}" for key, value in note.items() if value)

    if event.signal in (RunSignal.RUN_FAILED, RunSignal.STEP_FAILED):
        reason = event.data.get("termination_reason") or event.data.get("error")
        if isinstance(reason, str) and reason.strip():
            lines.append(f"> {_first_line(reason)}")

    footer = f"run `{event.run_id}`"
    lines.append(f"{footer} · {dashboard}" if dashboard else footer)
    return {"text": "\n".join(lines)}


def _first_line(text: str, *, limit: int = 160) -> str:
    """The opening of a prompt, flattened. A channel is not a document."""
    opening = " ".join(text.strip().splitlines()[:1])
    return opening if len(opening) <= limit else opening[: limit - 1] + "…"


API = "https://slack.com/api/chat.postMessage"
REPLY_TIMEOUT_SECONDS = 10.0


def reply(
    *,
    token: str,
    channel: str,
    thread_ts: str,
    text: str,
    timeout: float = REPLY_TIMEOUT_SECONDS,
) -> str:
    """Say ``text`` under ``thread_ts``. Returns "" on success, else why not.

    ``chat.postMessage`` rather than the click's ``response_url``: that one posts
    where the *message* was, which for a button inside a thread is the channel
    root — so the answer to a question asked in a thread landed outside it, next
    to every other run's. Threading needs the parent, and the parent is in the
    payload.

    Never raises. Saying what happened is the last thing in a sequence whose
    real work — answering the gate — is already done.
    """
    body: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    request = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read())
    except (OSError, json.JSONDecodeError) as exc:
        return f"could not reach Slack: {type(exc).__name__}"
    if isinstance(answer, dict) and answer.get("ok"):
        return ""
    error = str(answer.get("error")) if isinstance(answer, dict) else "unreadable reply"
    return f"Slack refused the reply: {error}"
