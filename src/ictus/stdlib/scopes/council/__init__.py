"""A council — several standpoints, deliberating until they agree or run out.

Each round every voice assesses at once and a synthesis step writes one report.
Exits ``agreed`` when every voice is satisfied, ``unresolved`` when the round
budget runs out, and ``halted`` when ``interject`` is on and a person stops it.
All three carry the report and what was still contested.
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
from ictus.prompting import prompt
from ictus.stdlib.llm.voice import SATISFIED, UNCHECKED, voice
from ictus.stdlib.scopes.outcomes import AGREED, HALTED, UNRESOLVED
from ictus.stdlib.steps.counter import counter as counter_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.node import Node
    from ictus.graph.ref import Ref, TemplatePart

__all__ = ["AGREED", "HALTED", "UNRESOLVED", "Voice", "council"]

ROUND = "round_number"
REPORT = "report"
PANEL = "voices"
INTERJECT = "interject"
TALLY = "tallied"
VERIFY = "verify"
CHECKS = "checks"
CHECK_SUFFIX = "_check"
DIRECTION = "direction"

STR, NUM = PortType.STRING, PortType.NUMBER


@dataclass(frozen=True, slots=True)
class Voice:
    """One seat on a council: who is speaking and what they are watching for.

    A spec rather than a node; a voice reads things built after it.
    """

    node_id: str
    persona: str
    focus: str
    description: str = ""
    tools: tuple[str, ...] | None = ()
    """What this voice may call.

    ``()`` denies tools; ``None`` gives the workflow's default set. Naming
    individual tools is emitted as written, and the conductor lint refuses it on
    a provider that cannot translate the names.
    """

    max_turns: int | None = None
    """This voice's ceiling on tool-use rounds. Only meaningful with ``tools=None``.

    The engine's default of fifty is a kill, not a throttle.
    """


def council(
    *,
    stage_id: str,
    voices: Sequence[Voice],
    subject: str = "What the council is assessing",
    charge: str = "What this assessment is for",
    verify: str = "",
    verify_each: str = "",
    deliberate: bool = True,
    verify_turns: int = 200,
    rounds: int = 3,
    interject: bool = False,
    remember: bool = True,
    synthesis: str = "",
    description: str = "",
) -> Scope:
    """Convene ``voices`` and deliberate until they agree or the rounds run out.

    Outcomes are ``agreed``, ``unresolved`` and — with ``interject`` on —
    ``halted``. All three carry the last report, what was contested, and the
    round count. ``agreed`` means the voices converged on a report, not that
    they liked what they read.

    ``deliberate`` hands every voice the others' positions from the last round,
    verbatim and attributed. Costs prompt tokens, no extra model calls.

    ``verify_each`` puts a checker behind every voice, running at once, before
    the round is written up; it doubles the model calls in a round. With it on,
    a voice reads its own checker's corrections next round rather than the
    group checker's. ``verify_turns`` is the ceiling for every checker.

    ``remember`` keeps each voice's session across rounds, and needs a provider
    that can resume one.

    Contract: inputs ``subject`` (required), ``charge`` and ``intent``
    (optional). ``charge`` is the standing instruction every voice receives;
    ``intent`` is what the material itself is meant to do.
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
            "unverified": OutputPort("unverified", STR, "What the round could not check"),
            "rounds": OutputPort("rounds", NUM, "How many rounds it took"),
            **(
                {"corrections": OutputPort("corrections", STR, "What verification struck out")}
                if verify
                else {}
            ),
        },
        description=description or f"Council of {len(voices)}: {subject}",
        loop_passes=rounds,
    )
    body = scope.body
    material = body.declare_input("subject", STR, description=subject)
    intent = body.declare_input(
        "intent", STR, required=False, description="What the material is meant to do"
    )
    # An input rather than a constant: the charge changes per run.
    charge_in = body.declare_input("charge", STR, required=False, description=charge)

    counter = body.add(counter_step(node_id=ROUND, description="Which round this is"))
    body.set_entry(counter)
    body.feed(counter, "value", counter, ROUND)

    # Both are forward references into the loop: neither step has run when the
    # first round's voices are prompted. The compiler adds the first-pass guard.
    prior = ref_to(REPORT, "text", STR)
    steer = ref_to(INTERJECT, "notes", STR) if interject else None

    # Which corrections a voice reads next round: its own checker's when there
    # is one, otherwise the group checker's.
    def _checked_for(node_id: str) -> Ref | None:
        if verify_each:
            return ref_to(f"{node_id}{CHECK_SUFFIX}", "corrections", STR)
        return ref_to(VERIFY, "corrections", STR) if verify else None

    def _peers_of(node_id: str) -> tuple[tuple[str, Ref, Ref], ...]:
        if not deliberate:
            return ()
        return tuple(
            (
                other.node_id,
                ref_to(other.node_id, "position", STR),
                ref_to(other.node_id, "concerns", STR),
            )
            for other in voices
            if other.node_id != node_id
        )

    seats = [
        body.add(
            voice(
                node_id=spec.node_id,
                persona=spec.persona,
                focus=spec.focus,
                description=spec.description,
                tools=spec.tools,
                max_turns=spec.max_turns,
                # Its own key: a session cannot be shared by concurrent steps.
                remember=f"{stage_id}-{spec.node_id}" if remember else None,
                subject=material.ref(),
                charge=charge_in.ref(),
                intent=intent.ref(),
                prior=prior,
                peers=_peers_of(spec.node_id),
                checked=_checked_for(spec.node_id),
                direction=steer,
                inputs=(
                    InputPort("subject", STR),
                    InputPort("charge", STR, optional=True),
                    InputPort("intent", STR, optional=True),
                    InputPort(REPORT, STR, "The last round", optional=True),
                    *(
                        (InputPort(VERIFY, STR, "What was struck out", optional=True),)
                        if (verify or verify_each)
                        else ()
                    ),
                    *(
                        (InputPort(DIRECTION, STR, "Human direction", optional=True),)
                        if interject
                        else ()
                    ),
                    *(
                        port
                        for name, _, _ in _peers_of(spec.node_id)
                        for port in (
                            InputPort(f"{name}__said", STR, optional=True),
                            InputPort(f"{name}__wants", STR, optional=True),
                        )
                    ),
                ),
            )
        )
        for spec in voices
    ]
    panel = body.parallel(PANEL, seats, description=f"{len(seats)} voices, at once")

    # Every voice reads every other, a round behind: the seats run at once, so
    # a seat on its second pass sees its neighbours' first.
    if deliberate:
        for seat in seats:
            for other in seats:
                if other is seat:
                    continue
                body.feed(other, "position", seat, f"{other.node_id}__said", previous_pass=True)
                body.feed(other, "concerns", seat, f"{other.node_id}__wants", previous_pass=True)

    # One checker per voice, rather than one over the finished report.
    guards: list[AgentNode] = []
    for seat in seats:
        if not verify_each:
            break
        guards.append(
            body.add(
                AgentNode(
                    node_id=f"{seat.node_id}{CHECK_SUFFIX}",
                    description=f"Check what {seat.node_id!r} claimed",
                    inputs=(
                        InputPort("position", STR),
                        InputPort("concerns", STR),
                        InputPort(UNCHECKED, STR),
                    ),
                    # A checker needs tools; without them it is another voice.
                    tools=None,
                    max_turns=verify_turns,
                    session_key=(f"{stage_id}-{seat.node_id}{CHECK_SUFFIX}" if remember else None),
                    prompt=tpl(
                        verify_each.strip(),
                        "\n\n" + prompt(__name__, "verify_each") + "\n",
                        seat.ref("position"),
                        "\n\n--- what it would change ---\n",
                        seat.ref("concerns"),
                        "\n\n--- what it could not check ---\n",
                        seat.ref(UNCHECKED),
                    ),
                    declared_outputs=(
                        OutputPort("corrections", STR, "Claims that did not survive"),
                        OutputPort("sound", PortType.BOOLEAN, "Whether this voice holds up"),
                    ),
                )
            )
        )
        body.feed(seat, "position", guards[-1], "position")
        body.feed(seat, "concerns", guards[-1], "concerns")
        body.feed(seat, UNCHECKED, guards[-1], UNCHECKED)
        # Back round the loop. `feed`, not a declared port: a group member's
        # output is addressed through its group, and only the edge knows that.
        body.feed(guards[-1], "corrections", seat, VERIFY)
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
                        InputPort(f"{seat.node_id}__unchecked", STR),
                        InputPort(f"{seat.node_id}__ok", PortType.BOOLEAN),
                        *((InputPort(f"{seat.node_id}__checked", STR),) if verify_each else ()),
                    )
                ),
            ),
            prompt=tpl(*_synthesis(synthesis, seats, checked=bool(verify_each))),
            declared_outputs=(
                OutputPort("text", STR, "The round's report"),
                OutputPort("dissent", STR, "What is still contested, and by whom"),
                OutputPort("unverified", STR, "Claims resting on something nobody could check"),
            ),
        )
    )
    # `feed`, not `connect`: a group member carries no route of its own.
    # `satisfied` is wired though the prompt never reads it, because a group's
    # fields are projected one at a time and the agreement test runs here.
    for seat in seats:
        body.feed(seat, "position", report, f"{seat.node_id}__position")
        body.feed(seat, "concerns", report, f"{seat.node_id}__concerns")
        body.feed(seat, UNCHECKED, report, f"{seat.node_id}__unchecked")
        body.feed(seat, SATISFIED, report, f"{seat.node_id}__ok")
    for guard in guards:
        body.feed(guard, "corrections", report, f"{guard.node_id[: -len(CHECK_SUFFIX)]}__checked")
    body.feed(counter, "value", report, ROUND)
    # The edge that makes the next round a deliberation rather than a re-poll.
    for seat in seats:
        body.feed(report, "text", seat, REPORT)
    if guards:
        # A second group, not a chain: the checks are independent.
        checks = body.parallel(CHECKS, guards, description=f"{len(guards)} checks, at once")
        body.route(panel, checks)
        body.route(checks, report)
    else:
        body.route(panel, report)

    checker: AgentNode | None = None
    if verify:
        checker = body.add(
            AgentNode(
                node_id=VERIFY,
                description="Check the report against the thing itself",
                inputs=(
                    InputPort("text", STR),
                    InputPort("dissent", STR),
                    InputPort("unverified", STR),
                ),
                # The engine's default tools; a verifier has to go and look.
                tools=None,
                # The most tool-hungry step here, and running out of turns
                # raises rather than throttles.
                max_turns=verify_turns,
                session_key=f"{stage_id}-{VERIFY}" if remember else None,
                prompt=tpl(
                    verify.strip(),
                    "\n\n" + prompt(__name__, "verify_report") + "\n",
                    report.ref("text"),
                    "\n\n--- what they still contest ---\n",
                    report.ref("dissent"),
                    "\n\n--- what they could not check ---\n",
                    report.ref("unverified"),
                ),
                declared_outputs=(
                    OutputPort("corrections", STR, "Claims that did not survive"),
                    OutputPort("sound", PortType.BOOLEAN, "Whether it holds up"),
                ),
            )
        )
        body.connect(report, "text", checker, "text")
        body.feed(report, "dissent", checker, "dissent")
        body.feed(report, "unverified", checker, "unverified")
        if not guards:
            # One source per port: with per-voice checkers on, its own.
            for seat in seats:
                body.feed(checker, "corrections", seat, VERIFY)

    agreed = scope.exit(
        node_id="agreed",
        outcome=AGREED,
        reason="Every voice is satisfied",
        report=report.ref("text"),
        dissent=report.ref("dissent"),
        unverified=report.ref("unverified"),
        rounds=counter.ref("value"),
        **({"corrections": checker.ref("corrections")} if checker is not None else {}),
    )
    unresolved = scope.exit(
        node_id="unresolved",
        outcome=UNRESOLVED,
        reason=f"Still contested after {rounds} round(s)",
        report=report.ref("text"),
        dissent=report.ref("dissent"),
        unverified=report.ref("unverified"),
        rounds=counter.ref("value"),
        **({"corrections": checker.ref("corrections")} if checker is not None else {}),
    )

    # Agreement measures convergence; verification measures soundness.
    verdicts = [seat.ref(SATISFIED) for seat in seats]
    if checker is not None:
        verdicts.append(checker.ref("sound"))
    settled = every(*verdicts)
    spent = at_least(counter.ref("value"), rounds)

    decides: Node = checker if checker is not None else report
    if interject:
        gate = body.add(
            GateNode(
                node_id=INTERJECT,
                description="Read this round and steer it",
                inputs=(
                    InputPort("text", STR),
                    InputPort("dissent", STR),
                    InputPort(ROUND, NUM, "Which round"),
                    *(
                        (InputPort("corrections", STR), InputPort("sound", PortType.BOOLEAN))
                        if checker is not None
                        else ()
                    ),
                    *(InputPort(f"{seat.node_id}__ok", PortType.BOOLEAN) for seat in seats),
                ),
                prompt=tpl(
                    "Round ",
                    counter.ref("value"),
                    f" of {rounds}.\n\n",
                    # What carrying on will do, which the person cannot see.
                    "**Where it stands**\n\n",
                    *_standing(seats, checker),
                    "\nChoosing to carry on hands it back to the council, which "
                    "then finishes if every voice is satisfied and the report "
                    f"survived checking, or runs another round — up to {rounds}.\n\n",
                    report.ref("text"),
                    "\n\n--- still contested ---\n",
                    report.ref("dissent"),
                    *(
                        (
                            "\n\n--- what did not survive checking ---\n",
                            checker.ref("corrections"),
                        )
                        if checker is not None
                        else ()
                    ),
                ),
                choices=(
                    GateChoice("continue", "Hand it back to the council"),
                    GateChoice(
                        "steer",
                        "Hand it back, with direction they must follow",
                        prompt_for="notes",
                        multiline=True,
                    ),
                    GateChoice("stop", "Stop now and take this report as it is"),
                ),
            )
        )
        # After verification when there is one, so the corrections are in front
        # of the person. The report cannot have two unconditional routes out.
        if checker is not None:
            body.connect(checker, "corrections", gate, "corrections")
            body.feed(report, "text", gate, "text")
        else:
            body.connect(report, "text", gate, "text")
        body.feed(report, "dissent", gate, "dissent")
        body.feed(counter, "value", gate, ROUND)
        # Everything the standing block reads; undeclared state renders empty.
        for seat in seats:
            body.feed(seat, SATISFIED, gate, f"{seat.node_id}__ok")
        if checker is not None:
            body.feed(checker, "sound", gate, "sound")
        halted = scope.exit(
            node_id="halted",
            outcome=HALTED,
            reason="Stopped by the person overseeing the council",
            report=report.ref("text"),
            dissent=report.ref("dissent"),
            unverified=report.ref("unverified"),
            rounds=counter.ref("value"),
            **({"corrections": checker.ref("corrections")} if checker is not None else {}),
        )
        # A gate's branches are the human's buttons, so it cannot test whether
        # the voices agreed. The tally is a zero-cost step both route to.
        decides = body.add(
            ComputeNode(
                node_id=TALLY,
                description="Decide whether the council is finished",
                value="continuing",
                value_type=STR,
                # A route condition's references must be in scope where it is
                # evaluated, so the tally declares every verdict and the count.
                inputs=(
                    InputPort("choice", STR),
                    InputPort(ROUND, NUM, "Which round"),
                    *(InputPort(f"{seat.node_id}__ok", PortType.BOOLEAN) for seat in seats),
                    *((InputPort("sound", PortType.BOOLEAN),) if checker is not None else ()),
                ),
                declared_outputs=(OutputPort("value", STR, "Marker"),),
            )
        )
        body.feed(gate, "selected", decides, "choice")
        body.feed(counter, "value", decides, ROUND)
        for seat in seats:
            body.feed(seat, SATISFIED, decides, f"{seat.node_id}__ok")
        if checker is not None:
            body.feed(checker, "sound", decides, "sound")
        body.branch(gate, {"continue": decides, "steer": decides, "stop": halted})
        for seat in seats:
            body.feed(gate, "notes", seat, DIRECTION)

    body.route(decides, agreed, when=settled)
    body.route(decides, unresolved, when=spent)
    body.route(decides, counter)
    return scope


