"""A voice — one standpoint, assessing something on its own terms.

A council is only worth convening if its members disagree, and they only
disagree if they were given different things to care about. A voice is
therefore two prompts, not one: a *persona* (who is speaking, and what they
have been burned by) and a *focus* (what they are watching for). The persona is
not decoration — "a performance engineer who has been paged at 3am for a
regression" and "assess performance" produce visibly different assessments, the
first because it supplies a threshold for what counts as serious.

The output contract is fixed rather than configurable, because a council routes
on it: ``satisfied`` is the boolean a loop-exit condition tests, and every voice
must answer the same question for the tally to mean anything.

That question is about the *record*, not the material. A council does not get to
edit what it is assessing — only its own positions change between rounds — so a
voice that withholds satisfaction until the code is good withholds it forever,
and the loop can only ever run out. Satisfaction here means "the report captures
where I stand", which is reachable however bad the material is, and which is
what makes an unresolved council actually mean something went wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import AgentNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import optional, tpl

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ref import Ref, TemplatePart

__all__ = ["SATISFIED", "UNCHECKED", "voice"]

SATISFIED = "satisfied"
UNCHECKED = "unchecked"

_STANCE = """You are one voice among several, each watching for something \
different. Assess the material below from your standpoint alone. Do not try to \
cover what the others are watching for, and do not soften a real objection \
because you assume someone else will raise it.

Put what you would change in `concerns`, one concrete item per line, each \
naming the change rather than what is wrong. Leave it empty only if you would \
change nothing.

A claim you have not checked is not a finding. If you could not check \
something — a file you could not open, a command that failed, a source you \
could not reach — put it in `unchecked`, saying what you tried and what \
stopped you. Do not route it into `concerns` as a recommendation instead.

A lookup you could not perform is not evidence about the thing you were \
looking for. A tool that errors is a fact about this environment, not about \
the material: `command not found` and `ModuleNotFoundError` mean you asked \
the wrong path or the wrong interpreter, and neither one means the thing you \
were checking does not exist. Try another way of reaching it before you give \
up, and if you still cannot, record that you could not — the others will \
build on whatever you assert.

`satisfied` is not about the material. It asks whether the record of this \
discussion is now complete from where you stand: set it true when the previous \
round's report states your position and your concerns fairly — including any \
disagreement you still hold, which being recorded is what settles it — and you \
have nothing to add. Set it false if the report missed something, got your \
position wrong, or if another voice raised something you now want to answer.

