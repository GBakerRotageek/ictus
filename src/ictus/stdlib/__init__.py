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

from ictus.stdlib.agents import briefing, remediate, validate_mcp, verdict, voice
from ictus.stdlib.gates import approval_gate, ask_human, ask_human_for, choice_gate
from ictus.stdlib.stages import (
    AGREED,
    APPROVE_OR_REJECT,
    CONVERGED,
    DONE,
    EXHAUSTED,
    FAILED,
    HALTED,
    OK,
    UNCLEAR,
    UNRESOLVED,
    Attempt,
    Choice,
    ReviewOption,
    ScriptStep,
    Speaker,
    Tier,
    Voice,
    briefing_gate,
    classify,
    converge,
    council,
    resolve_unknowns,
    roundtable,
    script_sequence,
    tiered,
    try_shell,
    validate_mcps,
)
from ictus.stdlib.steps import bindings, constant, counter, save_text, shell, wait
from ictus.stdlib.terminals import fail, succeed

__all__ = [
    "AGREED",
    "APPROVE_OR_REJECT",
    "CONVERGED",
    "DONE",
    "EXHAUSTED",
    "FAILED",
    "HALTED",
    "OK",
    "UNCLEAR",
    "UNRESOLVED",
    "Attempt",
    "Choice",
    "ReviewOption",
    "ScriptStep",
    "Speaker",
    "Tier",
    "Voice",
    "approval_gate",
    "ask_human",
    "ask_human_for",
    "bindings",
    "briefing",
    "briefing_gate",
    "choice_gate",
    "classify",
    "constant",
    "converge",
    "council",
    "counter",
    "fail",
    "remediate",
    "resolve_unknowns",
    "roundtable",
    "save_text",
    "script_sequence",
    "shell",
    "succeed",
    "tiered",
    "try_shell",
    "validate_mcp",
    "validate_mcps",
    "verdict",
    "voice",
    "wait",
]