def _standing(seats: Sequence[AgentNode], checker: AgentNode | None) -> list[TemplatePart]:
    """Each voice's verdict and the check's, so a choice is an informed one."""
    out: list[TemplatePart] = []
    for seat in seats:
        out += [
            f"- `{seat.node_id}` satisfied: ",
            tpl(seat.ref(SATISFIED)),
            "\n",
        ]
    if checker is not None:
        out += ["- report survived checking: ", tpl(checker.ref("sound")), "\n"]
    return out


def _synthesis(
    extra: str, seats: Sequence[AgentNode], *, checked: bool = False
) -> list[TemplatePart]:
    """The report prompt: every voice's position, and what to do with them."""
    parts: list[TemplatePart] = [
        "Several voices have just assessed the same material, each watching for "
        "something different. Write one report of where this round got to.\n\n"
        "Do not average them. Where they disagree, say who disagrees with whom and "
        "about what — a disagreement recorded as a disagreement is useful, and one "
        "smoothed into consensus is a decision made by omission. Where a concern "
        "from one voice would be answered by another's suggestion, say so.\n\n"
        "Put anything still contested in `dissent`, naming the voices. Leave it "
        "empty only when nothing is.\n\n"
        "Each voice also reports what it could not check. Collect all of it into "
        "`unverified`, naming the voice and what blocked it. This is the one part "
        "of the report you must not tidy away: a finding that rests on a lookup "
        "nobody managed to perform is not a finding, and it must be visible as "
        "such rather than written up in the same voice as a checked one. Where a "
        "voice put a claim in `concerns` that its own `unchecked` shows it could "
        "not verify, say so in `unverified` rather than repeating the claim.\n\n"
        "Voices agreeing is not evidence. Several voices blocked by the same "
        "failed lookup will reach the same wrong conclusion independently and "
        "look like consensus; if their `unchecked` entries name the same "
        "obstacle, say that plainly — it is the most useful thing in the round.\n",
    ]
    if checked:
        parts.append(
            "\nEach voice was checked on its own before you saw it. Where a claim did "
            "not survive, it is struck out below its author: do not carry it into the "
            "report, and do not quietly restate it in your own words. A voice whose "
            "position rested on a struck claim has a weaker position than it thinks, "
            "and saying so is the report's job.\n"
        )
    if extra:
        parts.append(f"\n{extra.strip()}\n")
    for seat in seats:
        parts += [
            f"\n--- {seat.node_id} ({seat.description}) ---\nposition: ",
            seat.ref("position"),
            "\nconcerns:\n",
            seat.ref("concerns"),
            "\ncould not check:\n",
            seat.ref(UNCHECKED),
            "\n",
        ]
        if checked:
            parts += [
                "did not survive checking:\n",
                ref_to(f"{seat.node_id}{CHECK_SUFFIX}", "corrections", STR),
                "\n",
            ]
    return parts
