"""Answering a gate from a button somebody pressed in a channel.

Between the two boundaries and owned by neither. The press comes from a service
(``ictus.notify``) and the gate belongs to a run on an engine
(``ictus.interfaces``); this is the one module that holds both, so neither has
to know the other exists.

A press does not name its run. It names the step that posted its button and the
message it is on, and the run is found by asking each live run's history which
of them posted that message. That one lookup settles two things a run id never
could:

* **Which run.** No engine variable has to reach the button. The first version
  read one that a foreground run never sets, and every press on such a run was
  refused as "no longer running" while it sat waiting.
* **Which round.** A gate in a loop is asked again under the same name, and the
  engine matches an answer to a gate by name alone — so a press on the message
  from an earlier round answered the current one, approving a revision nobody
  had read. A press is accepted only on the message the step posted *last*.

And one thing the engine does not check until too late: the answer must be one
the gate offers. The engine takes any other value with a 200, then fails the run
on it.
"""

from __future__ import annotations

import http.client
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.interfaces.conductor.events import history, offered, step_outputs
from ictus.interfaces.conductor.respond import answer_gate
from ictus.interfaces.conductor.runs import LiveRun, live_runs

if TYPE_CHECKING:
    from ictus.notify.slack.listen import Click, Note

__all__ = ["Outcome", "resolve", "submit"]


@dataclass(frozen=True, slots=True)
class Outcome:
    """What became of one press."""

    answered: bool
    reason: str = ""
    """Why not, when not. Phrased for whoever pressed, not for a log."""

    run_id: str = ""
    needs_note: bool = False
    """The choice asks for text: ask for it, then ``submit`` what comes back."""


def resolve(click: Click, *, allowed: frozenset[str] = frozenset()) -> Outcome:
    """Answer the gate a press names, or say why not.

    ``allowed`` is the user ids that may answer. Empty means anyone who can see
    the button, which is right for a channel people were invited to and wrong
    for a deploy; there is no middle setting, because who may approve something
    is a decision to make rather than inherit.
    """
    if allowed and click.who not in allowed:
        return Outcome(False, f"they are not allowed to answer {click.gate}")
    found = _current(click)
    if isinstance(found, Outcome):
        return found
    if click.ask:
        return Outcome(False, run_id=found.run_id, needs_note=True)
    return _answer(found, click, None)


def submit(note: Note, *, allowed: frozenset[str] = frozenset()) -> Outcome:
    """Answer with the text a form collected.

    Checked again from the start: the question may have been answered, or asked
    again, while the form was open.
    """
    click = note.click
    if allowed and click.who not in allowed:
        return Outcome(False, f"they are not allowed to answer {click.gate}")
    found = _current(click)
    if isinstance(found, Outcome):
        return found
    return _answer(found, click, note.text)


def _current(click: Click) -> LiveRun | Outcome:
    """The run that posted this message, if it is still the question being asked."""
    for run in live_runs():
        try:
            events = history(run)
        except (OSError, ValueError, http.client.HTTPException):
            continue  # a run that cannot be read cannot be the one that posted it
        posted = [str(output.get("thread", "")) for output in step_outputs(events, click.step)]
        if click.message_ts not in posted:
            continue
        if posted[-1] != click.message_ts:
            return Outcome(
                False,
                f"{click.gate} has been asked again since that message; answer the newest one, "
                "or answer in the dashboard",
                run.run_id,
            )
        options = offered(events, click.gate)
        if options and click.choice not in options:
            return Outcome(
                False, f"{click.choice!r} is not an answer {click.gate} offers", run.run_id
            )
        return run
    return Outcome(
        False,
        "no run on this machine asked that question; it has finished, or it is running elsewhere",
    )


def _answer(run: LiveRun, click: Click, note: str | None) -> Outcome:
    result = answer_gate(run, gate=click.gate, choice=click.choice, note=note)
    if result.accepted:
        return Outcome(True, run_id=run.run_id)
    return Outcome(False, f"the run would not take it: {result.detail}", run.run_id)
