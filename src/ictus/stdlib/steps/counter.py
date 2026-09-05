"""A pass counter — the only thing that makes a loop's give-up routable.

Conductor has no route for exhaustion. ``_run_child_engine`` catches an explicit
termination and nothing else, so a ``MaxIterationsError`` from inside a loop
escapes past every route its caller declared. A loop that wants to *report*
having run out has to count its own passes and take an exit before the engine's
budget does, and this is the step that counts.

It costs one iteration and no provider call. Three details are engine behaviour
rather than style, each verified on a live run:

* The guard is on the node, not the value. On the first pass the whole step is
  absent from context, so ``pass_number.output | default(0)`` raises on the
  attribute access before the filter is ever reached.
* A single ``value:`` is stored as the bare scalar, so it is addressed as
  ``n.output``. Reading ``n.output.value`` gives "'int object' has no attribute
  'value'".
* A single ``value:`` step must declare no ``output:`` schema. Conductor rejects
  one *after* the step has already run.

The last two are handled by ``ComputeNode`` itself; the first is here.
"""

from __future__ import annotations

from ictus.graph.node import ComputeNode
from ictus.graph.ports import InputPort, OutputPort, PortType

__all__ = ["COUNT", "counter"]

COUNT = "value"


def counter(*, node_id: str, description: str = "") -> ComputeNode:
    """Count how many times the run has reached this point, starting at one.

    Read it with ``node.ref("value")``, and test it with ``at_least``. Wire it
    to itself with ``feed(node, "value", node, node_id)`` — the self-dependency
    is what puts the previous value in scope under ``context.mode: explicit``.
    """
    return ComputeNode(
        node_id=node_id,
        description=description or "Which pass this is",
        value=(
            "{% if " + node_id + " is defined %}"
            "{{ (" + node_id + ".output | int) + 1 }}"
            "{% else %}1{% endif %}"
        ),
        value_type=PortType.NUMBER,
        inputs=(InputPort(node_id, PortType.NUMBER, "The previous pass", optional=True),),
        declared_outputs=(OutputPort(COUNT, PortType.NUMBER, "Pass number"),),
    )
