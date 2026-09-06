"""A conversation — several people, taking turns, until they agree.

A council polls: every voice assesses at once, a synthesis step writes the round
up, and the next round reads that write-up. It converges on a *record*. What it
cannot do is let anyone answer anyone, because the voices run concurrently and
none of them has heard the others yet.

A roundtable talks. Each person reads the material alone first, then they take
turns: the second speaker has heard the first *this round*, the third has heard
both, and the round closes when the last has spoken. Nobody summarises on their
behalf and there is no lag inside a round — which is the whole difference, and
the reason this exists alongside `council` rather than as an option on it.

The cost is wall-clock. Voices in a council run at once; speakers here run in
order, so a round takes as long as the sum of its turns rather than the longest
of them. Buy that only when you want them to actually argue.

Every phase is its own node, and the turns are named after the people taking
them, so a run reads as a conversation in the trace and in the dashboard: who
has spoken, what they said, and who still disagrees.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import AgentNode, ComputeNode, GateChoice, GateNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import at_least, equals, every, optional, ref_to, tpl
from ictus.graph.scope import Scope, outcome_scope
from ictus.stdlib.steps.counter import counter as counter_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.node import Node
    from ictus.graph.pipeline import ParallelGroup
    from ictus.graph.ref import Ref, Template, TemplatePart

__all__ = ["AGREED", "HALTED", "UNRESOLVED", "Speaker", "roundtable"]

AGREED = "agreed"
UNRESOLVED = "unresolved"
HALTED = "halted"

ROUND = "round_number"
STUDY = "study"
STUDY_SUFFIX = "_study"
MINUTES = "minutes"
INTERJECT = "interject"
TALLY = "tallied"
DIRECTION = "direction"

REMARK = "remark"
AGREE = "agree"
NOTES = "notes"

STR, NUM, BOOL = PortType.STRING, PortType.NUMBER, PortType.BOOLEAN


@dataclass(frozen=True, slots=True)
class Speaker:
    """One seat at the table: who is talking and what they are watching for."""

    node_id: str
    persona: str
    focus: str
    description: str = ""
    tools: tuple[str, ...] | None = ()
    """What this speaker may call. ``()`` denies tools, ``None`` gives the
    engine's default set. A speaker with tools needs ``max_turns``."""

    max_turns: int | None = None
    """Ceiling on tool-use rounds. Required when ``tools`` is ``None``: the
    engine's fifty is a kill rather than a throttle, and it lands after every
    earlier turn has been paid for."""


