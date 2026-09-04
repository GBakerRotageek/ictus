"""Node definitions - the atomic units of work with typed I/O.

Nodes are simple, reusable units with defined inputs and outputs.
Composition-time validation ensures type safety when connecting nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ictus.types import InputPort, OutputPort, PortType, SerializedDict


@dataclass
class Node:
    """A node is an atomic unit of work with typed inputs and outputs."""

    name: str
    node_id: str = field(default="")
    description: str = ""
    inputs: dict[str, InputPort] = field(default_factory=dict)
    outputs: dict[str, OutputPort] = field(default_factory=dict)
    config: SerializedDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            self.node_id = self.name.lower().replace(" ", "_")

    def add_input(self, port: InputPort) -> InputPort:
        """Add an input port to this node."""
        self.inputs[port.name] = port
        return port

    def add_output(self, port: OutputPort) -> OutputPort:
        """Add an output port to this node."""
        self.outputs[port.name] = port
        return port

    def get_input(self, name: str) -> InputPort:
        """Get an input port by name."""
        if name not in self.inputs:
            raise KeyError(f"Input port '{name}' not found on node '{self.name}'")
        return self.inputs[name]

    def get_output(self, name: str) -> OutputPort:
        """Get an output port by name."""
        if name not in self.outputs:
            raise KeyError(f"Output port '{name}' not found on node '{self.name}'")
        return self.outputs[name]

    def to_dict(self) -> SerializedDict:
        """Serialize node to dict for YAML emission."""
        return {
            "name": self.name,
            "task_id": self.node_id,
            "type": "agent",
            "config": self.config,
        }


class NodeBuilder:
    """Builder for creating nodes with fluent API."""

    def __init__(self, name: str, description: str = "") -> None:
        self.node = Node(name=name, description=description)

    def with_id(self, node_id: str) -> NodeBuilder:
        """Set the node ID."""
        self.node.node_id = node_id
        return self

    def with_config(self, **config: Any) -> NodeBuilder:  # type: ignore[explicit-any]
        """Set node configuration."""
        self.node.config = config
        return self

    def input(
        self,
        name: str,
        port_type: PortType,
        description: str = "",
        optional: bool = False,
    ) -> NodeBuilder:
        """Add an input port."""
        port = InputPort(name, port_type, description, optional)
        self.node.add_input(port)
        return self

    def output(self, name: str, port_type: PortType, description: str = "") -> NodeBuilder:
        """Add an output port."""
        port = OutputPort(name, port_type, description)
        self.node.add_output(port)
        return self

    def build(self) -> Node:
        """Build the node."""
        return self.node
