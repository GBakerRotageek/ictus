"""Which Conductor events stand for which ``RunSignal``.

The mapping lives here rather than beside the enum because the left-hand side is
engine-neutral and the right-hand side is Conductor's spelling, and a Conductor
event name above ``interfaces/conductor/`` is a defect with a name.

One signal covers several event names: a person answering a gate and a person
answering a questions step are the same moment to anyone being told about it,
and so is a failure whatever kind of step it happened in. Going the other way,
most of Conductor's ~45 event types map to no signal at all — ``agent_started``,
``checkpoint_saved``, ``route_taken`` and the per-tool events are a trace, not
news, and forwarding them would make a report a firehose.

A gate is not the only place a run waits for a person. The iteration-limit
prompt parks a background run until somebody raises the limit or stops it, and
an agent's dialog waits for a reply; no step can stand in front of either, so
the watcher is the only thing that can say a run is waiting on them.

``test_signals.py`` checks every name here against the installed engine's own
source, which is what catches the engine renaming an event under us. The
recorded streams in ``tests/fixtures/`` hold only the names a gate run emits.
"""

from __future__ import annotations

from ictus.graph.signals import RunSignal

__all__ = ["REPORTABLE", "SIGNAL_EVENTS", "signal_for"]

#: Conductor event names, by the signal they stand for.
SIGNAL_EVENTS: dict[RunSignal, frozenset[str]] = {
    RunSignal.RUN_STARTED: frozenset({"workflow_started"}),
    RunSignal.RUN_FINISHED: frozenset({"workflow_completed"}),
    RunSignal.RUN_FAILED: frozenset({"workflow_failed"}),
    RunSignal.RUN_PAUSED: frozenset({"agent_paused"}),
    RunSignal.DECISION_NEEDED: frozenset(
        {"gate_presented", "questions_presented", "iteration_limit_reached", "dialog_started"}
    ),
    RunSignal.DECISION_MADE: frozenset(
        {"gate_resolved", "questions_completed", "iteration_limit_resolved", "dialog_completed"}
    ),
    RunSignal.STEP_FAILED: frozenset(
        {
            "agent_failed",
            "agent_validation_failed",
            "script_failed",
            "set_failed",
            "wait_failed",
            "mcp_failed",
            "subworkflow_failed",
            "parallel_agent_failed",
            "for_each_item_failed",
        }
    ),
    RunSignal.BUDGET_EXCEEDED: frozenset({"budget_exceeded"}),
}

#: Every signal Conductor can report. Declared in ``Capabilities.signals``.
REPORTABLE: frozenset[RunSignal] = frozenset(SIGNAL_EVENTS)

_BY_EVENT: dict[str, RunSignal] = {
    event: signal for signal, events in SIGNAL_EVENTS.items() for event in events
}


def signal_for(event_type: str) -> RunSignal | None:
    """The signal ``event_type`` stands for, or ``None`` if it is not news.

    Returning ``None`` rather than raising is deliberate: an engine upgrade that
    adds an event type must not break a watcher that is mid-run. An unmapped
    event is dropped, and the test suite is what notices a rename.
    """
    return _BY_EVENT.get(event_type)
