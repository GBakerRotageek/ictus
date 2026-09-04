"""Conditional branching nodes.

Nodes that route workflow execution based on conditions or values.
Used with pipeline.branch() for if/else and switch patterns.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.types import PortType


def if_then_node(
    name: str = "Conditional",
    node_id: str = "if_then",
    description: str = "Conditional branching based on boolean input",
) -> Node:
    """Create a conditional branch node.

    Routes to different paths based on a boolean condition.
    Used with pipeline.branch() for if/else patterns.

    Inputs:
        - condition (BOOLEAN): Branch condition
        - data (JSON, optional): Data to pass through

    Outputs:
        - data (JSON): Data passed through
        - branch (STRING): "true" or "false"
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("condition", PortType.BOOLEAN, "Branch condition")
        .input("data", PortType.JSON, "Data to pass through", optional=True)
        .output("data", PortType.JSON, "Data passed through")
        .output("branch", PortType.STRING, "true or false")
        .build()
    )


def switch_node(
    name: str = "Switch",
    node_id: str = "switch",
    description: str = "Multi-way switch based on string value",
) -> Node:
    """Create a switch branching node.

    Routes to different paths based on string value.
    Used with pipeline.branch() for switch patterns.

    Inputs:
        - value (STRING): Switch value
        - data (JSON, optional): Data to pass through

    Outputs:
        - data (JSON): Data passed through
        - case (STRING): Selected case/branch
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("value", PortType.STRING, "Switch value")
        .input("data", PortType.JSON, "Data to pass through", optional=True)
        .output("data", PortType.JSON, "Data passed through")
        .output("case", PortType.STRING, "Selected case")
        .build()
    )
