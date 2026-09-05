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
from ictus.graph.ref import (
    Ref,
    Template,
    at_least,
    equals,
    every,
    not_equals,
    not_every,
    optional,
    ref_to,
    tpl,
)
from ictus.graph.requirements import EnvVar, McpServer, McpTransport
from ictus.graph.scope import Scope, ScopeNode, outcome_scope
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
    "Scope",
    "ScopeNode",
    "ScriptNode",
    "Stage",
    "SubGraphNode",
    "Template",
    "TerminateNode",
    "UnknownPortError",
    "WaitNode",
    "WorkflowInput",
    "at_least",
    "equals",
    "every",
    "not_equals",
    "not_every",
    "optional",
    "outcome_scope",
    "ref_to",
    "slugify",
    "tpl",
]
