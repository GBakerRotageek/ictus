"""Lowering parallel groups to Conductor's top-level ``parallel:`` list.

Members stay ordinary entries in ``agents:`` and are *listed* by name here.
They must not carry routes of their own — Conductor rejects that — so the
emitter suppresses them rather than trusting the author to leave them off.

Which *kinds* may be members is a fact about this engine too, so it is checked
here rather than where the group is composed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import NodeKind
from ictus.interfaces.conductor.agents import route_entries

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict, YamlValue

__all__ = ["parallel_block", "parallel_problems"]

# config/validator.py:746-786, one rule per kind: a human gate, a questions
# step, a script, a wait, a sub-workflow and a terminate step are each refused
# as a member. Verified by emitting a group holding a gate and running
# `conductor validate` on it, which is also why nothing here re-derives the
# reason — the engine gives one.
_GROUPABLE = frozenset({NodeKind.LLM_CALL, NodeKind.COMPUTATION})


def parallel_problems(pipeline: Pipeline) -> list[str]:
    """Members this backend cannot schedule together.

    ``conductor validate`` refuses every one of these for itself, so what this
    adds is *when*: ``ictus lint`` reports it with nothing emitted, and
    ``ictus emit`` refuses rather than writing YAML into a committed ``build/``
    that only a later ``validate`` would reject.
    """
    return [
        f"{pipeline.pipeline_id}: parallel group {group.group_id!r} holds "
        f"{member.node_id!r}, a {member.kind.value}, which cannot run inside one — "
        "Conductor permits only model calls and computations as members. Put it "
        "before or after the group."
        for group in pipeline.groups
        for member in group.members
        if member.kind not in _GROUPABLE
    ]


def parallel_block(pipeline: Pipeline) -> list[YamlValue]:
    """Render every parallel group the pipeline declares."""
    groups: list[YamlValue] = []
    for group in pipeline.groups:
        entry: YamlDict = {
            "name": group.group_id,
            "agents": [member.node_id for member in group.members],
            "failure_mode": group.failure_mode.value,
        }
        if group.description:
            entry["description"] = group.description
        routes = route_entries(pipeline, group)
        if routes:
            entry["routes"] = routes
        groups.append(entry)
    return groups