If no previous report is shown below, you have not seen one: `satisfied` is \
false. Say what you think anyway — that is what the first report is built \
from."""


def voice(
    *,
    node_id: str,
    persona: str,
    focus: str,
    subject: Ref,
    charge: Ref | None = None,
    intent: Ref | None = None,
    prior: Ref | None = None,
    peers: Sequence[tuple[str, Ref, Ref]] = (),
    checked: Ref | None = None,
    direction: Ref | None = None,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    tools: Sequence[str] | None = (),
    max_turns: int | None = None,
    remember: str | None = None,
) -> AgentNode:
    """One standpoint's assessment of ``subject``.

    ``charge`` is the framing every voice on a council shares — what the whole
    assessment is for — as against ``focus``, which is what this one voice is
    watching for. Usually a workflow input, so it can be set per run.

    ``checked`` carries what a verification step struck out of the last round,
    so a refuted claim is not argued again in the next one.

    ``peers`` is what lets a voice answer the others rather than a summary of
    them: ``(name, position, concerns)`` for each of the other seats, rendered
    verbatim and attributed. Without it a voice reads only the synthesis, which
    is one more agent's compression of what everybody said — so it can restate,
    but it cannot disagree with anyone in particular, and a council of that
    shape discovers and asserts round after round without converging.

    The references are a round behind, because the seats run at once. That is
    the right lag: a voice answering what its neighbours said last round is a
    deliberation, and there is no ordering in which it could answer what they
    are saying at this moment.

    ``prior`` and ``direction`` are what make a council deliberate rather than
    poll: the first is the last round's synthesis, the second is whatever a
    human said when they interjected. Both are usually forward references into
    a loop — they name steps that have not run on the first pass — so they are
    wrapped in blocks that render to nothing until they resolve. A voice told
    "a previous round concluded:" followed by nothing will invent what it was.

    ``tools`` has three states and they are not shades of one thing:

    * ``()`` — no tools. The default here, because a voice already has the
      material in its prompt and a council of four with tools is four agents
      going to look at the same file. Observed on a live run, at four times
      the cost, for an assessment of text they had already been handed.
    * ``None`` — whatever the engine gives a step by default. On
      ``claude-agent-sdk`` that is a full Claude Code session: filesystem,
      bash and web, permissions bypassed, up to fifty turns. This is what a
      voice assessing a *repository* wants, since the repository does not fit
      in a prompt.
    * a list — refused at composition. Conductor's ``tools:`` are workflow
      tool names, not the CLI's, and its provider raises rather than grant
      the wrong ones.

    ``max_turns`` is this voice's ceiling on tool-use rounds. It matters only
    when ``tools`` is ``None``: a voice that can go and look will, and the
    engine's default of fifty is not a throttle but a kill — the provider
    raises rather than returning what the step had, and no scope can catch it.
    Set it on any voice given tools, and expect a voice reading a repository to
    need a few hundred.

    ``remember`` names a session this voice resumes rather than starting cold.
    Round two of a council otherwise reads a summary of a meeting it did not
    attend: it has the report, and nothing it worked out for itself last time.
    Keys must differ between voices, because they run at once and a session
    cannot be shared by concurrent steps.
    """
    if not persona.strip():
        raise CompositionError(
            f"voice {node_id!r} has no persona. Two voices with the same standpoint are "
            "one voice run twice, and a council of them agrees with itself."
        )
    if not focus.strip():
        raise CompositionError(f"voice {node_id!r} has no focus")
    if tools is None and max_turns is None:
        raise CompositionError(
            f"voice {node_id!r} has tools but no max_turns. A voice that can go and look "
            "will, and the engine's default of fifty tool-use rounds is a kill rather "
            "than a throttle: the provider raises instead of returning what the step had, "
            "no scope can turn that into an outcome, and it takes the whole council down "
            "with it — after every earlier round has been paid for. Set max_turns "
            "explicitly (200 suits a voice reading a repository), or tools=() if this "
            "voice should assess only what it is handed."
        )

    parts: list[TemplatePart] = [
        f"{persona.strip()}\n\nYour focus: {focus.strip()}\n\n{_STANCE}\n\n",
    ]
    if charge is not None:
        parts += [optional("--- what this council has been asked to do ---\n", charge, "\n\n")]
    if intent is not None:
        parts += [optional("What this is meant to do:\n", intent, "\n\n")]
    parts += ["--- the material ---\n", subject, "\n"]
    if prior is not None:
        parts += [
            "\n",
            optional(
                "--- where the last round got to ---\n",
                prior,
                "\n\nRespond to it. Say plainly if it changed your mind, and say plainly "
                "if it did not — agreeing to close a discussion you still disagree with "
                "is the one thing that makes this whole exercise worthless.\n",
            ),
        ]
    if peers:
        block: list[TemplatePart] = []
        for name, position, concerns in peers:
            block += [
                optional(
                    f"\n### {name}\nsaid: ",
                    position,
                    "",
                ),
                optional("\nwants changed:\n", concerns, "\n"),
            ]
        parts += [
            "\n",
            "--- what the others said last round, in their own words ---\n",
            *block,
            "\nThese are their words, not a summary of them. Answer the ones you "
            "disagree with by name and say what would change your mind; where one of "
            "them has changed yours, say so and say which. A round where nobody "
            "addresses anybody is four assessments filed together, not a council.\n",
        ]
    if checked is not None:
        parts += [
            "\n",
            optional(
                "--- what verification struck out of the last round ---\n",
                checked,
                "\n\nThese claims did not survive being checked against the thing "
                "itself. Do not repeat them, and do not rebuild the same argument on "
                "a different one you have not checked either.\n",
            ),
        ]
    if direction is not None:
        parts += [
            "\n",
            optional(
                "--- direction from the person overseeing this ---\n",
                direction,
                "\n\nThis is not a vote you can outweigh. Take it as a constraint.\n",
            ),
        ]

    return AgentNode(
        node_id=node_id,
        description=description or f"{focus.strip()}",
        inputs=tuple(inputs),
        tools=None if tools is None else tuple(tools),
        max_turns=max_turns,
        session_key=remember,
        prompt=tpl(*parts),
        declared_outputs=(
            OutputPort(SATISFIED, PortType.BOOLEAN, f"Nothing left to change re: {focus}"),
            OutputPort("position", PortType.STRING, "Where this voice stands, in a sentence"),
            OutputPort("concerns", PortType.STRING, "What to change, one per line"),
            OutputPort(
                UNCHECKED,
                PortType.STRING,
                "What this voice could not verify, and what blocked it",
            ),
        ),
    )
