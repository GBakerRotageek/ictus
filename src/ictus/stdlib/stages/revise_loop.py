"""Draft, review, revise — the loop most gated pipelines are built around."""

from __future__ import annotations

from ictus.graph.node import AgentNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import optional, ref_to, tpl
from ictus.graph.stage import Stage
from ictus.stdlib.gates.approval import approval_gate
from ictus.stdlib.terminals.succeed import succeed

__all__ = ["revise_loop"]


def revise_loop(
    *,
    stage_id: str,
    task: str,
    artifact: str = "draft",
    description: str = "",
    passes: int = 3,
) -> Stage:
    """A stage that produces something, gets it reviewed, and revises on rejection.

    The rejection branch carries the reviewer's notes back into the producer, so
    a second pass has information the first did not. That feedback edge is the
    only thing separating a revise loop from a retry loop, and it is the part
    that is easy to leave out — the graph looks identical without it.

    Three details here are Conductor semantics rather than style:

    * The producer's reference to the gate is a forward reference into the loop.
      The guard it needs on the first pass is emitted by the compiler, not
      remembered by the author — a live run died on exactly that omission.
    * ``passes`` becomes the loop bound. Conductor's ``max_iterations`` default
      of 10 total steps would stop a three-node loop midway through its fourth
      pass, at a number nobody chose.
    * The entry point is pinned. Every node in a loop has an inbound edge, so
      there is no unique root to infer.

    Contract: input ``brief`` (string) in, output ``result`` (string) out.
    """
    stage = Stage(
        stage_id=stage_id,
        description=description or f"Draft and review: {task}",
        loop_passes=passes,
    )
    brief = stage.body.declare_input("brief", PortType.STRING, description=f"What to {task}")

    produce = stage.body.add(
        AgentNode(
            node_id="produce",
            description=f"Produce the {artifact}",
            inputs=(
                InputPort("brief", PortType.STRING),
                InputPort("notes", PortType.STRING, "Reviewer notes", optional=True),
            ),
            prompt=tpl(
                f"{task}\n\n",
                brief.ref(),
                "\n\n",
                # A forward reference: the reviewer does not exist yet. The guard
                # this needs on the first pass is added by the compiler, which
                # already knows the reference is deferred.
                optional(
                    f"A previous {artifact} was rejected with these notes — address each one:\n",
                    ref_to("review", "notes", PortType.STRING),
                ),
            ),
            declared_outputs=(OutputPort("result", PortType.STRING, f"The {artifact}"),),
        )
    )
    review = stage.body.add(
        approval_gate(
            node_id="review",
            description=f"Review the {artifact}",
            inputs=(InputPort("result", PortType.STRING),),
            prompt=tpl(f"Approve this {artifact}?\n\n", produce.ref("result")),
            reject_label="Reject and revise",
        )
    )
    accepted = stage.body.add(
        succeed(node_id="accepted", reason=f"{artifact} approved after review")
    )

    stage.body.set_entry(produce)
    stage.body.connect_input(brief, produce, "brief")
    stage.body.connect(produce, "result", review, "result")
    stage.body.branch(review, {"approved": accepted, "rejected": produce})
    stage.body.feed(review, "notes", produce, "notes")
    stage.body.expose_output("result", produce, "result")
    return stage