def roundtable(
    *,
    stage_id: str,
    speakers: Sequence[Speaker],
    subject: str = "What the table is discussing",
    charge: str = "What this discussion is for",
    rounds: int = 3,
    study: str = "",
    interject: bool = False,
    remember: bool = True,
    closing: str = "",
    description: str = "",
) -> Scope:
    """Sit ``speakers`` at a table and let them talk until they agree.

    Outcomes are ``agreed`` and ``unresolved``, plus ``halted`` when
    ``interject`` is on and a person stops it. All three carry the minutes, what
    was still contested, and how many rounds it took.

    ``study`` is what each person is told to do before anybody speaks. Those
    steps run at once, once, outside the loop — reading is independent and
    re-reading every round would be paid for every round. Leave it empty to send
    people in cold, which is right when the material is short enough to sit in
    the prompt.

    ``rounds`` is turns each, not turns total: a table of four over three rounds
    is twelve model calls plus the study and the minutes.

    ``closing`` is the extra instruction to whoever writes the minutes at the
    end. The minutes run once, after the talking stops, rather than once a round
    — a conversation does not need summarising while it is still happening, and
    a council paying for that summary every round is most of what a council
    costs.

    Order is the design. Later speakers hear the earlier ones from *this* round
    and the earlier ones hear them from the last, so put the person whose
    framing should land first at the front, and the person who should have the
    last word at the back.
    """
    if len(speakers) < 2:
        raise CompositionError(
            f"roundtable {stage_id!r} needs at least two speakers; one person taking "
            "turns with themselves is a monologue with extra steps"
        )
    repeated = sorted(n for n, c in Counter(s.node_id for s in speakers).items() if c > 1)
    if repeated:
        raise CompositionError(f"roundtable {stage_id!r} seats more than one {repeated}")
    if rounds < 1:
        raise CompositionError(
            f"roundtable {stage_id!r} needs rounds >= 1, got {rounds}. Unlike a council "
            "the first round is real conversation — everyone after the first speaker has "
            "already heard somebody — so one round is a short meeting rather than a "
            "pointless one."
        )
    for spec in speakers:
        if spec.tools is None and spec.max_turns is None:
            raise CompositionError(
                f"speaker {spec.node_id!r} has tools but no max_turns. The engine's "
                "default of fifty tool-use rounds is a kill rather than a throttle, and "
                "at a table it lands after every earlier turn has been paid for."
            )

    outcomes = (AGREED, UNRESOLVED, *((HALTED,) if interject else ()))
    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=outcomes,
        carry={
            MINUTES: OutputPort(MINUTES, STR, "What the table concluded"),
            "dissent": OutputPort("dissent", STR, "What was still contested"),
            "rounds": OutputPort("rounds", NUM, "How many rounds it took"),
        },
        description=description or f"Roundtable of {len(speakers)}: {subject}",
        loop_passes=rounds,
    )
    body = scope.body
    material = body.declare_input("subject", STR, description=subject)
    charge_in = body.declare_input("charge", STR, required=False, description=charge)

    steer = ref_to(INTERJECT, "notes", STR) if interject else None

    # --- everyone reads on their own, once ---------------------------------
    desks: dict[str, AgentNode] = {}
    reading: ParallelGroup | None = None
    if study:
        for spec in speakers:
            desks[spec.node_id] = body.add(
                AgentNode(
                    node_id=f"{spec.node_id}{STUDY_SUFFIX}",
                    description=f"What {spec.node_id!r} makes of it alone",
                    inputs=(InputPort("subject", STR), InputPort("charge", STR, optional=True)),
                    tools=spec.tools,
                    max_turns=spec.max_turns,
                    # The same session the speaker later talks from, so it
                    # arrives at the table remembering what it read rather than
                    # a summary of what it read. Safe to share: the desks run
                    # concurrently with each other, never with their speaker.
                    session_key=f"{stage_id}-{spec.node_id}" if remember else None,
                    prompt=tpl(
                        f"{spec.persona.strip()}\n\nYour focus: {spec.focus.strip()}\n\n",
                        study.strip(),
                        "\n\nYou are about to sit down with several other people and "
                        "work this out together, and you are the only one who will have "
                        "read it from your angle. Form your own view first — the "
                        "discussion is worth having only if the people at it disagree "
                        "for reasons they arrived at independently.\n\n"
                        "In `notes`, write what you found and where you stand, for "
                        "yourself. Nobody else reads this.\n\n",
                        optional("--- what this is for ---\n", charge_in.ref(), "\n\n"),
                        "--- the material ---\n",
                        material.ref(),
                    ),
                    declared_outputs=(OutputPort(NOTES, STR, "What this person found alone"),),
                )
            )
        for desk in desks.values():
            body.connect_input(material, desk, "subject")
            body.connect_input(charge_in, desk, "charge")
        reading = body.parallel(
            STUDY, list(desks.values()), description=f"{len(desks)} reading, at once"
        )
        body.set_entry(reading)

    counter = body.add(counter_step(node_id=ROUND, description="Which round this is"))
    if reading is not None:
        # Reading is the way in and happens once; the loop goes back to the
        # counter, never to the desks.
        body.route(reading, counter)
    else:
        body.set_entry(counter)
    body.feed(counter, "value", counter, ROUND)

    # --- the turns ----------------------------------------------------------
    seats: list[AgentNode] = []
    for index, spec in enumerate(speakers):
        heard: list[TemplatePart] = []
        for other in speakers:
            if other.node_id == spec.node_id:
                continue
            said: Ref = ref_to(other.node_id, REMARK, STR)
            # Everyone before you has spoken this round; everyone after you last
            # spoke in the round before, and on the first round not at all —
            # which is what the guard renders away.
            heard.append(optional(f"\n**{other.node_id}** said:\n", said, "\n"))
        seats.append(
            body.add(
                AgentNode(
                    node_id=spec.node_id,
                    description=spec.description or f"{spec.focus.strip()}",
                    inputs=(
                        InputPort("subject", STR),
                        InputPort("charge", STR, optional=True),
                        InputPort(ROUND, NUM, "Which round this is"),
                        *((InputPort(NOTES, STR, "Your own reading"),) if study else ()),
                        *(
                            InputPort(f"{other.node_id}__said", STR, optional=True)
                            for other in speakers
                            if other.node_id != spec.node_id
                        ),
                        *(
                            (InputPort(DIRECTION, STR, "Human direction", optional=True),)
                            if interject
                            else ()
                        ),
                    ),
                    tools=spec.tools,
                    max_turns=spec.max_turns,
                    session_key=f"{stage_id}-{spec.node_id}" if remember else None,
                    prompt=tpl(
                        f"{spec.persona.strip()}\n\nYour focus: {spec.focus.strip()}\n\n",
                        "Round ",
                        counter.ref("value"),
                        f" of {rounds}. ",
                        "You are one of several people working this out together, "
                        f"speaking {_position(index, len(speakers))}. This is a "
                        "conversation, not a survey: the others' words are below, and "
                        "the point of your turn is to move the discussion rather than "
                        "to restate where you stand.\n\n"
                        "So: answer the people you disagree with by name and say what "
                        "would change your mind. Say plainly when somebody has changed "
                        "yours, and which of them did. Where you have nothing to add to "
                        "a point somebody else has already made properly, say that "
                        "instead of making it again — agreement stated once is worth "
                        "more than the same position restated by four people.\n\n"
                        "Put your contribution in `remark`. It is the only thing the "
                        "others see of this turn, so it has to stand on its own.\n\n"
                        "Set `agree` true when you would be content for the table to "
                        "stop here — meaning the discussion has covered what you came "
                        "with and your remaining disagreements, if any, are recorded "
                        "rather than resolved. Setting it true because the conversation "
                        "has become tiring is how a table agrees on something nobody "
                        "checked.\n\n",
                        optional("--- what this is for ---\n", charge_in.ref(), "\n\n"),
                        *(
                            (
                                "--- what you made of it on your own ---\n",
                                desks[spec.node_id].ref(NOTES),
                                "\n\n",
                            )
                            if study
                            else ()
                        ),
                        "--- what has been said ---\n",
                        *heard,
                        *(
                            (
                                optional(
                                    "\n--- direction from the person overseeing this ---\n",
                                    steer,
                                    "\n\nThis is not a vote you can outweigh. Take it as "
                                    "a constraint.\n",
                                ),
                            )
                            if steer is not None
                            else ()
                        ),
                        "\n--- the material ---\n",
                        material.ref(),
                    ),
                    declared_outputs=(
                        OutputPort(REMARK, STR, "What this person says this turn"),
                        OutputPort(AGREE, BOOL, "Whether they would stop here"),
                    ),
                )
            )
        )

    for seat in seats:
        body.connect_input(material, seat, "subject")
        body.connect_input(charge_in, seat, "charge")
        body.feed(counter, "value", seat, ROUND)
        if study:
            body.feed(desks[seat.node_id], NOTES, seat, NOTES)

    # Turn order, and the edges that make each turn hear the ones before it.
    body.route(counter, seats[0])
    for earlier, later in itertools.pairwise(seats):
        body.route(earlier, later)
    for speaking in seats:
        for neighbour in seats:
            if neighbour is speaking:
                continue
            body.feed(neighbour, REMARK, speaking, f"{neighbour.node_id}__said")

    return _close(
        scope,
        seats=seats,
        counter=counter,
        rounds=rounds,
        interject=interject,
        closing=closing,
    )


