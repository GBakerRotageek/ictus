"""Fan out over a list whose length is only known at run time.

``Pipeline.parallel`` needs its members written down, so it can only express a
fan-out whose width the author knew. The shape that actually recurs — "split
this ticket into sub-tickets, then work each one" — has a width that comes out
of an earlier step.

What a group produces is one value under its own name: ``outputs`` (a list, or a
mapping when ``key_by`` is set), ``errors`` keyed the same way, and ``count``.
Routes are evaluated once, after every item has finished, and read that
aggregate — an item cannot route.

The loop variable carries typed field references. A backend decides how those
references are rendered and which names and body kinds it supports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import NodeKind
from ictus.graph.ports import OutputPort, PortType
from ictus.graph.ref import Origin, Ref

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ictus.graph.node import Node
    from ictus.graph.pipeline import FailureMode

__all__ = ["COUNT_PORT", "ERRORS_PORT", "OUTPUTS_PORT", "Item", "MapGroup"]

OUTPUTS_PORT = "outputs"
ERRORS_PORT = "errors"
COUNT_PORT = "count"


def _result_fields(body: Node) -> dict[str, PortType] | None:
    """The fields one item's result has at its top level, as the body stores it.

    One entry per item, each the body's result — but the result is not always an
    object keyed by port name, and ``output_ref`` is where each kind says where a
    port really lives. A port at an empty path *is* the result: a single-value
    ``set`` step stores the bare value, so the group collects ``[1, 2, 3]`` and
    its items have no fields. Advertising ``{"value": ...}`` there let a map
    downstream compose, lint and validate, then fail on its first item with
    "'int object' has no attribute 'value'". A port at a nested path — a gate's
    free text under ``additional_input`` — is not a top-level field either, so
    it is not claimed.

    Without a shape nothing downstream can fan out over what was collected, which
    is why the fields that are real are worth deriving at all.
    """
    paths = {port.name: body.output_ref(port.name) for port in body.outputs}
    if "" in paths.values():
        return None
    fields = {port.name: port.port_type for port in body.outputs if paths[port.name] == port.name}
    return fields or None


@dataclass(frozen=True, eq=False)
class Item:
    """The loop variable, as something references can be taken from.

    Its fields are declared rather than discovered: the source array's element
    shape is not knowable from the array's own port type, and an undeclared
    field read inside the body is a template error on an item nobody looked at.

    Compared by identity, never by value. ``item`` is the name everyone reaches
    for, so two groups in one pipeline routinely have loop variables that are
    equal in every field and belong to different fan-outs — and a reference from
    the wrong one renders the right word against the wrong array.
    """

    name: str
    fields: Mapping[str, PortType] = field(default_factory=dict)

    def ref(self, field_name: str) -> Ref:
        """A reference to one field of the current item."""
        if field_name not in self.fields:
            known = ", ".join(sorted(self.fields)) or "(none)"
            raise CompositionError(
                f"loop item {self.name!r} has no field {field_name!r}; declared: {known}"
            )
        return Ref(
            source_id=self.name,
            port=field_name,
            port_type=self.fields[field_name],
            origin=Origin.LOOP_ITEM,
            source=self,
        )

    def whole(self) -> Ref:
        """A reference to the item itself, for a list of plain values."""
        return Ref(
            source_id=self.name,
            port="",
            port_type=PortType.OBJECT,
            origin=Origin.LOOP_ITEM,
            source=self,
        )


@dataclass(frozen=True, eq=False)
class MapGroup:
    """One body, run once per element of an array resolved at run time.

    ``node_id`` is deliberately the same attribute a node uses, so every routing
    path treats a map group, a parallel group and a node identically.
    """

    group_id: str
    source: Ref
    item: Item
    body: Node
    """The step run per item.

    ``expect_items`` is how many items the caller expects at most. It buys
    iteration budget; it does not cap anything. ``max_concurrent`` limits how
    many items run at once, not the source array's length, and the array comes
    out of an earlier step, so nothing in the graph bounds it.

    Named for what it does because the consequence of getting it wrong is
    expensive and one-directional: a longer array runs *every* item, paying for
    all of them, and the step after the group is the one that dies on the
    iteration budget. If the length is genuinely unbounded, cap it in the step
    that produces the array — tell the model the limit and declare it in the
    port description — rather than hoping this number holds.
    """
    expect_items: int
    description: str = ""
    max_concurrent: int = 10
    failure_mode: FailureMode | None = None
    key_by: str | None = None
    bind: Mapping[str, Ref] = field(default_factory=dict)
    """Which of a stage body's parameters each item supplies, and from where.

    Only a stage has parameters to bind. Any other body reads the item straight
    out of its own prompt, because it runs in the iteration's context; a child
    workflow does not — it is handed a fresh context built from these values, so
    without them every iteration receives the parent's own inputs.

    A parameter the item does not decide is wired with an ordinary ``feed`` or
    ``connect_input`` into the body, like any other stage placement. The two
    sources are disjoint by construction: binding one an edge already supplies
    is refused, because only one of them can reach the child.
    """

    def __post_init__(self) -> None:
        if self.source.origin is Origin.LOOP_ITEM:
            raise CompositionError(
                f"map group {self.group_id!r} maps over a field of loop item "
                f"{self.source.source_id!r}. A group is scheduled where no item exists — "
                "an item is injected only into the body of the group iterating it — so "
                "there is no array there to read."
            )
        if self.source.port_type is not PortType.ARRAY:
            raise CompositionError(
                f"map group {self.group_id!r} maps over "
                f"{self.source.source_id}.{self.source.port}, which is "
                f"{self.source.port_type.value}, not array"
            )
        if not self.item.name.isidentifier():
            raise CompositionError(f"loop variable {self.item.name!r} is not an identifier")
        element = self.source.element
        if self.item.fields and element is None:
            raise CompositionError(
                f"map group {self.group_id!r} reads fields off each item, but "
                f"{self.source.source_id}.{self.source.port} declares no element shape. "
                "If the items are objects, give the producing port an `element=` mapping "
                "to declare their fields. If they are plain values — what a map over a "
                "single-value set step collects — they have no fields: read each one "
                "whole with `Item.whole()`."
            )
        if element is not None:
            missing = sorted(set(self.item.fields) - set(element))
            if missing:
                known = ", ".join(sorted(element)) or "(none)"
                raise CompositionError(
                    f"map group {self.group_id!r} reads {missing} off each item, which "
                    f"{self.source.source_id}.{self.source.port} does not produce; "
                    f"its items have: {known}"
                )
            wrong = sorted(
                name
                for name, port_type in self.item.fields.items()
                if element[name] is not port_type
            )
            if wrong:
                raise CompositionError(
                    f"map group {self.group_id!r} reads {wrong} at a different type than "
                    f"{self.source.source_id}.{self.source.port} produces"
                )
        for name, ref in self.bind.items():
            if ref.origin is not Origin.LOOP_ITEM:
                raise CompositionError(
                    f"map group {self.group_id!r} binds {name!r} to "
                    f"{ref.source_id}.{ref.port}, which is not a field of an item. A value "
                    "that is the same for every iteration is wired with feed() or "
                    "connect_input() instead."
                )
            if ref.source is not None and ref.source is not self.item:
                raise CompositionError(
                    f"map group {self.group_id!r} binds {name!r} to a field of a different "
                    f"loop item that is also called {ref.source_id!r}. Items are compared by "
                    "identity, so pass the same Item this group iterates with."
                )
        if self.bind and self.body.kind is not NodeKind.SUB_GRAPH:
            raise CompositionError(
                f"map group {self.group_id!r} binds {sorted(self.bind)} on "
                f"{self.body.node_id!r}, but only a stage has parameters to bind — every "
                "other body reads the item from its own prompt. Reference the item there."
            )
        if self.expect_items < 1:
            raise CompositionError(
                f"map group {self.group_id!r} needs expect_items >= 1, got {self.expect_items}"
            )
        if self.max_concurrent < 1:
            raise CompositionError(
                f"map group {self.group_id!r} needs max_concurrent >= 1, got {self.max_concurrent}"
            )

    @property
    def node_id(self) -> str:
        """The identifier routing resolves against."""
        return self.group_id

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        """What a later step can read off the finished group."""
        collected = PortType.OBJECT if self.key_by else PortType.ARRAY
        element = None if self.key_by else _result_fields(self.body)
        return (
            OutputPort(OUTPUTS_PORT, collected, "Every item's result", element),
            OutputPort(ERRORS_PORT, PortType.OBJECT, "Items that failed, keyed"),
            OutputPort(COUNT_PORT, PortType.NUMBER, "How many items ran"),
        )

    def get_output(self, port: str) -> OutputPort:
        """One of the group's aggregate ports, by name."""
        for candidate in self.outputs:
            if candidate.name == port:
                return candidate
        known = ", ".join(p.name for p in self.outputs)
        raise CompositionError(
            f"map group {self.group_id!r} has no output {port!r}; it produces {known}"
        )

    def output_ref(self, port_name: str) -> str:
        """The aggregate field named by this output port."""
        return port_name

    def ref(self, port: str) -> Ref:
        """A reference to one of the group's aggregate outputs."""
        found = self.get_output(port)
        return Ref(
            source_id=self.group_id,
            port=port,
            port_type=found.port_type,
            origin=Origin.NODE,
            source=self,
            # Copied as `Node.ref` copies it. The port knowing the shape is not
            # enough: a consumer only ever sees the reference.
            element=found.element,
        )
