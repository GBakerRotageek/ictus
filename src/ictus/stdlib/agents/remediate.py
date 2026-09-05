"""An in-flow helper that diagnoses a blocked run and talks the person through it.

Placed behind a gate, this is the difference between "go and fix it yourself"
and staying in the run while it gets fixed. Conductor's ``dialog`` opens a
multi-turn conversation — in the dashboard when one is served, in the terminal
otherwise — so the person is talked through the problem rather than handed a
failure and a shrug.

The prompt draws one hard line, and it is a security boundary rather than a
preference: **the helper never handles credentials.** It may inspect and repair
what needs no secret. The moment a fix needs a token, a permission change or
anything else sensitive, it stops and hands over the exact command for the
person to run in their own shell. Nothing asks them to paste a secret into a
conversation with a model, and no secret value is ever printed back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import AgentNode
from ictus.graph.ports import OutputPort, PortType
from ictus.graph.ref import Template, tpl

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort

__all__ = ["remediate"]

_CREDENTIAL_BOUNDARY = (
    "Hard rules you must not break:\n"
    "- Never ask the person to paste a token, key, password or any other secret "
    "into this conversation. If a fix needs one, give them the exact command to "
    "run in their own shell and let them run it there.\n"
    "- Never print the value of an environment variable or any credential, even "
    "if you can read it. Say whether it is set, never what it is.\n"
    "- Anything that changes access, permissions or credentials is theirs to run, "
    "not yours. Hand over the command and say what it will do.\n"
)

_METHOD = (
    "Work in this order:\n"
    "1. Diagnose before changing anything. Check what is actually true: is the "
    "command present, does the host resolve, is the variable set, what exactly "
    "did the failure say.\n"
    "2. Fix what you can fix safely — a missing package, a wrong path, a stale "
    "config file. Say what you are doing before you do it.\n"
    "3. The moment a fix needs a credential or an access change, stop and hand "
    "it over with the exact command.\n"
    "4. When there is nothing left you can do, say plainly what is still needed "
    "and who has to do it.\n"
)


def remediate(
    *,
    node_id: str = "remediate",
    problem: Template,
    subject: str = "the blocked step",
    description: str = "",
    inputs: Sequence[InputPort] = (),
) -> AgentNode:
    """A helper that investigates a failure and works through it with the person.

    ``problem`` is a template carrying the failure detail — typically the report
    from whatever check failed, so the helper starts from evidence rather than
    guessing.
    """
    return AgentNode(
        node_id=node_id,
        description=description or f"Help resolve {subject}",
        inputs=tuple(inputs),
        prompt=tpl(
            f"A workflow is paused because {subject} failed. Here is what was observed:\n\n",
            problem,
            "\n\nHelp the person get past this. Talk to them: explain what is "
            "wrong in plain terms, then work through it with them.\n\n",
            _METHOD,
            "\n",
            _CREDENTIAL_BOUNDARY,
            "\nWhen you are done, report whether the run can proceed. It will be "
            "re-checked automatically, so do not claim success you have not "
            "verified.",
        ),
        # Always converse: the person is already waiting at a gate, and a helper
        # that silently did or did not fix something would be worse than useless.
        dialog_trigger=(
            "Always enter dialog. The workflow is paused on a failure that needs "
            "a person, and this agent exists to work through it with them."
        ),
        declared_outputs=(
            OutputPort("resolved", PortType.BOOLEAN, "Whether the helper believes it is fixed"),
            OutputPort("summary", PortType.STRING, "What was done, and what is still needed"),
        ),
    )
