"""Utility nodes for data transformation and workflow control.

Common helpers for logging, data manipulation, and flow control.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.types import PortType


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
