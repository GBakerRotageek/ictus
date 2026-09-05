"""Work out what is missing, then ask a person only for what is actually missing.

The shape a real ticket needs. Something reads the work and reports what it
could not determine — which repositories a change spans, which environment is
meant, which of three services owns a table. If it determined everything, the
run continues untouched. If it did not, the person is asked precisely those
questions and nothing else.

Asking unconditionally is the easy version and the wrong one: a pipeline that
stops to ask about things it already knows gets skipped past, and then the one
time it mattered nobody read it either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import AgentNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import tpl
from ictus.graph.stage import Stage
from ictus.stdlib.gates.ask import ask_human_for
from ictus.stdlib.terminals.succeed import succeed

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["resolve_unknowns"]

STR, OBJ, ARR = PortType.STRING, PortType.OBJECT, PortType.ARRAY


def resolve_unknowns(
    *,
    stage_id: str = "resolve-unknowns",
    subject: str,
    needs: Sequence[str],
    description: str = "",
) -> Stage:
    """Determine what is unknown, ask a person for it, and carry the answers out.

    ``needs`` names the things the work requires — "the filesystem path of every
    repository this ticket touches", "the target environment". The identifying
    step decides which of them it can already answer and writes questions for
    the rest, so the number of questions follows the ticket rather than being
    fixed when the pipeline was written.

    Contract: input ``brief`` (string) in; ``known`` (object) and ``answers``
    (object) out. A caller merges them, or reads whichever it needs.
    """
    if not needs:
        raise ValueError("resolve_unknowns needs at least one thing to look for")

    stage = Stage(stage_id=stage_id, description=description or f"Resolve unknowns for {subject}")
    body = stage.body
    brief = body.declare_input("brief", STR, description=f"The {subject} to examine")

    wanted = "\n".join(f"- {item}" for item in needs)
    identify = body.add(
        AgentNode(
            node_id="identify",
            description=f"Work out what is missing from the {subject}",
            inputs=(InputPort("brief", STR),),
            prompt=tpl(
                f"Examine the {subject} below. The work needs each of these:\n{wanted}\n\n",
                brief.ref(),
                "\n\nFor each item, decide whether you can determine it from what you have. "
                "Put what you determined in `known`, keyed by item.\n\n"
                "For anything you cannot determine, write a question for the person running "
                "this. Ask for one specific value per question, say what it is for, and give "
                "an example of the form you expect. Where you can guess plausible answers, "
                "offer them as choices — picking is far less work than typing.\n\n"
                "Do not invent values. An unanswered question is recoverable; a fabricated "
                "path is not.",
            ),
            declared_outputs=(
                OutputPort("known", OBJ, "What could be determined without asking"),
                OutputPort("missing", ARR, "Questions for the person, one per unknown"),
                OutputPort("all_known", PortType.BOOLEAN, "Whether nothing needs asking"),
            ),
        )
    )

    ask = body.add(
        ask_human_for(
            node_id="ask",
            description=f"Ask for what the {subject} did not say",
            source=identify.ref("missing"),
        )
    )
    ready = body.add(succeed(node_id="ready", reason="Everything needed is known."))
    abandoned = body.add(
        succeed(
            node_id="abandoned",
            reason="Abandoned: the run needs values nobody supplied.",
        )
    )

    body.connect_input(brief, identify, "brief")
    # Ask only about what is actually unknown.
    body.route(identify, ready, when=tpl(identify.ref("all_known")))
    body.route(identify, ask)
    body.route(ask, ready)
    body.abort_route(ask, abandoned)

    body.expose_output("known", identify, "known")
    # Defaulted: on the path where nothing was missing, the questions never ran.
    body.expose_output("answers", ask, "answers", default="{}")
    return stage
