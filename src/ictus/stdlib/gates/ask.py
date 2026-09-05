"""Ask a person for values the run could not work out for itself."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import QuestionsNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.node import Question
    from ictus.graph.ports import InputPort
    from ictus.graph.ref import Ref

__all__ = ["ask_human", "ask_human_for"]


def ask_human(
    *,
    node_id: str = "ask",
    questions: Sequence[Question],
    description: str = "",
    inputs: Sequence[InputPort] = (),
    allow_abort: bool = False,
    allow_skip: bool | None = None,
) -> QuestionsNode:
    """Ask a fixed set of questions. Emits ``type: questions``.

    Distinct from a gate: a gate offers a decision among known options, this
    collects *values* — a path, a ticket id, a name. All of them cost one
    iteration together, not one each.

    Give every question an ``id``. It is the key its answer lands under, and
    naming them means inserting a question later does not renumber the answers
    that downstream nodes already reference.
    """
    return QuestionsNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        questions=tuple(questions),
        allow_abort=allow_abort or None,
        allow_skip=allow_skip,
    )


def ask_human_for(
    *,
    node_id: str = "ask",
    source: Ref,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    allow_abort: bool = True,
) -> QuestionsNode:
    """Ask questions an earlier node produced. Emits ``type: questions``.

    For everything that cannot be written down in advance. A ticket touching an
    unknown number of repositories has an unknown number of questions, so the
    node that works out *what* is missing is the one that writes them.

    ``source`` points at an array of strings or of question objects. Because the
    ids are only known at run time, the answers are read through
    ``ref("answers")`` rather than one port per question.
    """
    return QuestionsNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        source=source,
        allow_abort=allow_abort or None,
    )
