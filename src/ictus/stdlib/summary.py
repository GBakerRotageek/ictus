"""Summary and formatting nodes.

Convert structured data into human-readable formats.
Useful for presenting information before gates and reporting.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.types import PortType


def text_summary(
    name: str = "Text Summary",
    node_id: str = "text_summary",
    description: str = "Format data into human-readable summary",
) -> Node:
    """Create a text summary node.

    Formats structured data into a readable text summary.
    Useful before gates to provide context to humans.

    Inputs:
        - data (JSON): Data to summarize
        - template (TEXT, optional): Summary template/format

    Outputs:
        - summary (TEXT): Formatted text summary
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("data", PortType.JSON, "Data to summarize")
        .input("template", PortType.TEXT, "Summary template", optional=True)
        .output("summary", PortType.TEXT, "Formatted summary")
        .build()
    )


def status_report(
    name: str = "Status Report",
    node_id: str = "status_report",
    description: str = "Generate execution status report",
) -> Node:
    """Create a status report node.

    Generates a status report from execution results.
    Perfect for communicating progress and outcomes.

    Inputs:
        - results (JSON): Execution results
        - duration (NUMBER, optional): Execution time in seconds
        - errors (ARRAY, optional): Any errors encountered

    Outputs:
        - report (TEXT): Status report text
        - status (STRING): "success", "warning", or "failure"
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("results", PortType.JSON, "Execution results")
        .input("duration", PortType.NUMBER, "Execution time (seconds)", optional=True)
        .input("errors", PortType.ARRAY, "Errors encountered", optional=True)
        .output("report", PortType.TEXT, "Status report")
        .output("status", PortType.STRING, "success, warning, or failure")
        .build()
    )
