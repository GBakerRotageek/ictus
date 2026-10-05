"""Moments in a run that something outside it may want to hear about.

Named for what happens rather than for what any engine calls it, the way
``NodeKind`` is. A backend maps these onto its own vocabulary and declares in
``Capabilities`` which it can report, so subscribing to one the target cannot
observe is refused while it is being written.

Not a second routing mechanism: a gate's answer and a scope's outcome are
already edges, and acting on one belongs in the graph where it is costed. This
is for telling somebody — and for the moments no node can observe, because the
engine rather than a step is what moved.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ANNOUNCED_BY_STEPS", "RunSignal"]


class RunSignal(StrEnum):
    """A moment in a run worth reporting."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    """Includes a run declined at the start gate: nothing was attempted."""

    RUN_FAILED = "run_failed"
    RUN_PAUSED = "run_paused"
    """A person paused it and it is waiting to be resumed."""

    DECISION_NEEDED = "decision_needed"
    """The one worth notifying on above all others. The run is parked, spending
    nothing and achieving nothing, until somebody arrives."""

    DECISION_MADE = "decision_made"
    STEP_FAILED = "step_failed"
    """Not ``RUN_FAILED``: a graph can route around a failed step, so this fires
    on runs that go on to succeed."""

    BUDGET_EXCEEDED = "budget_exceeded"
    """Whether it stops the run depends on ``budget_mode``, so this says the
    ceiling was crossed and nothing about what happened next."""


#: What a step can stand in front of, so an attached integration reports it from
#: inside the run: the start, every gate and question, every way the graph ends.
#: ``RUN_FAILED`` only partly — an explicit failed exit has a step in front of
#: it, an engine failure does not. Anything else reaches only ``ictus watch``.
ANNOUNCED_BY_STEPS = frozenset(
    {
        RunSignal.RUN_STARTED,
        RunSignal.DECISION_NEEDED,
        RunSignal.RUN_FINISHED,
        RunSignal.RUN_FAILED,
    }
)
