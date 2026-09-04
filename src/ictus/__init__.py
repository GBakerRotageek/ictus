"""Typed pipeline composition for multi-stage agent workflows.

Three-tier hierarchy:
- Nodes: Atomic units with typed inputs/outputs
- Stages: Reusable collections of nodes
- Pipelines: Large-scale flows of stages and nodes

Composition-time validation ensures type safety at every connection.

Standard library (stdlib) provides pre-built nodes for common patterns:
- Gate nodes: approval_gate(), multi_choice_gate()
- Summary nodes: text_summary(), status_report()
- Terminal nodes: success_node(), failure_node()
- Utilities: log_node(), merge_data(), transform_data(), etc.
"""

from __future__ import annotations

from ictus.core import (
    InputPort,
    Node,
    NodeBuilder,
    OutputPort,
    Pipeline,
    PipelineBuilder,
    PipelineElement,
    PortConnection,
    PortType,
    Stage,
    StageBuilder,
)
from ictus.stdlib import (
    approval_gate,
    build_approval_gate_pair,
    build_summary_approval_gate,
    failure_node,
    filter_array,
    if_then_node,
    log_node,
    merge_data,
    multi_choice_gate,
    status_report,
    success_node,
    switch_node,
    text_summary,
    transform_data,
    wait_node,
)

__all__ = [
    "InputPort",
    "Node",
    "NodeBuilder",
    "OutputPort",
    "Pipeline",
    "PipelineBuilder",
    "PipelineElement",
    "PortConnection",
    "PortType",
    "Stage",
    "StageBuilder",
    "approval_gate",
    "build_approval_gate_pair",
    "build_summary_approval_gate",
    "failure_node",
    "filter_array",
    "if_then_node",
    "log_node",
    "merge_data",
    "multi_choice_gate",
    "status_report",
    "success_node",
    "switch_node",
    "text_summary",
    "transform_data",
    "wait_node",
]
