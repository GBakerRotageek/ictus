"""Reusable stages — collections of nodes worth naming as a unit.

Each compiles to its own Conductor workflow file plus a single ``type: workflow``
agent in the parent, so a stage costs the caller one iteration however many
steps it contains, and the dashboard renders it as a nested group.
"""

from __future__ import annotations

from ictus.stdlib.stages.briefing_gate import APPROVE_OR_REJECT, ReviewOption, briefing_gate
from ictus.stdlib.stages.classify import UNCLEAR, Choice, classify
from ictus.stdlib.stages.converge import CONVERGED, EXHAUSTED, Attempt, converge
from ictus.stdlib.stages.council import AGREED, HALTED, UNRESOLVED, Voice, council
from ictus.stdlib.stages.map_stage import map_stage
from ictus.stdlib.stages.poll_until import READY, poll_until
from ictus.stdlib.stages.resolve_unknowns import resolve_unknowns
from ictus.stdlib.stages.roundtable import Speaker, roundtable
from ictus.stdlib.stages.script_sequence import ScriptStep, script_sequence
from ictus.stdlib.stages.tiered import DONE, Tier, tiered
from ictus.stdlib.stages.try_shell import FAILED, OK, try_shell
from ictus.stdlib.stages.validate_mcps import validate_mcps

__all__ = [
    "AGREED",
    "APPROVE_OR_REJECT",
    "CONVERGED",
    "DONE",
    "EXHAUSTED",
    "FAILED",
    "HALTED",
    "OK",
    "READY",
    "UNCLEAR",
    "UNRESOLVED",
    "Attempt",
    "Choice",
    "ReviewOption",
    "ScriptStep",
    "Speaker",
    "Tier",
    "Voice",
    "briefing_gate",
    "classify",
    "converge",
    "council",
    "map_stage",
    "poll_until",
    "resolve_unknowns",
    "roundtable",
    "script_sequence",
    "tiered",
    "try_shell",
    "validate_mcps",
]
