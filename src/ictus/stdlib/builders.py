"""Pre-built node combinations for common workflow patterns.

Convenience builders that package frequently-used node combinations.
"""

from __future__ import annotations

from ictus.node import Node  # noqa: TC001
from ictus.stdlib.gates import approval_gate
from ictus.stdlib.summary import text_summary
from ictus.stdlib.terminal import failure_node, success_node


def build_approval_gate_pair() -> tuple[Node, Node, Node]:
    """Build an approval gate pair (gate + success/failure nodes).

    Returns:
        (approval_gate_node, success_node, failure_node)

    Usage:
        gate, success, fail = build_approval_gate_pair()
        gate_elem = pipeline.add_node(gate)
        success_elem = pipeline.add_node(success)
        fail_elem = pipeline.add_node(fail)
        pipeline.branch(gate_elem, branches={
            "approved": success_elem,
            "rejected": fail_elem,
        })
    """
    gate = approval_gate(name="Approval", node_id="approval")
    success = success_node(name="Approved", node_id="approved")
    failure = failure_node(name="Rejected", node_id="rejected")
    return gate, success, failure


def build_summary_approval_gate() -> tuple[Node, Node]:
    """Build a summary + approval gate pair.

    Returns:
        (summary_node, approval_gate_node)

    Useful pipeline pattern:
        data -> summary -> approval -> [branches]

    Usage:
        summary, gate = build_summary_approval_gate()
        summary_elem = pipeline.add_node(summary)
        gate_elem = pipeline.add_node(gate)
        pipeline.connect(data_elem, summary_elem, ...)
        pipeline.connect(summary_elem, gate_elem, ...)
    """
    summary = text_summary(name="Summarize", node_id="summarize")
    gate = approval_gate(name="Review", node_id="review")
    return summary, gate
