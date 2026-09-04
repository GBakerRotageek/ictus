"""Stage definitions - reusable collections of nodes.

Stages are compositions of nodes that can be nested and reused.
They expose their own inputs/outputs at the stage level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ictus.node import Node  # noqa: TC001
from ictus.types import InputPort, OutputPort, SerializedDict  # noqa: TC001


@dataclass
class Stage:
    """A stage is a reusable collection of nodes with exposed I/O."""

    name: str
    stage_id: str = field(default="")
    description: str = ""
    nodes: list[Node] = field(default_factory=list)
    edges: list[tuple[Node, Node]] = field(default_factory=list)
    inputs: dict[str, InputPort] = field(default_factory=dict)
    outputs: dict[str, OutputPort] = field(default_factory=dict)
    start_node: Node | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.stage_id:
            self.stage_id = self.name.lower().replace(" ", "_")

    def add_node(self, node: Node) -> Node:
        """Add a node to this stage."""
        self.nodes.append(node)
        if self.start_node is None:
            self.start_node = node
        return node

    def add_edge(self, from_node: Node, to_node: Node) -> None:
        """Connect two nodes in the stage."""
        self.edges.append((from_node, to_node))

    def expose_input(self, port: InputPort) -> InputPort:
        """Expose an input port at the stage level."""
        self.inputs[port.name] = port
        return port

    def expose_output(self, port: OutputPort) -> OutputPort:
        """Expose an output port at the stage level."""
        self.outputs[port.name] = port
        return port

    def to_dict(self) -> SerializedDict:
        """Serialize stage to dict for YAML emission as a collection."""
        node_dicts: list[SerializedDict] = []
        for node in self.nodes:
            node_dict = node.to_dict()
            # Find next node
            next_nodes = [to_node for from_node, to_node in self.edges if from_node == node]
            if next_nodes:
                node_dict["next_task_id"] = next_nodes[0].node_id
            node_dicts.append(node_dict)

        return {
            "name": self.name,
            "task_id": self.stage_id,
            "type": "agent",
            "description": self.description,
            "tasks": node_dicts,
        }


class StageBuilder:
    """Builder for creating stages with fluent API."""

    def __init__(self, name: str, description: str = "") -> None:
        self.stage = Stage(name=name, description=description)

    def with_id(self, stage_id: str) -> StageBuilder:
        """Set the stage ID."""
        self.stage.stage_id = stage_id
        return self

    def add_node(self, node: Node) -> StageBuilder:
        """Add a node to the stage."""
        self.stage.add_node(node)
        return self

    def connect(self, from_node: Node, to_node: Node) -> StageBuilder:
        """Connect two nodes."""
        self.stage.add_edge(from_node, to_node)
        return self

    def expose_input(self, port: InputPort) -> StageBuilder:
        """Expose an input port."""
        self.stage.expose_input(port)
        return self

    def expose_output(self, port: OutputPort) -> StageBuilder:
        """Expose an output port."""
        self.stage.expose_output(port)
        return self

    def build(self) -> Stage:
        """Build the stage."""
        return self.stage
