"""Gate nodes - Human approval points with context.

Gates pause workflow execution and wait for human input.
They display context/summaries and collect decisions or feedback.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.types import PortType


def approval_gate(
    name: str = "Approval Gate",
    node_id: str = "approval_gate",
    description: str = "Human approval gate with summary",
) -> Node:
    """Create an approval gate node.

    Takes a summary input, displays it to human, waits for approval/rejection.
    Useful for critical decision points in pipelines.

    Inputs:
        - summary (TEXT): Work summary to review
        - context (JSON, optional): Additional context data

    Outputs:
        - decision (STRING): "approved" or "rejected"
        - feedback (TEXT, optional): Human feedback/notes
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("summary", PortType.TEXT, "Summary of work to review")
        .input("context", PortType.JSON, "Additional context", optional=True)
        .output("decision", PortType.STRING, "approved or rejected")
        .output("feedback", PortType.TEXT, "Human feedback/notes")
        .build()
    )


def multi_choice_gate(
    name: str = "Choice Gate",
    node_id: str = "choice_gate",
    description: str = "Present choices to human",
) -> Node:
    """Create a multi-choice gate node.

    Presents multiple options to human, waits for selection.
    Useful for routing decisions and user-driven branching.

    Inputs:
        - summary (TEXT): Context for the choice
        - choices (JSON): Array of choice objects with id, label, description

    Outputs:
        - selected_choice (STRING): ID of selected choice
        - metadata (JSON, optional): Additional selection metadata
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("summary", PortType.TEXT, "Context for the choice")
        .input("choices", PortType.JSON, "Array of {id, label, description}")
        .output("selected_choice", PortType.STRING, "ID of selected choice")
        .output("metadata", PortType.JSON, "Selection metadata")
        .build()
    )
