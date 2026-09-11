"""An N-way decision made by a model, with a vocabulary closed on both ends.

``verdict`` answers yes or no. Anything wider was a bare ``AgentNode`` plus a
hand-written route per answer, and that shape has a hole the lint cannot see:
the vocabulary lives in the prompt, the routes are written separately, and
nothing checks that they are the same list. A model that answers with a sixth
word lands on whichever route was written last — silently, as a decision
somebody made rather than an answer nobody could read.

A :class:`Scope` closes it. The choices become the outcome vocabulary, so
``Pipeline.branch_on_outcome`` refuses to leave one unrouted, and an answer that
is not in the list takes ``unclear`` — a real exit, carrying what the model
actually said. That distinction is the point: "I could not classify this" and "I
classified this as the last option in the list" are different facts, and only one
of them is worth acting on.

Conductor's output schema cannot help here. ``PortType`` is deliberately the five
wire types and nothing narrower, so there is no enum to emit and no provider-side
enforcement to lean on. The vocabulary is stated in the prompt and *checked* by
where an off-vocabulary answer ends up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import AgentNode, check_route_name
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import equals, tpl
from ictus.graph.scope import outcome_scope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.scope import Scope

__all__ = ["UNCLEAR", "Choice", "classify"]

#: Where an answer outside the vocabulary goes. Also what the prompt offers as a
#: legitimate answer, so a model with no good fit has somewhere honest to put it
#: instead of picking the nearest wrong one.
UNCLEAR = "unclear"

ANSWER = "answer"
RATIONALE = "rationale"
CHOICE_PORT = "choice"


@dataclass(frozen=True, slots=True)
class Choice:
    """One answer the model may give, and what it means.

    ``meaning`` is not documentation. It is the only thing telling the model
    what distinguishes this choice from its neighbours, and a vocabulary of bare
    words is a classifier that guesses.
    """

    value: str
    meaning: str

    def __post_init__(self) -> None:
        # The value is both an outcome and the id of the exit node that reports
        # it, so it has to be routable. Checked here rather than where the node
        # is built: `is_needs work` is not a name the caller ever wrote.
        check_route_name(self.value, what="choice")
        if not self.meaning:
            raise CompositionError(
                f"choice {self.value!r} needs a meaning; it is what tells the model "
                "when this answer is the right one"
            )


def classify(
    *,
    stage_id: str,
    question: str,
    choices: Sequence[Choice],
    node_id: str = "decide",
    model: str | None = None,
    provider: str | None = None,
    tools: tuple[str, ...] | None = (),
    max_turns: int | None = None,
    description: str = "",
    brief: str = "What to classify",
) -> Scope:
    """Ask a model to pick one of ``choices``; report which, as an outcome.

    Outcomes are every choice value plus ``unclear``, and the caller must route
    all of them — leave one out and ``branch_on_outcome`` refuses at
    composition rather than the run reaching a dead end.

    Both outcomes carry ``answer`` (what the model actually said, verbatim) and
    ``rationale``. On the ``unclear`` branch ``answer`` is the whole point: it
    is the difference between a model that hedged and a model that invented a
    category you should probably add.

    ``tools`` defaults to denied, as ``voice()`` does — a classifier reading the
    material it was handed should not be opening files. Give it tools and you
    must give it ``max_turns``: the engine's default of fifty is a kill, not a
    throttle, and it is not an error a scope can turn into an outcome.

    Contract: input ``material`` (string, prose) in; one outcome per choice,
    plus ``unclear``.
    """
    if len(choices) < 2:
        raise CompositionError(
            f"classify {stage_id!r} needs at least two choices; with one there is nothing "
            "to decide and the node should be a plain agent"
        )
    values = [choice.value for choice in choices]
    duplicated = sorted({v for v in values if values.count(v) > 1})
    if duplicated:
        raise CompositionError(f"classify {stage_id!r} repeats the choice(s) {duplicated}")
    if UNCLEAR in values:
        raise CompositionError(
            f"classify {stage_id!r} declares a choice named {UNCLEAR!r}, which is where an "
            "answer outside the vocabulary already goes; name the real category something else"
        )
    if tools != () and max_turns is None:
        raise CompositionError(
            f"classify {stage_id!r} gives {node_id!r} tools but no max_turns. The engine "
            "allows fifty tool-use rounds and then raises rather than returning what the step "
            "had, and that error is not one this scope can report as an outcome."
        )

    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=(*values, UNCLEAR),
        carry={ANSWER: PortType.STRING, RATIONALE: PortType.STRING},
        description=description or f"Decide: {question}",
    )
    body = scope.body
    material = body.declare_input("material", PortType.STRING, prose=True, description=brief)

    decide = body.add(
        AgentNode(
            node_id=node_id,
            description=question,
            inputs=(InputPort("material", PortType.STRING),),
            prompt=tpl(
                f"{question}\n\n",
                "Answer with exactly one of these values, copied verbatim:\n\n",
                "".join(f"- {c.value} — {c.meaning}\n" for c in choices),
                f"\nIf none of them is right, answer {UNCLEAR!r} and say why in the rationale. "
                "That is a better answer than the nearest wrong one, and it is routed "
                "separately.\n\n--- material ---\n",
                material.ref(),
            ),
            declared_outputs=(
                OutputPort(CHOICE_PORT, PortType.STRING, "One of the declared values"),
                OutputPort(RATIONALE, PortType.STRING, "Why that answer"),
            ),
            model=model,
            provider=provider,
            tools=tools,
            max_turns=max_turns,
        )
    )
    body.set_entry(decide)
    body.connect_input(material, decide, "material")

    carried = {ANSWER: decide.ref(CHOICE_PORT), RATIONALE: decide.ref(RATIONALE)}
    for choice in choices:
        taken = scope.exit(
            node_id=f"is_{choice.value}",
            outcome=choice.value,
            reason=f"Classified as {choice.value}",
            **carried,
        )
        body.route(decide, taken, when=equals(decide.ref(CHOICE_PORT), choice.value))

    # Last, and unconditional. Every value the vocabulary knows has already been
    # tested, so what reaches here is an answer nobody asked for — which is a
    # fact worth carrying rather than a branch worth guessing at.
    body.route(
        decide,
        scope.exit(
            node_id=UNCLEAR,
            outcome=UNCLEAR,
            reason=tpl("Answered outside the vocabulary: ", decide.ref(CHOICE_PORT)),
            **carried,
        ),
    )
    return scope
