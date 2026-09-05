"""One ticket, N repos: the shape every ticket-driven pipeline needs.

Three constructs that did not exist a day ago, doing the thing they exist for:

* ``map_over`` fans out over a list whose length comes out of the first step,
  which is the only honest way to model "this ticket touches some repos".
* ``outcome_scope`` gives the locate step a vocabulary — found, ambiguous,
  missing — so "we could not find the repo" is a value the pipeline routes on
  rather than an exception that kills it.
* ``converge`` bounds the work-and-review loop and reports *giving up* as an
  outcome, carrying the near-miss out with it.

Run it with::

    ictus emit demo_work/pipelines --out demo_work/build
    ictus run demo_work/build/fan-out.yaml --input brief="..."
"""

from __future__ import annotations

from ictus import Pipeline, PortType, outcome_scope
from ictus.graph.mapping import Item
from ictus.graph.node import AgentNode
from ictus.graph.ports import InputPort, OutputPort
from ictus.graph.ref import equals, tpl
from ictus.stdlib import succeed

STR, ARR, OBJ, NUM = PortType.STRING, PortType.ARRAY, PortType.OBJECT, PortType.NUMBER

PIECE = {"repo": STR, "task": STR, "acceptance": STR}

# --- the scope: locating a repo can fail, and failing is a value -------------
locate = outcome_scope(
    stage_id="locate-repos",
    outcomes=("found", "ambiguous", "missing"),
    carry={
        # The element shape travels with the port: a scope that hands an array
        # to a fan-out has to say what is in it, or the fan-out reads keys the
        # producing model was never told to emit.
        "pieces": OutputPort("pieces", ARR, "One entry per repository", element=PIECE),
        "candidates": OutputPort("candidates", ARR, "Repositories weighed but not chosen"),
    },
    description="Work out which repositories a ticket touches",
)
_ticket = locate.body.declare_input("ticket", STR, description="The ticket body")
_search = locate.body.add(
    AgentNode(
        node_id="search",
        description="Identify the repositories involved",
        inputs=(InputPort("ticket", STR),),
        prompt=tpl(
            "Read this ticket and split it into one piece of work per repository "
            "it touches. If exactly one repository is implied, that is one piece. "
            "If it is unclear which repositories are meant, set verdict to "
            "'ambiguous' and list what you considered. If none can be identified, "
            "set verdict to 'missing'.\n\n",
            _ticket.ref(),
        ),
        declared_outputs=(
            OutputPort("pieces", ARR, "One entry per repository", element=PIECE),
            OutputPort("considered", ARR, "Repositories weighed but not chosen"),
            OutputPort("verdict", STR, "found, ambiguous or missing"),
        ),
    )
)
locate.body.set_entry(_search)
locate.body.connect_input(_ticket, _search, "ticket")

_found = locate.exit(
    node_id="found",
    outcome="found",
    reason="Identified the repositories",
    pieces=_search.ref("pieces"),
)
_ambiguous = locate.exit(
    node_id="ambiguous",
    outcome="ambiguous",
    reason="More than one reading of the ticket",
    candidates=_search.ref("considered"),
)
_missing = locate.exit(
    node_id="missing",
    outcome="missing",
    reason="No repository could be identified",
)
locate.body.route(_search, _ambiguous, when=equals(_search.ref("verdict"), "ambiguous"))
locate.body.route(_search, _missing, when=equals(_search.ref("verdict"), "missing"))
locate.body.route(_search, _found)

# --- the pipeline -----------------------------------------------------------
fan_out = Pipeline(
    pipeline_id="fan-out",
    description="Split a ticket across repositories and work each one",
)
brief = fan_out.declare_input("brief", STR, description="The ticket to act on")

repos = locate.instantiate(fan_out, node_id="locate")
fan_out.set_entry(repos)
fan_out.connect_input(brief, repos, "ticket")

piece = Item(name="piece", fields=PIECE)
worker = fan_out.add(
    AgentNode(
        node_id="worker",
        description="Plan one repository's share of the ticket",
        prompt=tpl(
            "Repository: ",
            piece.ref("repo"),
            "\nWork: ",
            piece.ref("task"),
            "\nDone when: ",
            piece.ref("acceptance"),
            "\n\nWrite the plan for this repository alone. Do not touch the others.",
        ),
        declared_outputs=(
            OutputPort("plan", STR, "What to do in this repository"),
            OutputPort("risk", STR, "What could go wrong"),
        ),
    )
)
# expect_items is not a guess to be tuned away: Conductor charges one iteration per
# item, the array comes out of `locate`, and nothing else bounds its length.
workers = fan_out.map_over(
    "workers",
    source=repos.ref("pieces"),
    item=piece,
    body=worker,
    expect_items=8,
    max_concurrent=4,
    key_by="repo",
    description="One planner per repository",
)

shipped = fan_out.add(
    succeed(
        node_id="planned",
        reason="Every repository has a plan",
        inputs=(InputPort("plans", OBJ),),
        result={"plans": tpl(workers.ref("outputs")), "repos": tpl(workers.ref("count"))},
    )
)
unclear = fan_out.add(
    succeed(
        node_id="unclear",
        reason="A person has to say which repositories this ticket means",
        inputs=(InputPort("candidates", ARR),),
        result={"candidates": tpl(repos.ref("candidates"))},
    )
)

fan_out.route(workers, shipped)
fan_out.feed(workers, "outputs", shipped, "plans")
fan_out.feed(repos, "candidates", unclear, "candidates")
fan_out.branch_on_outcome(repos, {"found": workers, "ambiguous": unclear, "missing": unclear})
fan_out.expose_output("plans", workers, "outputs")
