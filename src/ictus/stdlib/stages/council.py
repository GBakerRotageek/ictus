"""A council — several standpoints, deliberating until they agree or run out.

The shape this exists for: assessing something where the interesting failures
are the ones a single reviewer would not have been looking for. One agent asked
to "review this thoroughly" produces one perspective wearing several hats, and
the hats agree with each other. Several voices, each given a different thing to
care about and each seeing the others' conclusions, produce actual disagreement
— which is the only thing that surfaces a tradeoff nobody had decided.

Each round: every voice assesses at once, a synthesis step writes one report,
and the round ends. If every voice is satisfied the council exits ``agreed``;
if the round budget is spent it exits ``unresolved``, carrying the report and
what was still contested. Both are outcomes, so the caller decides what an
unresolved council means — that decision is not the council's to make.

With ``interject`` on, a person reads each round's report and may steer or stop
it. The steer is a constraint on the next round rather than another vote,
because the alternative — a human opinion the voices are free to outvote — is a
worse version of both.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import AgentNode, ComputeNode, GateChoice, GateNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import at_least, every, ref_to, tpl
from ictus.graph.scope import Scope, outcome_scope
from ictus.stdlib.agents.voice import SATISFIED, voice
from ictus.stdlib.steps.counter import counter as counter_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.node import Node
    from ictus.graph.ref import TemplatePart

__all__ = ["AGREED", "HALTED", "UNRESOLVED", "Voice", "council"]

AGREED = "agreed"
UNRESOLVED = "unresolved"
HALTED = "halted"

ROUND = "round_number"
REPORT = "report"
PANEL = "voices"
INTERJECT = "interject"
TALLY = "tallied"
DIRECTION = "direction"

STR, NUM = PortType.STRING, PortType.NUMBER


@dataclass(frozen=True, slots=True)
class Voice:
    """One seat on a council: who is speaking and what they are watching for.

    A spec rather than a node because a voice reads things that do not exist
    until the council is built — the subject it assesses is the council body's
    own input, and the last round's report is a step further round the loop.
    """

    node_id: str
    persona: str
    focus: str
    description: str = ""
    tools: tuple[str, ...] | None = ()
    """What this voice may call. Empty denies tools; ``None`` takes the default."""


def council(
    *,
    stage_id: str,
    voices: Sequence[Voice],
    subject: str = "What the council is assessing",
    charge: str = "What this assessment is for",
    rounds: int = 3,
    interject: bool = False,
    synthesis: str = "",
    description: str = "",
) -> Scope:
    """Convene ``voices`` and deliberate until they agree or the rounds run out.

    Outcomes are ``agreed`` and ``unresolved``, plus ``halted`` when
    ``interject`` is on and a person stops it. All three carry the last report,
    what was still contested, and how many rounds it took, so a caller can act
    on a council that did not converge instead of only learning that it didn't.

    ``agreed`` means the voices converged on a *report* — not that they liked
    what they read. A council assessing something with a real defect in it
    should still agree, on a report saying so; if satisfaction meant "nothing
    left to fix" it could never be reached, and every council would exhaust its
    rounds and report failure over a discussion that had finished. The first
    round has no report to accept, so two rounds is the floor.

    Contract: inputs ``subject`` (required), ``charge`` and ``intent`` (both
    optional). ``charge`` is the standing instruction every voice receives —
    what this assessment is for. ``intent`` is what the material itself is meant
    to do, which is what lets a voice judge it against something rather than
    against taste.
    """
    if len(voices) < 2:
        raise CompositionError(
            f"council {stage_id!r} needs at least two voices; one voice is not a council, "
            "it is an assessment, and the loop around it would only ask it to agree "
            "with itself"
        )
    repeated = sorted(n for n, count in Counter(v.node_id for v in voices).items() if count > 1)
    if repeated:
        raise CompositionError(f"council {stage_id!r} has more than one voice named {repeated}")
    if rounds < 2:
        raise CompositionError(
            f"council {stage_id!r} needs rounds >= 2, got {rounds}. A voice is satisfied "
            "when the report captures its position, and there is no report to read on the "
            "first round — so a one-round council can only ever come back unresolved."
        )

    outcomes = (AGREED, UNRESOLVED, *((HALTED,) if interject else ()))
    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=outcomes,
        carry={
            REPORT: OutputPort(REPORT, STR, "The last round's synthesis"),
            "dissent": OutputPort("dissent", STR, "What was still contested"),
            "rounds": OutputPort("rounds", NUM, "How many rounds it took"),
        },
        description=description or f"Council of {len(voices)}: {subject}",
        loop_passes=rounds,
    )
    body = scope.body
    material = body.declare_input("subject", STR, description=subject)
    intent = body.declare_input(
        "intent", STR, required=False, description="What the material is meant to do"
    )
    # Shared framing, as an input rather than a constant: what a council is asked
    # to look at usually changes per run, while who sits on it does not.
    charge_in = body.declare_input("charge", STR, required=False, description=charge)

    counter = body.add(counter_step(node_id=ROUND, description="Which round this is"))
    body.set_entry(counter)
    body.feed(counter, "value", counter, ROUND)

    # Both are forward references into the loop: neither step has run when the
    # first round's voices are prompted. The compiler adds the first-pass guard.
    prior = ref_to(REPORT, "text", STR)
    steer = ref_to(INTERJECT, "notes", STR) if interject else None

    seats = [
        body.add(
            voice(
                node_id=spec.node_id,
                persona=spec.persona,
                focus=spec.focus,
                description=spec.description,
                tools=spec.tools,
                subject=material.ref(),
                charge=charge_in.ref(),
                intent=intent.ref(),
                prior=prior,
                direction=steer,
                inputs=(
                    InputPort("subject", STR),
                    InputPort("charge", STR, optional=True),
                    InputPort("intent", STR, optional=True),
                    InputPort(REPORT, STR, "The last round", optional=True),
                    *(
                        (InputPort(DIRECTION, STR, "Human direction", optional=True),)
                        if interject
                        else ()
                    ),
                ),
            )
        )
        for spec in voices
    ]
    panel = body.parallel(PANEL, seats, description=f"{len(seats)} voices, at once")
    for seat in seats:
        body.connect_input(material, seat, "subject")
        body.connect_input(charge_in, seat, "charge")
        body.connect_input(intent, seat, "intent")
    body.route(counter, panel)

    report = body.add(
        AgentNode(
            node_id=REPORT,
            description="Synthesise the round",
            inputs=(
                InputPort(ROUND, NUM, "Which round"),
                *(
                    port
                    for seat in seats
                    for port in (
                        InputPort(f"{seat.node_id}__position", STR),
                        InputPort(f"{seat.node_id}__concerns", STR),
                        InputPort(f"{seat.node_id}__ok", PortType.BOOLEAN),
                    )
                ),
            ),
            prompt=tpl(*_synthesis(synthesis, seats)),
            declared_outputs=(
                OutputPort("text", STR, "The round's report"),
                OutputPort("dissent", STR, "What is still contested, and by whom"),
            ),
        )
    )
    # `feed`, not `connect`: a group member carries no route of its own, and the
    # control edge into the report comes from the group. Conductor rejects a
    # member with `routes:` outright.
    #
    # `satisfied` is wired even though the report's prompt never reads it,
    # because a group's fields are projected into scope one at a time
    # (engine/context.py:99-101) and the agreement test is evaluated here.
    for seat in seats:
        body.feed(seat, "position", report, f"{seat.node_id}__position")
        body.feed(seat, "concerns", report, f"{seat.node_id}__concerns")
        body.feed(seat, SATISFIED, report, f"{seat.node_id}__ok")
    body.feed(counter, "value", report, ROUND)
    # The edge that makes the next round a deliberation rather than a re-poll.
    for seat in seats:
        body.feed(report, "text", seat, REPORT)
    body.route(panel, report)

    agreed = scope.exit(
        node_id="agreed",
        outcome=AGREED,
        reason="Every voice is satisfied",
        report=report.ref("text"),
        dissent=report.ref("dissent"),
        rounds=counter.ref("value"),
    )
    unresolved = scope.exit(
        node_id="unresolved",
        outcome=UNRESOLVED,
        reason=f"Still contested after {rounds} round(s)",
        report=report.ref("text"),
        dissent=report.ref("dissent"),
        rounds=counter.ref("value"),
    )

    settled = every(*(seat.ref(SATISFIED) for seat in seats))
    spent = at_least(counter.ref("value"), rounds)

    decides: Node = report
    if interject:
        gate = body.add(
            GateNode(
                node_id=INTERJECT,
                description="Read this round and steer it",
                inputs=(
                    InputPort("text", STR),
                    InputPort("dissent", STR),
                    InputPort(ROUND, NUM, "Which round"),
                ),
                prompt=tpl(
                    "Round ",
                    counter.ref("value"),
                    " of the council.\n\n",
                    report.ref("text"),
                    "\n\n--- still contested ---\n",
                    report.ref("dissent"),
                ),
                choices=(
                    GateChoice("continue", "Let them carry on"),
                    GateChoice(
                        "steer",
                        "Carry on — with direction",
                        prompt_for="notes",
                        multiline=True,
                    ),
                    GateChoice("stop", "Stop here and take this report"),
                ),
            )
        )
        body.connect(report, "text", gate, "text")
        body.feed(report, "dissent", gate, "dissent")
        body.feed(counter, "value", gate, ROUND)
        halted = scope.exit(
            node_id="halted",
            outcome=HALTED,
            reason="Stopped by the person overseeing the council",
            report=report.ref("text"),
            dissent=report.ref("dissent"),
            rounds=counter.ref("value"),
        )
        # A gate's branches are the human's buttons, so it cannot test whether
        # the voices agreed. The tally is one zero-cost step later, which is
        # somewhere both "carry on" options can route to and a condition can be
        # evaluated from.
        decides = body.add(
            ComputeNode(
                node_id=TALLY,
                description="Carry on; check where the council stands",
                value="continuing",
                value_type=STR,
                # Whatever a route condition reads has to be in scope where the
                # route is evaluated, which is here — the tally is where the
                # council decides, so it declares every verdict and the count.
                inputs=(
                    InputPort("choice", STR),
                    InputPort(ROUND, NUM, "Which round"),
                    *(InputPort(f"{seat.node_id}__ok", PortType.BOOLEAN) for seat in seats),
                ),
                declared_outputs=(OutputPort("value", STR, "Marker"),),
            )
        )
        body.feed(gate, "selected", decides, "choice")
        body.feed(counter, "value", decides, ROUND)
        for seat in seats:
            body.feed(seat, SATISFIED, decides, f"{seat.node_id}__ok")
        body.branch(gate, {"continue": decides, "steer": decides, "stop": halted})
        for seat in seats:
            body.feed(gate, "notes", seat, DIRECTION)

    body.route(decides, agreed, when=settled)
    body.route(decides, unresolved, when=spent)
    body.route(decides, counter)
    return scope


def _synthesis(extra: str, seats: Sequence[AgentNode]) -> list[TemplatePart]:
    """The report prompt: every voice's position, and what to do with them."""
    parts: list[TemplatePart] = [
        "Several voices have just assessed the same material, each watching for "
        "something different. Write one report of where this round got to.\n\n"
        "Do not average them. Where they disagree, say who disagrees with whom and "
        "about what — a disagreement recorded as a disagreement is useful, and one "
        "smoothed into consensus is a decision made by omission. Where a concern "
        "from one voice would be answered by another's suggestion, say so.\n\n"
        "Put anything still contested in `dissent`, naming the voices. Leave it "
        "empty only when nothing is.\n",
    ]
    if extra:
        parts.append(f"\n{extra.strip()}\n")
    for seat in seats:
        parts += [
            f"\n--- {seat.node_id} ({seat.description}) ---\nposition: ",
            seat.ref("position"),
            "\nconcerns:\n",
            seat.ref("concerns"),
            "\n",
        ]
    return parts
