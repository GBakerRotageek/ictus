"""Core type system for nodes, stages, and pipelines.

Defines the I/O port system with composition-time validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")

# Type alias for serialized data (heterogeneous dicts for YAML/JSON)
SerializedDict = dict[str, object]


class PortType(StrEnum):
    """Type of a port (input or output)."""

    STRING = "string"
    BOOLEAN = "boolean"
    NUMBER = "number"
    OBJECT = "object"
    ARRAY = "array"
    TEXT = "text"  # longer-form text/reports
    JSON = "json"


@dataclass(frozen=True)
class OutputPort:
    """Output port of a node - can be connected to other nodes' inputs."""

    name: str
    port_type: PortType
    description: str = ""

    def __hash__(self) -> int:
        return hash((self.name, self.port_type))

    def matches(self, input_port: InputPort) -> bool:
        """Check if this output can connect to an input port."""
        return self.port_type == input_port.port_type


@dataclass(frozen=True)
class InputPort:
    """Input port of a node - receives output from other nodes."""

    name: str
    port_type: PortType
    description: str = ""
    optional: bool = False

    def __hash__(self) -> int:
        return hash((self.name, self.port_type))

    def matches(self, output_port: OutputPort) -> bool:
        """Check if this input can receive from an output port."""
        return self.port_type == output_port.port_type


@dataclass(frozen=True)
class PortConnection:
    """A connection between an output port and an input port."""

    from_port: OutputPort
    to_port: InputPort

    def validate(self) -> bool:
        """Validate that ports are compatible."""
        if not self.from_port.matches(self.to_port):
            raise ValueError(
                f"Cannot connect {self.from_port.name} ({self.from_port.port_type}) "
                f"to {self.to_port.name} ({self.to_port.port_type})"
            )
        return True
