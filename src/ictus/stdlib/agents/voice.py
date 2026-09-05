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

__all__ = ["SATISFIED", "voice"]

SATISFIED = "satisfied"

_STANCE = """You are one voice among several, each watching for something \
different. Assess the material below from your standpoint alone. Do not try to \
cover what the others are watching for, and do not soften a real objection \
because you assume someone else will raise it.

Put what you would change in `concerns`, one concrete item per line, each \
naming the change rather than what is wrong. Leave it empty only if you would \
change nothing.

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
    direction: Ref | None = None,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    tools: Sequence[str] | None = (),
) -> AgentNode:
    """One standpoint's assessment of ``subject``.

    ``charge`` is the framing every voice on a council shares — what the whole
    assessment is for — as against ``focus``, which is what this one voice is
    watching for. Usually a workflow input, so it can be set per run.

    ``prior`` and ``direction`` are what make a council deliberate rather than
    poll: the first is the last round's synthesis, the second is whatever a
    human said when they interjected. Both are usually forward references into
    a loop — they name steps that have not run on the first pass — so they are
    wrapped in blocks that render to nothing until they resolve. A voice told
    "a previous round concluded:" followed by nothing will invent what it was.

    ``tools`` defaults to none. A voice has the material in its prompt, and a
    council of four with tools is four agents independently going looking for
    the file it came from — observed on a live run, at four times the cost, for
    an assessment of text they had already been handed. Pass ``None`` for the
    workflow default, or a list where a voice genuinely has to go and check
    something. Note that ``claude-agent-sdk`` ignores per-agent tool lists.
    """
    if not persona.strip():
        raise CompositionError(
            f"voice {node_id!r} has no persona. Two voices with the same standpoint are "
            "one voice run twice, and a council of them agrees with itself."
        )
    if not focus.strip():
        raise CompositionError(f"voice {node_id!r} has no focus")

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
        prompt=tpl(*parts),
        declared_outputs=(
            OutputPort(SATISFIED, PortType.BOOLEAN, f"Nothing left to change re: {focus}"),
            OutputPort("position", PortType.STRING, "Where this voice stands, in a sentence"),
            OutputPort("concerns", PortType.STRING, "What to change, one per line"),
        ),
    )
