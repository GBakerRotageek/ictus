"""Slack as a thing that *sends* — presses, forms, and requests to start a run.

The mirror of ``ictus.notify.slack``, which is Slack as a thing that is told.
That one is data a pipeline declares; this one is a process that runs.
"""

from __future__ import annotations

from ictus.bridge.slack.listen import (
    Click,
    Note,
    SlackError,
    SlackUnreachableError,
    asked,
    events,
    open_form,
    open_socket,
    presses,
    retire,
    say,
    verdict,
)

__all__ = [
    "Click",
    "Note",
    "SlackError",
    "SlackUnreachableError",
    "asked",
    "events",
    "open_form",
    "open_socket",
    "presses",
    "retire",
    "say",
    "verdict",
]
