"""Slack, as a place a run reports to. Nothing above this package names it.

    send.py   building a report and posting one

Pure data: ``slack_channel`` and ``slack_webhook`` return an ``Integration``
carrying a program nothing in ictus reads.

Receiving from Slack is a process rather than a declaration, and lives in
``ictus.bridge``.
"""

from __future__ import annotations

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
    "api_call",
    "endpoint",
    "reply",
    "slack_channel",
    "slack_webhook",
]
