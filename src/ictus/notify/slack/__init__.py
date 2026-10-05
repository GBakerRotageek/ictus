"""Slack. Nothing above this package names it.

    send.py     building a report and posting one
    listen.py   receiving a button press and answering the gate it names

A second service is a sibling folder, and nothing else moves.
"""

from __future__ import annotations

from ictus.notify.slack.listen import Click, SlackError, clicks, resolve, say
from ictus.notify.slack.send import API, API_ENV, reply, slack_channel, slack_webhook

__all__ = [
    "API",
    "API_ENV",
    "Click",
    "SlackError",
    "clicks",
    "reply",
    "resolve",
    "say",
    "slack_channel",
    "slack_webhook",
]
