"""Core pipeline composition and emission module.

Public API for building typed, composable pipelines with nodes, stages, and workflows.
"""

from __future__ import annotations

from ictus.node import Node, NodeBuilder
from ictus.pipeline import Pipeline, PipelineBuilder, PipelineElement
from ictus.stage import Stage, StageBuilder
from ictus.types import InputPort, OutputPort, PortConnection, PortType

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