def _position(index: int, total: int) -> str:
    if index == 0:
        return "first"
    if index == total - 1:
        return "last"
    return f"{index + 1} of {total}"


def _close(
    scope: Scope,
    *,
    seats: Sequence[AgentNode],
    counter: Node,
    rounds: int,
    interject: bool,
    closing: str,
) -> Scope:
    """The end of a round: write it up if it is over, otherwise go round again.

    The minutes are deliberately outside the loop. A council pays a synthesis
    step every round because its voices cannot hear each other without one; a
    table has already said everything to itself, so the write-up is needed once,
    when the talking stops.
    """
    body = scope.body
    settled = every(*(seat.ref(AGREE) for seat in seats))
    spent = at_least(counter.ref("value"), rounds)

    minutes = body.add(
        AgentNode(
            node_id=MINUTES,
            description="Write up what the table concluded",
            inputs=(
                InputPort(ROUND, NUM, "Which round"),
                *(InputPort(f"{seat.node_id}__said", STR) for seat in seats),
                *(InputPort(f"{seat.node_id}__ok", BOOL) for seat in seats),
                # Not read by the prompt: the route *out* of here tests which
                # button the person pressed, and a condition needs its
                # references in scope exactly as a template does.
                *((InputPort("choice", STR, optional=True),) if interject else ()),
            ),
            prompt=tpl(
                "A group of people have just finished talking something through. "
                "Write up what they concluded.\n\n"
                "You were not in the room and you are not a participant: report the "
                "discussion, do not continue it. Where they agreed, say what they "
                "agreed and on whose argument. Where they did not, put it in `dissent` "
                "naming who holds what — a disagreement recorded as a disagreement is "
                "useful, and one smoothed into consensus is a decision made by "
                "omission.\n\n"
                "Say what changed during the conversation. A position somebody "
                "arrived with and abandoned, and why, is usually the most informative "
                "thing that happened, and it is the part a transcript buries.\n",
                *((f"\n{closing.strip()}\n",) if closing else ()),
                "\n--- the last thing each person said ---\n",
                *(
                    part
                    for seat in seats
                    for part in (f"\n**{seat.node_id}**:\n", seat.ref(REMARK), "\n")
                ),
            ),
            declared_outputs=(
                OutputPort("text", STR, "What the table concluded"),
                OutputPort("dissent", STR, "What is still contested, and by whom"),
            ),
        )
    )
    for seat in seats:
        body.feed(seat, REMARK, minutes, f"{seat.node_id}__said")
        body.feed(seat, AGREE, minutes, f"{seat.node_id}__ok")
    body.feed(counter, "value", minutes, ROUND)

    agreed = scope.exit(
        node_id=AGREED,
        outcome=AGREED,
        reason="The table agreed",
        minutes=minutes.ref("text"),
        dissent=minutes.ref("dissent"),
        rounds=counter.ref("value"),
    )
    unresolved = scope.exit(
        node_id=UNRESOLVED,
        outcome=UNRESOLVED,
        reason=f"Still contested after {rounds} round(s)",
        minutes=minutes.ref("text"),
        dissent=minutes.ref("dissent"),
        rounds=counter.ref("value"),
    )

    # The last speaker hands over to whatever decides whether to go round again.
    decides: Node = seats[-1]
    if interject:
        gate = body.add(
            GateNode(
                node_id=INTERJECT,
                description="Read the round and steer it",
                inputs=(
                    InputPort(ROUND, NUM, "Which round"),
                    *(InputPort(f"{seat.node_id}__said", STR) for seat in seats),
                    *(InputPort(f"{seat.node_id}__ok", BOOL) for seat in seats),
                ),
                prompt=tpl(
                    "Round ",
                    counter.ref("value"),
                    f" of {rounds}.\n\n**Where it stands**\n\n",
                    *(
                        part
                        for seat in seats
                        for part in (
                            f"- `{seat.node_id}` would stop here: ",
                            tpl(seat.ref(AGREE)),
                            "\n",
                        )
                    ),
                    "\nCarrying on hands it back to the table, which finishes when "
                    f"everyone would stop, or runs another round — up to {rounds}.\n\n"
                    "--- the last thing each person said ---\n",
                    *(
                        part
                        for seat in seats
                        for part in (f"\n**{seat.node_id}**:\n", seat.ref(REMARK), "\n")
                    ),
                ),
                choices=(
                    GateChoice("continue", "Hand it back to the table"),
                    GateChoice(
                        "steer",
                        "Hand it back, with direction they must follow",
                        prompt_for="notes",
                        multiline=True,
                    ),
                    GateChoice("stop", "Stop here and write it up"),
                ),
            )
        )
        body.route(seats[-1], gate)
        for seat in seats:
            body.feed(seat, REMARK, gate, f"{seat.node_id}__said")
            body.feed(seat, AGREE, gate, f"{seat.node_id}__ok")
        body.feed(counter, "value", gate, ROUND)
        for seat in seats:
            body.feed(gate, "notes", seat, DIRECTION)
        # A gate's branches are the person's buttons, so it cannot also test
        # whether the table agreed. One zero-cost step later is where both
        # "carry on" branches meet and the condition can be evaluated.
        tallied = body.add(
            ComputeNode(
                node_id=TALLY,
                description="Where the round left it",
                value="continue",
                value_type=STR,
                inputs=(
                    InputPort("choice", STR),
                    InputPort(ROUND, NUM),
                    *(InputPort(f"{seat.node_id}__ok", BOOL) for seat in seats),
                ),
                declared_outputs=(OutputPort("value", STR, "Marker"),),
            )
        )
        body.feed(gate, "selected", tallied, "choice")
        body.feed(gate, "selected", minutes, "choice")
        body.feed(counter, "value", tallied, ROUND)
        for seat in seats:
            body.feed(seat, AGREE, tallied, f"{seat.node_id}__ok")
        halted = scope.exit(
            node_id=HALTED,
            outcome=HALTED,
            reason="Stopped by the person overseeing the table",
            minutes=minutes.ref("text"),
            dissent=minutes.ref("dissent"),
            rounds=counter.ref("value"),
        )
        body.branch(gate, {"continue": tallied, "steer": tallied, "stop": minutes})
        body.route(minutes, halted, when=_stopped(gate))
        decides = tallied

    # Written up once, on the way out, whichever way that is.
    body.route(decides, minutes, when=settled)
    body.route(decides, minutes, when=spent)
    body.route(decides, counter)
    body.route(minutes, agreed, when=settled)
    body.route(minutes, unresolved)
    return scope


def _stopped(gate: Node) -> Template:
    """The person pressed stop, so the write-up is the end of it."""
    return equals(gate.ref("selected"), "stop")
