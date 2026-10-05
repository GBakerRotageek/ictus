"""Moments in a run that something outside it may want to hear about.

Named for what happens rather than for what any engine calls it, the same way
``NodeKind`` is. A backend maps these onto its own vocabulary — one signal may
cover several of an engine's event names — and declares in ``Capabilities``
which ones it can actually report, so a pipeline subscribing to a moment the
target cannot observe is refused while it is being written.

These are *not* a second routing mechanism. A gate's answer and a scope's
outcome are already edges in the graph, and acting on one belongs in the graph
where it is costed and visible. A signal is for telling somebody, and for the
moments no node can observe because the engine, not a step, is what moved:
a budget tripping, a person pausing the run, the whole thing failing.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["RunSignal"]


class RunSignal(StrEnum):
    """A moment in a run worth reporting."""

    RUN_STARTED = "run_started"
    """The run began. Carries what it was given to work on."""

    RUN_FINISHED = "run_finished"
    """The run ended successfully. Includes a run that a person declined at the
    start gate — nothing was attempted, so nothing failed."""

    RUN_FAILED = "run_failed"
    """The run ended badly, whether a step failed or the engine stopped it."""

    RUN_PAUSED = "run_paused"
    """A person paused the run and it is waiting to be resumed."""

    DECISION_NEEDED = "decision_needed"
    """Somebody has to answer something before the run goes further.

    The one worth notifying on above all others: the run is parked, spending
    nothing and achieving nothing, and it stays parked until a person arrives.
    A gate nobody knows is open is the whole reason this exists.
    """

    DECISION_MADE = "decision_made"
    """Somebody answered. Carries the choice and any text they left with it."""

    STEP_FAILED = "step_failed"
    """One step failed, whatever kind of step it was.

    Not the same as ``RUN_FAILED``: a graph can route around a failed step, so
    this fires on a run that goes on to succeed.
    """

    BUDGET_EXCEEDED = "budget_exceeded"
    """Spend passed the ceiling the run was given.

    Whether that stops the run depends on ``budget_mode``, so this says the
    ceiling was crossed and nothing about what happened next.
    """
