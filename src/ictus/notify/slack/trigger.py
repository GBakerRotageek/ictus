"""Starting a run because somebody said so in a channel.

The last link. A message matching a prefix starts a pipeline, the message's own
timestamp becomes the conversation the run reports into, and whatever followed
the prefix becomes its input — so the answer arrives under the question rather
than beside it.

Two guards that are not optional:

* **The bot's own messages are ignored.** It reports into the channel it
  watches, so without this its first announcement starts a second run, which
  announces, which starts a third.
* **A message is acted on once.** Slack redelivers what it believes was not
  acknowledged, and a redelivery is indistinguishable from somebody saying the
  same thing twice.

Spawning is a subprocess, not an import: a run outlives the listener that
started it, and ``ictus run`` already knows how to detach one.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_PREFIX", "Asked", "Started", "Trigger", "asked", "start"]

#: What somebody types to start one.
DEFAULT_PREFIX = "Start test run:"

#: How long to wait for `ictus run` to detach before giving up on it.
LAUNCH_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True, slots=True)
class Asked:
    """Somebody asked for a run, and what they asked about."""

    question: str
    thread: str
    """The message's own timestamp. Replies to it open its thread."""

    channel: str
    who: str


@dataclass(frozen=True, slots=True)
class Started:
    """What became of a request to start a run."""

    why: str = ""
    """Empty when it started. Otherwise what to tell the person who asked."""

    dashboard: str = ""
    """Where to watch it. ``ictus run`` prints this and nothing else knows it:
    the port is assigned by the OS when the run binds, so it cannot be worked
    out beforehand."""

    @property
    def ok(self) -> bool:
        return not self.why


@dataclass(frozen=True, slots=True)
class Trigger:
    """What starts a run, and what to start."""

    folder: Path
    prefix: str = DEFAULT_PREFIX
    question_input: str = "question"
    thread_input: str = "reply_to"
    """Not ``thread``: that name collides with what an announcement publishes,
    so no pipeline can declare an input called it."""

    @property
    def pattern(self) -> re.Pattern[str]:
        """The prefix, matched at the start and case-insensitively.

        Loose on purpose: somebody typing this into a channel is not writing a
        command line, and a trigger that fails silently on capitalisation reads
        as a broken bot rather than a near miss.
        """
        return re.compile(rf"^\s*{re.escape(self.prefix)}\s*(?P<question>.+)", re.I | re.S)


def asked(envelope: dict[str, object], trigger: Trigger) -> Iterator[Asked]:
    """The request in one envelope, if it holds one."""
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "event_callback":
        return
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "message":
        return
    # Anything the app itself said, and anything that is not somebody typing:
    # edits, deletions, joins, and the thread-broadcast copies of all three.
    if event.get("bot_id") or event.get("subtype"):
        return
    text = event.get("text")
    if not isinstance(text, str):
        return
    found = trigger.pattern.match(text)
    if found is None:
        return
    # The message's own ts, never its thread_ts: a request made inside somebody
    # else's thread is answered in that thread, not alongside it.
    yield Asked(
        question=found.group("question").strip(),
        thread=str(event.get("ts", "")),
        channel=str(event.get("channel", "")),
        who=str(event.get("user", "")),
    )


def start(request: Asked, trigger: Trigger) -> Started:
    """Launch a run for ``request``, and say where to watch it.

    Never raises. This is driven by somebody typing in a channel, and every way
    it can fail is something to tell them rather than a traceback in a log.
    """
    command = [
        "ictus",
        "run",
        str(trigger.folder),
        "-i",
        f"{trigger.question_input}={request.question}",
        "-i",
        f"{trigger.thread_input}={request.thread}",
    ]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=LAUNCH_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return Started("`ictus` is not on PATH where the listener is running, so nothing started")
    except subprocess.TimeoutExpired:
        return Started("the run did not finish starting in time")
    if done.returncode != 0:
        # Preflight refusing is the common one, and it has already said which
        # variable or command is missing.
        last = (done.stderr or done.stdout or "it refused to start").strip().splitlines()[-1]
        return Started(last)
    return Started(dashboard=_dashboard(done.stdout))


def _dashboard(printed: str) -> str:
    """The run's dashboard, out of what ``ictus run`` printed.

    Scraped rather than asked for: the port is assigned when the run binds, so
    the only thing that knows it is the launch itself.
    """
    for line in printed.splitlines():
        _, sep, rest = line.partition("Dashboard:")
        if sep and rest.strip().startswith("http"):
            return rest.strip()
    return ""
