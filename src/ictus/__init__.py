"""Typed pipeline composition for multi-stage agent workflows.

Three-tier hierarchy:
- Nodes: Atomic units with typed inputs/outputs
- Stages: Reusable collections of nodes
- Pipelines: Large-scale flows of stages and nodes

Composition-time validation ensures type safety at every connection.
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
]
