"""Ports — the typed boundary of a node.

``PortType`` is deliberately the exact set of types Conductor accepts on the
wire (``OutputField.type`` / ``InputDef.type``). Carrying refinements Conductor
cannot represent — a distinct TEXT or JSON — makes port equality mean something
at composition time that is erased at emission, which both rejects compatible
pairs and accepts incompatible ones. One type set, one meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PortType(StrEnum):
    """A value type on a node boundary.

    These are Conductor's five wire types; the enum value is what is emitted.
    """

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


@dataclass(frozen=True, slots=True)
class OutputPort:
    """A named, typed value a node produces."""

    name: str
    port_type: PortType
    description: str = ""

    def accepts(self, other: InputPort) -> bool:
        """Whether this output can drive ``other``."""
        return self.port_type is other.port_type


@dataclass(frozen=True, slots=True)
class InputPort:
    """A named, typed value a node consumes.

    ``optional`` is load-bearing: it becomes the ``?`` suffix on the emitted
    ``input:`` reference, which is what lets a loop back-edge resolve on the
    first pass before the upstream node has ever run.
    """

    name: str
    port_type: PortType
    description: str = ""
    optional: bool = False


@dataclass(frozen=True, slots=True)
class PortConnection:
    """A validated output-to-input pairing."""

    source: OutputPort
    target: InputPort
