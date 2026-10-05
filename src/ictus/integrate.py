"""Attaching an integration to a pipeline, without the pipeline knowing one.

A declaration says *where* a run reports; this decides *when*. The announcements
are inserted when the pipeline is loaded, the same way the start gate is — so a
pipeline carries one line about Slack rather than four nodes and three data
edges, and deleting that line removes it entirely.

What gets inserted, and why those points:

* **One opener**, ahead of everything, which the whole run then hangs under. It
  runs before the start gate, because a run parked on a gate nobody was told
  about is the failure this exists to prevent.
* **Before every gate**, carrying that gate's own choices as buttons. A gate is
  the only place a run stops and waits for a person, so it is the only place a
  report has to arrive before anything else can happen.
* **Before every explicit exit**, saying how it ended.

Nothing is inserted *after* a gate. A branch has a target per choice, so "after"
is several places, and the answer is already reported: a button press is
acknowledged in the thread by whatever is listening, and the engine's own
``gate_resolved`` is what ``ictus watch`` reports. Two mechanisms saying the same
thing twice is worse than one saying it once.

Inserted at load time, so what is committed in ``build/`` is what runs — the
same property the start gate has, and the reason neither is a surprise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import GateNode, TerminateNode
from ictus.graph.signals import RunSignal
from ictus.stdlib.steps.announce import THREAD_PORT, announce

if TYPE_CHECKING:
    from ictus.graph.node import Node
    from ictus.graph.pipeline import Pipeline
    from ictus.graph.requirements import Integration

__all__ = ["OPENER_ID", "apply_integrations"]

#: The announcement everything else hangs under.
OPENER_ID = "report_opened"

_PREFIX = "report_"


def apply_integrations(pipeline: Pipeline) -> Pipeline:
    """Insert the announcements every attached integration asked for.

    Runs before the start gate is added, so the opener can be placed in front of
    it and so the gate's own prompt counts the steps honestly.
    """
    for target in pipeline.integrations:
        if target.reports:
            _attach(pipeline, target)
    return pipeline


def _attach(pipeline: Pipeline, target: Integration) -> None:
    """Insert one integration's announcements, and thread them together."""
    label = pipeline.pipeline_id
    opener: Node | None = None

    if target.threads:
        opener = pipeline.before_start_gate(
            announce(
                node_id=_name(pipeline, OPENER_ID),
                description=f"Opens this run's conversation with {target.name}",
                text=f"*{label}* — starting.",
                to=target,
            )
        )

    if target.wants(RunSignal.DECISION_NEEDED):
        for gate in [node for node in pipeline.nodes if isinstance(node, GateNode)]:
            _before(pipeline, target, gate, opener, text=_ask(label, gate), answers=gate)

    if target.wants(RunSignal.RUN_FINISHED) or target.wants(RunSignal.RUN_FAILED):
        for exit_node in [node for node in pipeline.nodes if isinstance(node, TerminateNode)]:
            if not _reported(target, exit_node):
                continue
            _before(pipeline, target, exit_node, opener, text=_ended(label, exit_node))


def _reported(target: Integration, exit_node: TerminateNode) -> bool:
    """Whether this exit is one the integration asked to hear about."""
    failed = exit_node.status == "failed"
    return target.wants(RunSignal.RUN_FAILED if failed else RunSignal.RUN_FINISHED)


def _before(
    pipeline: Pipeline,
    target: Integration,
    node: Node,
    opener: Node | None,
    *,
    text: str,
    answers: GateNode | None = None,
) -> None:
    """Put an announcement in front of ``node``, hung under the opener."""
    said = pipeline.insert_before(
        node,
        announce(
            node_id=_name(pipeline, f"{_PREFIX}{node.node_id}"),
            description=f"Report {node.node_id!r} to {target.name}",
            text=text,
            to=target,
            thread=opener.ref(THREAD_PORT) if opener is not None else None,
            answers=answers if answers is not None and target.threads else None,
        ),
    )
    if opener is not None:
        pipeline.feed(opener, THREAD_PORT, said, opener.node_id)


def _ask(label: str, gate: GateNode) -> str:
    """What a gate's announcement says. The prompt is the question."""
    asked = gate.prompt if isinstance(gate.prompt, str) else ""
    opening = asked.strip().splitlines()[0] if asked.strip() else f"{gate.node_id} needs an answer"
    return f"*{label}* needs a decision — {opening}"


def _ended(label: str, exit_node: TerminateNode) -> str:
    reason = (exit_node.reason or "").strip() if isinstance(exit_node.reason, str) else ""
    if exit_node.status == "failed":
        return f":rotating_light: *{label}* failed" + (f" — {reason}" if reason else "")
    return f":white_check_mark: *{label}* finished" + (f" — {reason}" if reason else "")


def _name(pipeline: Pipeline, wanted: str) -> str:
    """``wanted``, or the next free spelling of it.

    An inserted node must not collide with one somebody wrote, and a collision
    is the author's name winning rather than an error they did not cause.
    """
    taken = {node.node_id for node in pipeline.nodes}
    if wanted not in taken:
        return wanted
    for suffix in range(2, 100):
        candidate = f"{wanted}_{suffix}"
        if candidate not in taken:
            return candidate
    return f"{wanted}_{len(taken)}"
