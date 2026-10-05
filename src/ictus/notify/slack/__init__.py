"""Slack. Nothing above this package names it.

    send.py     building a report and posting one
    listen.py   receiving a button press, and saying what became of it

A second service is a sibling folder, and nothing else moves.
"""

from __future__ import annotations

from ictus.notify.slack.listen import (
    Click,
    Note,
    SlackError,
    SlackUnreachableError,
    events,
    open_form,
    presses,
    retire,
    say,
    verdict,
)
from ictus.notify.slack.send import (
    API,
    API_ENV,
    api_call,
    endpoint,
    reply,
    slack_channel,
    slack_webhook,
)

__all__ = [
    "API",
    "API_ENV",
    "Click",
    "Note",
    "SlackError",
    "SlackUnreachableError",
    "api_call",
    "endpoint",
    "events",
    "open_form",
    "presses",
    "reply",
    "retire",
    "say",
    "slack_channel",
    "slack_webhook",
    "verdict",
]
