"""Slack as a thing that *sends* — presses, forms, and requests to start a run.

The mirror of ``ictus.notify.slack``, which is Slack as a thing that is told.
That one is data a pipeline declares; this one is a process that runs.

Two ways in, and which credential you hold decides which: ``listen`` is an app
being told what happened in a channel it was invited to, and hears presses as
well as requests; ``watch`` is you asking a channel you are already in what has
been said, and hears requests only.
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
    refused,
    request_in,
    retire,
    say,
    verdict,
)
from ictus.bridge.slack.watch import Heard, latest, overheard, since

__all__ = [
    "Click",
    "Heard",
    "Note",
    "SlackError",
    "SlackUnreachableError",
    "asked",
    "events",
    "latest",
    "open_form",
    "open_socket",
    "overheard",
    "presses",
    "refused",
    "request_in",
    "retire",
    "say",
    "since",
    "verdict",
]
