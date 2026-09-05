"""Several named values at once. Conductor ``type: set``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import ComputeNode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ictus.graph.ports import InputPort

__all__ = ["bindings"]


def bindings(
    *,
    node_id: str,
    values: Mapping[str, str],
    description: str = "",
    inputs: Sequence[InputPort] = (),
) -> ComputeNode:
    """Compute several named values in one step.

    Bindings inside one block cannot reference each other — they are evaluated
    against the surrounding context, not against each other — so chain two nodes
    when one value depends on another.
    """
    if not values:
        raise CompositionError(f"bindings {node_id!r} needs at least one value")
    return ComputeNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        values=dict(values),
    )
