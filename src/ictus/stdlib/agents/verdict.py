"""Answer a yes/no question as a typed boolean."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import AgentNode
from ictus.graph.ports import OutputPort, PortType
from ictus.graph.ref import Ref, tpl

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort

__all__ = ["verdict"]


def verdict(
    *,
    node_id: str,
    question: str,
    source: Ref,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    output_name: str = "verdict",
) -> AgentNode:
    """Decide a yes/no question about upstream output.

    The boolean output is what a route ``when`` condition can test, which is how
    a machine-decided branch is expressed without stopping for a human. Pair it
    with a catch-all route: falling off the end of a condition list is a runtime
    error that ``conductor validate`` does not detect.
    """
    return AgentNode(
        node_id=node_id,
        description=description or question,
        inputs=tuple(inputs),
        prompt=tpl(f"{question}\n\nAnswer strictly about the material below.\n\n", source),
        declared_outputs=(
            OutputPort(output_name, PortType.BOOLEAN, question),
            OutputPort("rationale", PortType.STRING, "Why that answer"),
        ),
    )
