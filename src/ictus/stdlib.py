"""Standard library of reusable nodes for common workflow patterns.

Includes gate nodes, summary nodes, conditional branches, and utilities.
All nodes are pre-built and ready to compose into pipelines.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.types import PortType

# ============================================================================
# Gate Nodes - Human approval points with context
# ============================================================================


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


# ============================================================================
# Summary & Formatting Nodes
# ============================================================================


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


# ============================================================================
# Terminal Nodes - Mark workflow endpoints
# ============================================================================


def success_node(
    name: str = "Success",
    node_id: str = "success",
    description: str = "Terminal success node",
) -> Node:
    """Create a success terminal node.

    Marks the end of a successful workflow execution.
    Takes a summary and optional metrics.

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


# ============================================================================
# Utility Nodes - Common workflow utilities
# ============================================================================


def log_node(
    name: str = "Log",
    node_id: str = "log",
    description: str = "Log information for debugging",
) -> Node:
    """Create a log node.

    Logs structured data for debugging and audit trails.
    Data passes through unchanged.

    Inputs:
        - data (JSON): Data to log
        - level (STRING, optional): Log level (info, warn, error)

    Outputs:
        - data (JSON): Same data, passed through
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("data", PortType.JSON, "Data to log")
        .input("level", PortType.STRING, "Log level", optional=True)
        .output("data", PortType.JSON, "Data passed through")
        .build()
    )


def merge_data(
    name: str = "Merge Data",
    node_id: str = "merge_data",
    description: str = "Merge multiple JSON objects",
) -> Node:
    """Create a merge data node.

    Merges multiple JSON objects into a single object.
    Useful for combining results from parallel tasks.

    Inputs:
        - primary (JSON): Primary object
        - secondary (JSON): Secondary object to merge
        - deep (BOOLEAN, optional): Deep merge (default: true)

    Outputs:
        - merged (JSON): Merged object
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("primary", PortType.JSON, "Primary object")
        .input("secondary", PortType.JSON, "Object to merge")
        .input("deep", PortType.BOOLEAN, "Deep merge", optional=True)
        .output("merged", PortType.JSON, "Merged result")
        .build()
    )


def transform_data(
    name: str = "Transform Data",
    node_id: str = "transform_data",
    description: str = "Transform JSON data",
) -> Node:
    """Create a transform data node.

    Transforms JSON data using a transformation spec.
    Useful for data format conversion.

    Inputs:
        - input (JSON): Input data
        - transform_spec (JSON): Transformation specification

    Outputs:
        - output (JSON): Transformed data
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("input", PortType.JSON, "Input data")
        .input("transform_spec", PortType.JSON, "Transformation spec")
        .output("output", PortType.JSON, "Transformed data")
        .build()
    )


def filter_array(
    name: str = "Filter Array",
    node_id: str = "filter_array",
    description: str = "Filter array based on condition",
) -> Node:
    """Create a filter array node.

    Filters an array based on a predicate expression.

    Inputs:
        - array (ARRAY): Array to filter
        - predicate (STRING): Filter predicate/expression

    Outputs:
        - filtered (ARRAY): Filtered array
        - count (NUMBER): Number of items in result
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("array", PortType.ARRAY, "Array to filter")
        .input("predicate", PortType.STRING, "Filter predicate")
        .output("filtered", PortType.ARRAY, "Filtered array")
        .output("count", PortType.NUMBER, "Result count")
        .build()
    )


def wait_node(
    name: str = "Wait",
    node_id: str = "wait",
    description: str = "Pause execution for specified duration",
) -> Node:
    """Create a wait node.

    Pauses workflow execution for a specified duration.
    Useful for rate limiting or staggered processing.

    Inputs:
        - duration (NUMBER): Wait duration in seconds
        - data (JSON, optional): Data to pass through

    Outputs:
        - data (JSON): Same data as input
        - waited (BOOLEAN): Always true
    """
    return (
        NodeBuilder(name, description=description)
        .with_id(node_id)
        .input("duration", PortType.NUMBER, "Wait duration (seconds)")
        .input("data", PortType.JSON, "Data to pass through", optional=True)
        .output("data", PortType.JSON, "Data passed through")
        .output("waited", PortType.BOOLEAN, "Always true")
        .build()
    )


# ============================================================================
# Conditional Branching Nodes
# ============================================================================


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


# ============================================================================
# Pre-built Gate Pairs for Common Patterns
# ============================================================================


def build_approval_gate_pair() -> tuple[Node, Node, Node]:
    """Build an approval gate pair (gate + success/failure nodes).

    Returns:
        (approval_gate_node, success_node, failure_node)

    Usage:
        gate, success, fail = build_approval_gate_pair()
        pipeline.add_node(gate)
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
    """
    summary = text_summary(name="Summarize", node_id="summarize")
    gate = approval_gate(name="Review", node_id="review")
    return summary, gate
