"""Terminal nodes - Mark workflow endpoints.

Success and failure nodes represent the final states of workflow execution.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.types import PortType


def success_node(
    name: str = "Success",
    node_id: str = "success",
    description: str = "Terminal success node",
) -> Node:
    """Create a success terminal node.

    Marks the end of a successful workflow execution.
    Takes a summary and optional results.

    Inputs:
        - summary (TEXT): Final summary
        - results (JSON, optional): Final results/metrics

    Outputs:
        - status (STRING): Always "success"
        - results (JSON): Results passed through
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("summary", PortType.TEXT, "Final summary")
        .input("results", PortType.JSON, "Final results", optional=True)
        .output("status", PortType.STRING, "success")
        .output("results", PortType.JSON, "Results passed through")
        .build()
    )


def failure_node(
    name: str = "Failure",
    node_id: str = "failure",
    description: str = "Terminal failure node",
) -> Node:
    """Create a failure terminal node.

    Marks the end of a failed workflow execution.
    Captures error details.

    Inputs:
        - error_message (TEXT): Error description
        - error_details (JSON, optional): Detailed error info

    Outputs:
        - status (STRING): Always "failure"
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("error_message", PortType.TEXT, "Error description")
        .input("error_details", PortType.JSON, "Detailed error info", optional=True)
        .output("status", PortType.STRING, "failure")
        .build()
    )
