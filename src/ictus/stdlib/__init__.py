"""Ready-made nodes and stages for the shapes that recur in every pipeline.

Organised by what Conductor charges for them:

* ``gates``     — human decision points (``human_gate``)
* ``agents``    — model calls (``agent``)
* ``steps``     — zero-model steps (``set``, ``wait``, ``script``)
* ``terminals`` — explicit, distinguishable exits (``terminate``)
* ``stages``    — reusable sub-graphs (``workflow``)

One primitive per module, so the docstring next to a thing is about that thing.
"""

from __future__ import annotations

from ictus.stdlib.agents import briefing, remediate, validate_mcp, verdict
from ictus.stdlib.gates import approval_gate, ask_human, ask_human_for, choice_gate
from ictus.stdlib.stages import (
    APPROVE_OR_REJECT,
    ReviewOption,
    ScriptStep,
    briefing_gate,
    poll_until,
    resolve_unknowns,
    revise_loop,
    script_sequence,
    validate_mcps,
)
from ictus.stdlib.steps import bindings, constant, shell, wait
from ictus.stdlib.terminals import fail, succeed

__all__ = [
    "APPROVE_OR_REJECT",
    "ReviewOption",
    "ScriptStep",
    "approval_gate",
    "ask_human",
    "ask_human_for",
    "bindings",
    "briefing",
    "briefing_gate",
    "choice_gate",
    "constant",
    "fail",
    "poll_until",
    "remediate",
    "resolve_unknowns",
    "revise_loop",
    "script_sequence",
    "shell",
    "succeed",
    "validate_mcp",
    "validate_mcps",
    "verdict",
    "wait",
]
