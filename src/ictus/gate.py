"""The start gate — a person confirms before a pipeline does anything.

Conductor's dashboard is a view onto a live engine, not a launcher: its API has
``stop``, ``kill`` and ``resume`` but no start (``web/server.py``), and
``--dry-run`` returns before the dashboard is built (``cli/app.py``). So there is
no way to load a workflow into the UI and press go.

A human gate at the entry point is that, built out of what the engine has. It
costs no provider call and no money, the dashboard comes up with the whole graph
in it, and nothing has happened yet. It is on by default because the alternative
default is that every ``ictus run`` starts spending immediately, and the first
thing a person usually wants to check — am I in the right directory, is this the
right diff — is only visible once it is too late.

The gate is added at emit time, so what is committed in ``build/`` is what runs.
Turn it off with ``start_gate: false`` in the folder's ``config.yaml``.

The prompt reads the workflow's own inputs, so the person sees the actual values
rather than the fact that some exist. Optional ones are guarded on truthiness —
an absent optional input is bound to ``None`` by the engine, and a block guarded
on definedness would print that word under a heading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import GateChoice, GateNode
from ictus.graph.ports import InputPort
from ictus.graph.ref import TemplatePart, optional, tpl
from ictus.stdlib.terminals.succeed import succeed

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline

__all__ = ["CANCELLED_ID", "GATE_ID", "add_start_gate", "attach_start_herald"]

GATE_ID = "confirm_start"
CANCELLED_ID = "not_started"


def add_start_gate(pipeline: Pipeline) -> Pipeline:
    """Put a confirmation gate in front of ``pipeline``'s entry point.

    Choosing not to start ends the run *successfully*: nothing was attempted, so
    nothing failed, and a caller should not have to treat "a person looked at it
    and said no" as an error.
    """
    for taken in (GATE_ID, CANCELLED_ID):
        if any(node.node_id == taken for node in pipeline.nodes):
            raise CompositionError(
                f"pipeline {pipeline.pipeline_id!r} already has a node called {taken!r}, "
                "which the start gate needs. Rename it, or set start_gate: false."
            )
    # Read before the gate exists, so the prompt describes the work rather than
    # the asking, and so a herald can be put in front of the gate below.
    entry = pipeline.entry()
    herald = pipeline.start_herald
    if herald is not None and herald is entry:
        raise CompositionError(
            f"pipeline {pipeline.pipeline_id!r} both names {herald.node_id!r} as its entry "
            "point and asks to run it before the start gate; leave the entry on the first "
            "real step and the placement is worked out from there"
        )
    parts: list[TemplatePart] = [
        f"Start **{pipeline.pipeline_id}**?\n\n",
    ]
    if pipeline.description:
        parts.append(f"{pipeline.description}\n\n")
    # Counted before the gate's own two nodes are added: the person is deciding
    # whether to spend the work, and the gate is not part of it.
    once, budget = pipeline.total_cost(), pipeline.budget_cost()
    if budget > once:
        parts.append(
            f"{once} step(s) on one pass, up to {budget} with loops, "
            f"beginning with `{entry.node_id}`.\n"
        )
    else:
        parts.append(f"{once} step(s), beginning with `{entry.node_id}`.\n")

    declared = pipeline.workflow_inputs
    if declared:
        parts.append("\n---\n")
        for param in declared:
            body: list[TemplatePart] = [
                f"\n**{param.name}**\n\n```\n",
                param.ref(),
                "\n```\n",
            ]
            # A required input is always there; an optional one renders nothing
            # rather than the word None.
            parts.append(tpl(*body) if param.required else optional(*body))

    gate = pipeline.add(
        GateNode(
            node_id=GATE_ID,
            description="Confirm before anything runs",
            prompt=tpl(*parts),
            inputs=tuple(
                InputPort(param.name, param.port_type, optional=not param.required)
                for param in declared
            ),
            choices=(
                # Start first: `--skip-gates` takes the first option, and a run
                # launched unattended has made this decision by being unattended.
                GateChoice("start", "Start the run"),
                GateChoice("cancel", "Stop — do not run"),
            ),
        )
    )
    stopped = pipeline.add(
        succeed(
            node_id=CANCELLED_ID,
            reason="Stopped at the start gate; nothing was run.",
            result={"started": "false"},
        )
    )
    for param in declared:
        pipeline.connect_input(param, gate, param.name)
    pipeline.branch(gate, {"start": entry, "cancel": stopped})
    if herald is None:
        pipeline.set_entry(gate)
    else:
        # The whole point: something gets to speak before the run parks.
        pipeline.route(herald, gate)
        pipeline.set_entry(herald)
    return pipeline


def attach_start_herald(pipeline: Pipeline) -> Pipeline:
    """Put whatever ``before_start_gate`` named in front, with no gate behind it.

    The other half of the policy. Without this a pipeline that declares a herald
    and turns the gate off would carry an unreachable node, and the lint would
    refuse it for a reason the author never wrote down.
    """
    herald = pipeline.start_herald
    if herald is None:
        return pipeline
    entry = pipeline.entry()
    if herald is entry:
        return pipeline
    pipeline.route(herald, entry)
    pipeline.set_entry(herald)
    return pipeline
