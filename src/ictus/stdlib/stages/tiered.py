"""Send the work to a tier chosen for it, and hand the caller one shape back.

Effort cannot be decided at run time. Conductor renders Jinja in the prompt, the
working directory, a terminal's reason, a wait's duration, route conditions and
output templates — and nowhere else. ``model``, ``max_turns``, ``tools`` and
``provider`` are read straight off the agent definition
(``providers/claude_agent_sdk.py:886``), so no value a step produced can reach
them. ``reasoning`` is worse: that provider declares
``capabilities.reasoning_effort=None`` and never wires the field through at all.

So the tiers are structure. A triage step picks one by name, the graph branches
to a node that was declared with that model and that turn budget, and the choice
is a thing the lint can see, ``ictus trace`` can report and a cost estimate can
bound — none of which is true of a model string a model invented.

The reason this is a scope rather than a branch the caller writes: after a
branch, reading ``thorough.output.result`` when ``quick`` ran renders *empty*
rather than failing, because ictus guards a reference to a step that did not run.
"The cheap tier ran" and "the expensive tier returned nothing" then look
identical downstream. Every tier here exits with the same carried keys, so the
caller reads one place and the branch is over by the time it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import AgentNode, check_route_name
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import Ref, Template, equals, tpl
from ictus.graph.scope import outcome_scope
from ictus.stdlib.stages.classify import UNCLEAR

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.scope import Scope

__all__ = ["DONE", "UNCLEAR", "Tier", "tiered"]

DONE = "done"
"""Every tier reports this. Which one ran is carried, not branched on."""

# The same constant `classify` uses, not a second spelling of it. Two scopes
# with an outside-the-vocabulary exit mean the same thing by it, and a flat
# `ictus.stdlib` namespace would otherwise export whichever module imported
# last — silently, and only until one of them was edited.

TIER = "tier"
RATIONALE = "rationale"
TRIAGE = "triage"


@dataclass(frozen=True, slots=True)
class Tier:
    """One level of effort, and the work that belongs at it.

    ``meaning`` goes to the triage step — it is how the classifier knows which
    work lands here, and a list of bare names is a router that guesses.
    ``prompt`` goes to the tier itself.

    ``tools`` defaults to denied, as ``voice()`` does. Give a tier tools and you
    must give it ``max_turns``: the engine allows fifty tool-use rounds and then
    raises rather than returning what the step had, and that error is not one a
    scope can turn into an outcome — it destroys the run after every earlier
    step has been paid for.
    """

    name: str
    meaning: str
    prompt: str
    model: str | None = None
    provider: str | None = None
    tools: tuple[str, ...] | None = ()
    max_turns: int | None = None
    description: str = ""

    def __post_init__(self) -> None:
        # The name is the node the branch routes to, so it has to be routable.
        check_route_name(self.name, what="tier")
        if not self.meaning:
            raise CompositionError(
                f"tier {self.name!r} needs a meaning; it is what tells triage when this "
                "tier is the right one"
            )
        if not self.prompt:
            raise CompositionError(f"tier {self.name!r} needs a prompt")
        if self.tools != () and self.max_turns is None:
            raise CompositionError(
                f"tier {self.name!r} has tools but no max_turns. Fifty tool-use rounds is "
                "the engine's default and reaching it raises rather than returning what the "
                "step had — an error no scope can report as an outcome."
            )


def tiered(
    *,
    stage_id: str,
    question: str,
    tiers: Sequence[Tier],
    produces: Sequence[OutputPort],
    triage_model: str | None = None,
    triage_provider: str | None = None,
    description: str = "",
    brief: str = "What to work on",
) -> Scope:
    """Classify the work, then do it at the tier that classification picked.

    Every tier declares the same ``produces``, which is what makes the carry
    uniform: the caller reads ``result`` without knowing or caring who wrote it,
    and reads ``tier`` when it does care.

    Outcomes are ``done`` and ``unclear`` — two, not one per tier, because the
    caller almost never wants to branch on which tier ran. ``unclear`` is where
    triage lands when it answers outside the vocabulary; it carries the
    rationale and empty values for everything a tier would have produced, so a
    caller can retry, escalate to a person, or fail on its own terms rather than
    on a misclassification nobody saw.

    Contract: input ``brief`` (string, prose) in; outcomes ``done`` and
    ``unclear``, carrying ``tier``, ``rationale`` and every port in ``produces``.
    """
    if len(tiers) < 2:
        raise CompositionError(
            f"tiered {stage_id!r} needs at least two tiers; with one there is nothing to "
            "choose and the work should be a plain agent"
        )
    names = [tier.name for tier in tiers]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise CompositionError(f"tiered {stage_id!r} repeats the tier(s) {duplicated}")
    clashing = sorted({DONE, UNCLEAR, TRIAGE} & set(names))
    if clashing:
        raise CompositionError(
            f"tiered {stage_id!r} names tier(s) {clashing}, which the scope already uses "
            "for its own outcomes and its triage step"
        )
    if not produces:
        raise CompositionError(
            f"tiered {stage_id!r} declares no produces. Every tier has to emit the same "
            "ports — that shared shape is what lets the caller read one place instead of "
            "asking which tier ran."
        )
    reserved = sorted({port.name for port in produces} & {TIER, RATIONALE})
    if reserved:
        raise CompositionError(
            f"tiered {stage_id!r} declares {reserved} in produces, which the scope already "
            "carries to say which tier ran and why"
        )

    carry: dict[str, PortType | OutputPort] = {TIER: PortType.STRING, RATIONALE: PortType.STRING}
    carry.update({port.name: port for port in produces})

    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=(DONE, UNCLEAR),
        carry=carry,
        description=description or f"Route by effort: {question}",
    )
    body = scope.body
    work = body.declare_input("brief", PortType.STRING, prose=True, description=brief)

    triage = body.add(
        AgentNode(
            node_id=TRIAGE,
            description=question,
            inputs=(InputPort("brief", PortType.STRING),),
            prompt=tpl(
                f"{question}\n\n",
                "Name the tier that should do this work, copied verbatim:\n\n",
                "".join(f"- {t.name} — {t.meaning}\n" for t in tiers),
                f"\nIf none of them fits, answer {UNCLEAR!r} and say why. Routing work to a "
                "tier that cannot do it costs more than saying so.\n\n--- the work ---\n",
                work.ref(),
            ),
            declared_outputs=(
                OutputPort(TIER, PortType.STRING, "Which tier should do this"),
                OutputPort(RATIONALE, PortType.STRING, "Why that tier"),
            ),
            model=triage_model,
            provider=triage_provider,
            # Triage reads the brief it was handed and nothing else. A router
            # that opens files has become the work it was meant to route.
            tools=(),
        )
    )
    body.set_entry(triage)
    body.connect_input(work, triage, "brief")

    for tier in tiers:
        node = body.add(
            AgentNode(
                node_id=tier.name,
                description=tier.description or f"Do the work at the {tier.name} tier",
                inputs=(InputPort("brief", PortType.STRING),),
                prompt=tpl(tier.prompt, "\n\n--- the work ---\n", work.ref()),
                declared_outputs=tuple(produces),
                model=tier.model,
                provider=tier.provider,
                tools=tier.tools,
                max_turns=tier.max_turns,
            )
        )
        body.connect_input(work, node, "brief")
        body.route(triage, node, when=equals(triage.ref(TIER), tier.name))
        # One exit per tier, all reporting `done`. A single shared exit would
        # have to read every tier's ports, and the ones that did not run render
        # empty rather than failing — which is the ambiguity this scope exists
        # to remove.
        carried: dict[str, Ref | Template | str] = {
            TIER: tier.name,
            RATIONALE: triage.ref(RATIONALE),
            **{port.name: node.ref(port.name) for port in produces},
        }
        body.route(
            node,
            scope.exit(
                node_id=f"{DONE}_{tier.name}",
                outcome=DONE,
                reason=f"Done at the {tier.name} tier",
                **carried,
            ),
        )

    # Last and unconditional: every tier name has been tested above, so what
    # arrives here is triage naming something that does not exist.
    body.route(
        triage,
        scope.exit(
            node_id=UNCLEAR,
            outcome=UNCLEAR,
            reason=tpl("Triage named no usable tier: ", triage.ref(TIER)),
            **{RATIONALE: triage.ref(RATIONALE)},
        ),
    )
    return scope
