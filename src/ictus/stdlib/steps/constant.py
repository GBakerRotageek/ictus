"""A single computed value. Conductor ``type: set`` — no model call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import ComputeNode
from ictus.graph.ports import OutputPort, PortType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort

__all__ = ["constant"]


def constant(
    *,
    node_id: str,
    value: str,
    output_type: PortType | None = None,
    description: str = "",
    inputs: Sequence[InputPort] = (),
) -> ComputeNode:
    """Compute one value, with no provider call and no model cost.

    Pass ``output_type`` whenever the value is not free text. Without it
    Conductor runs the rendered string through a YAML load, so ``"no"`` comes
    back as ``False`` and ``"3"`` as an integer — a silent type change at the
    point a route condition is about to test it.

    The value is readable as ``node.ref("value")``. Conductor stores a single
    ``value:`` as the bare scalar, so that reference renders as ``node.output``
    with no trailing key — a distinction ``ComputeNode.output_ref`` owns, since
    reading ``node.output.value`` off one is a hard template error.

    Still costs one iteration, like every other step.
    """
    return ComputeNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        value=value,
        value_type=output_type,
        declared_outputs=(
            OutputPort("value", output_type or PortType.STRING, description or "The value"),
        ),
    )
