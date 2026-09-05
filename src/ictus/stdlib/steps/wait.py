"""A timed pause. Conductor ``type: wait``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import WaitNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort

__all__ = ["wait"]


def wait(
    *,
    node_id: str,
    seconds: float,
    reason: str | None = None,
    description: str = "",
    inputs: Sequence[InputPort] = (),
) -> WaitNode:
    """Pause the run without a model call.

    The canonical poll loop is ``check -> wait -> check``. Each wait costs one
    iteration, so a loop of N polls needs a budget of roughly 2N — which is why
    ``Pipeline`` derives ``max_iterations`` from the graph rather than trusting
    Conductor's default of 10.

    ``reason`` is a dashboard label here, not the required message that
    ``terminate`` takes.
    """
    return WaitNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        duration=seconds,
        reason=reason,
    )
