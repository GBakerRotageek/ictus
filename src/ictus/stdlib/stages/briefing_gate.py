"""Summarise something, then ask a human to decide about it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.graph.node import GateChoice, GateNode
from ictus.graph.ports import InputPort, PortType
from ictus.graph.ref import tpl
from ictus.graph.stage import Stage
from ictus.stdlib.agents.briefing import briefing
from ictus.stdlib.terminals.succeed import succeed

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["APPROVE_OR_REJECT", "ReviewOption", "briefing_gate"]


@dataclass(frozen=True, slots=True)
class ReviewOption:
    """One answer a reviewer can give.

    ``ask_for_notes`` opens a multi-line box and captures what they type. Any
    branch that sends work backwards should use it — a rejection with no reason
    makes the next attempt a guess.
    """

    value: str
    label: str
    ask_for_notes: bool = False


APPROVE_OR_REJECT: tuple[ReviewOption, ...] = (
    ReviewOption("approved", "Approve"),
    ReviewOption("rejected", "Reject", ask_for_notes=True),
)

NOTES_FIELD = "notes"


def briefing_gate(
    *,
    stage_id: str,
    subject: str,
    question: str,
    data_type: PortType = PortType.OBJECT,
    options: Sequence[ReviewOption] = APPROVE_OR_REJECT,
    description: str = "",
) -> Stage:
    """A stage that turns raw data into a decision, and reports which was taken.

    Both options route to the same exit. That is deliberate: this stage's job is
    to *obtain* a decision, not to act on one. It exposes ``decision`` as an
    output, and the parent pipeline branches on it with a route condition:

        p.route(stage_node, proceed, when="{{ review.output.decision == 'approved' }}")
        p.route(stage_node, stop)   # catch-all, emitted last

    Keeping the branch in the parent is what makes the stage reusable — the same
    review step can gate a deploy in one pipeline and a refund in another.

    The summariser earns its place: a gate prompt is the whole of what the
    reviewer reads, and a raw object dump gets approved without being read.

    Contract: input ``data`` (``data_type``, object by default) in;
    ``decision`` and ``summary`` (both string) out. Set ``data_type`` to match
    whatever upstream produces — a stage that emits a string result will not
    connect to an object port, which is the point.
    """
    stage = Stage(stage_id=stage_id, description=description or f"Review {subject}")
    data = stage.body.declare_input("data", data_type, description=f"The {subject} to review")

    summarise = stage.body.add(
        briefing(
            node_id="summarise",
            subject=subject,
            source=data.ref(),
            inputs=(InputPort("data", data_type),),
        )
    )
    review = stage.body.add(
        GateNode(
            node_id="review",
            description=question,
            inputs=(InputPort("summary", PortType.STRING),),
            prompt=tpl(f"{question}\n\n", summarise.ref("summary")),
            choices=tuple(
                GateChoice(
                    value=option.value,
                    label=option.label,
                    prompt_for=NOTES_FIELD if option.ask_for_notes else None,
                    multiline=option.ask_for_notes,
                )
                for option in options
            ),
        )
    )
    recorded = stage.body.add(
        succeed(
            node_id="recorded",
            # Declared as an input, not just referenced. Under
            # ``context.mode: explicit`` a node sees only what it declares, so a
            # template naming something absent from ``input:`` is an undefined
            # variable at run time — after the human has already answered.
            inputs=(InputPort("decision", PortType.STRING),),
            reason=tpl("Decision recorded: ", review.ref("selected")),
        )
    )
    stage.body.feed(review, "selected", recorded, "decision")

    stage.body.connect_input(data, summarise, "data")
    stage.body.connect(summarise, "summary", review, "summary")
    stage.body.branch(review, dict.fromkeys((o.value for o in options), recorded))
    stage.body.expose_output("decision", review, "selected")
    stage.body.expose_output("summary", summarise, "summary")
    if any(o.ask_for_notes for o in options):
        # Defaulted: the field only exists on the branches that asked for it.
        stage.body.expose_output(NOTES_FIELD, review, NOTES_FIELD, default="")
    return stage
