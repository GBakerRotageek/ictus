"""A stage, an outcome the caller routes on, and a fan-out over its result.

    [split] -+- found ---> {worker x N} -> planned
             |
             +- missing --> nothing-to-do

The second fixture exists for the shapes the first has none of: a stage emits a
*second* YAML file, so this is what checks that `emit` writes both, that
`validate` loads the parent through its child, and that a stale `build/` is
caught when the child is the file that moved. The map group is here for the same
reason — `expect_items` reaches the parent's iteration bound, and nothing in a
flat pipeline would notice it going wrong.
"""

from __future__ import annotations

from ictus import (
    AgentNode,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    equals,
    outcome_scope,
    tpl,
)
from ictus.graph.mapping import Item
from ictus.stdlib import succeed

STRING, ARRAY, OBJECT = PortType.STRING, PortType.ARRAY, PortType.OBJECT

# The element shape travels with the port. A scope handing an array to a
# fan-out has to say what is in it, or the fan-out reads keys the producing
# model was never asked for.
PIECE = {"area": STRING, "task": STRING}

split = outcome_scope(
    stage_id="split-work",
    outcomes=("found", "missing"),
    carry={"pieces": OutputPort("pieces", ARRAY, "One entry per area", element=PIECE)},
    description="Split a brief into one piece of work per area it touches",
)
_brief = split.body.declare_input("brief", STRING, description="The brief to split")
_reader = split.body.add(
    AgentNode(
        node_id="reader",
        description="Identify the areas the brief touches",
        inputs=(InputPort("brief", STRING),),
        prompt=tpl(
            "Split this brief into one piece of work per area it touches. "
            "If no area can be identified, set verdict to 'missing'.\n\n",
            _brief.ref(),
        ),
        declared_outputs=(
            OutputPort("pieces", ARRAY, "One entry per area", element=PIECE),
            OutputPort("verdict", STRING, "found or missing"),
        ),
    )
)
split.body.set_entry(_reader)
split.body.connect_input(_brief, _reader, "brief")

_found = split.exit(
    node_id="found",
    outcome="found",
    reason="Identified the areas",
    pieces=_reader.ref("pieces"),
)
_missing = split.exit(
    node_id="missing",
    outcome="missing",
    reason="No area could be identified",
)
split.body.route(_reader, _missing, when=equals(_reader.ref("verdict"), "missing"))
split.body.route(_reader, _found)

staged_fanout = Pipeline(
    pipeline_id="staged-fanout",
    description="Split a brief across areas and plan each one.",
    metadata={"generator": "ictus", "pipeline": "staged-fanout"},
)

brief = staged_fanout.declare_input("brief", STRING, description="The brief to act on")
pieces = split.instantiate(staged_fanout, node_id="split")
staged_fanout.set_entry(pieces)
staged_fanout.connect_input(brief, pieces, "brief")

piece = Item(name="piece", fields=PIECE)
worker = staged_fanout.add(
    AgentNode(
        node_id="worker",
        description="Plan one area's share of the brief",
        prompt=tpl(
            "Area: ",
            piece.ref("area"),
            "\nWork: ",
            piece.ref("task"),
            "\n\nWrite the plan for this area alone.",
        ),
        declared_outputs=(OutputPort("plan", STRING, "What to do in this area"),),
    )
)
# expect_items is not a number to tune away: Conductor charges the parent one
# iteration per item and the array's length is only known at run time.
workers = staged_fanout.map_over(
    "workers",
    source=pieces.ref("pieces"),
    item=piece,
    body=worker,
    expect_items=4,
    max_concurrent=2,
    key_by="area",
    description="One planner per area",
)

planned = staged_fanout.add(
    succeed(
        node_id="planned",
        reason="Every area has a plan.",
        inputs=(InputPort("plans", OBJECT),),
        result={"plans": tpl(workers.ref("outputs"))},
    )
)
nothing = staged_fanout.add(
    succeed(node_id="nothing_to_do", reason="The brief named no area to work on.")
)

staged_fanout.route(workers, planned)
staged_fanout.feed(workers, "outputs", planned, "plans")
staged_fanout.branch_on_outcome(pieces, {"found": workers, "missing": nothing})
staged_fanout.expose_output("plans", workers, "outputs")
