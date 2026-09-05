"""Two-way approve/reject gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import GateChoice, GateNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort
    from ictus.graph.ref import Template

__all__ = ["approval_gate"]


def approval_gate(
    *,
    node_id: str = "approval_gate",
    prompt: str | Template,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    approve_label: str = "Approve",
    reject_label: str = "Reject",
    notes_field: str | None = "notes",
) -> GateNode:
    """A gate offering exactly approve and reject.

    Emits ``type: human_gate``. That matters beyond correctness: the fleet TUI
    infers "at gate" solely from an unclosed ``gate_presented`` event, so a pause
    modelled as a plain agent leaves the run reporting ``running`` forever and
    the gate bell never rings.

    ``notes_field`` adds a free-text prompt on rejection and exposes it as an
    output port. Feeding that port back into the loop is what makes a revise
    cycle worth running — without it the retry re-runs with no new information.
    Remember that a template reading it must guard with ``{% if <gate> is
    defined %}``: the first pass has no gate output and Conductor renders with
    strict undefined.
    """
    return GateNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        prompt=prompt,
        choices=(
            GateChoice(value="approved", label=approve_label),
            GateChoice(
                value="rejected",
                label=reject_label,
                prompt_for=notes_field,
                multiline=notes_field is not None,
            ),
        ),
    )
