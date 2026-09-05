"""Typed composition for Conductor workflows."""

from __future__ import annotations

from ictus.errors import CompositionError, EmitError, LintError, PortTypeError, UnknownPortError
from ictus.graph.node import (
    AgentNode,
    ComputeNode,
    GateChoice,
    GateNode,
    Node,
    Question,
    QuestionsNode,
    ScriptNode,
    SubGraphNode,
    TerminateNode,
    WaitNode,
    slugify,
)
from ictus.graph.pipeline import END, Edge, Pipeline, WorkflowInput
from ictus.graph.ports import InputPort, OutputPort, PortConnection, PortType
from ictus.graph.ref import Ref, Template, equals, not_equals, optional, ref_to, tpl
from ictus.graph.requirements import EnvVar, McpServer, McpTransport
from ictus.graph.stage import Stage

__all__ = [
    "END",
    "AgentNode",
    "CompositionError",
    "ComputeNode",
    "Edge",
    "EmitError",
    "EnvVar",
    "GateChoice",
    "GateNode",
    "InputPort",
    "LintError",
    "McpServer",
    "McpTransport",
    "Node",
    "OutputPort",
    "Pipeline",
    "PortConnection",
    "PortType",
    "PortTypeError",
    "Question",
    "QuestionsNode",
    "Ref",
    "ScriptNode",
    "Stage",
    "SubGraphNode",
    "Template",
    "TerminateNode",
    "UnknownPortError",
    "WaitNode",
    "WorkflowInput",
    "equals",
    "not_equals",
    "optional",
    "ref_to",
    "slugify",
    "tpl",
]
