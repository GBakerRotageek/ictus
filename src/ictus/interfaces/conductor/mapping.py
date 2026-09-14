"""Lowering map groups to Conductor's top-level ``for_each:`` list.

Unlike a parallel group — whose members stay ordinary ``agents:`` entries and
are merely *listed* here — a for-each group carries its body inline. The body
must therefore be kept out of ``agents:`` entirely: emitting it in both places
leaves a step that Conductor would schedule once on its own, before the group
ever runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import NodeKind
from ictus.interfaces.conductor.agents import agent_entry, route_entries
from ictus.interfaces.conductor.templates import reference_path

if TYPE_CHECKING:
    from ictus.graph.mapping import MapGroup
    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict, YamlValue

__all__ = ["for_each_block", "mapping_problems"]

# ForEachDef.validate_loop_variable and validate_max_concurrent in
# config/schema.py. These constrain this backend, not the composition model.
_RESERVED = frozenset({"workflow", "context", "output", "_index", "_key"})
_MAX_CONCURRENT = 100

# config/schema.py, the for_each `source` validator: "minimum 3 parts". It is a
# format rule, so it refuses a whole class of sources rather than a typo — a map
# group's aggregate (`group.outputs`) and a single-value `set` step
# (`step.output`) both address an array in two.
_SOURCE_PARTS = 3

# engine/workflow.py:_execute_for_each_group handles set and workflow itself;
# the remaining path invokes the agent executor, not the human-gate handler.
# config/validator.py also rejects script, wait, terminate and questions.
_ITERABLE = frozenset({NodeKind.LLM_CALL, NodeKind.COMPUTATION, NodeKind.SUB_GRAPH})


def mapping_problems(pipeline: Pipeline) -> list[str]:
    """Map constructs this backend cannot lower or Conductor cannot execute."""
    problems: list[str] = []
    for group in pipeline.maps:
        where = f"{pipeline.pipeline_id}: map group {group.group_id!r}"
        if group.item.name in _RESERVED:
            problems.append(
                f"{where}: loop variable {group.item.name!r} is reserved by Conductor "
                f"({', '.join(sorted(_RESERVED))}); choose another name"
            )
        if group.body.kind not in _ITERABLE:
            problems.append(
                f"{where}: the Conductor backend cannot iterate a {group.body.kind.value} "
                f"({group.body.node_id!r}); only model calls and computations are "
                "supported as map bodies. Put this step before or after the group."
            )
        source = reference_path(pipeline, group.source)
        if len(source.split(".")) < _SOURCE_PARTS:
            problems.append(
                f"{where}: maps over {source!r}, which Conductor cannot use as a for_each "
                "source: it needs at least three dotted parts — a step's output field or a "
                f"workflow input — and this is addressed in {len(source.split('.'))}. A map "
                "group's collected array and a single-value set step are both addressed "
                "this way. Republish the array as a field of a step, and map over that."
            )
        if group.max_concurrent > _MAX_CONCURRENT:
            problems.append(
                f"{where}: max_concurrent is {group.max_concurrent}, but Conductor "
                f"permits at most {_MAX_CONCURRENT}; lower the concurrency to batch the items"
            )
    return problems


# The template still needs a `name` — Conductor requires it, and it labels each
# iteration in the dashboard — but it is not a step in the graph, so it has no
# routes of its own. `input_mapping` *does* belong: a stage body is handed a
# fresh context built from it, and the loop variable is already injected by the
# time the engine renders it (`_inject_loop_variables`, then
# `_build_subworkflow_inputs`, both in `_execute_for_each_group`).
_NOT_ON_A_TEMPLATE = ("routes",)


def _bound_mapping(pipeline: Pipeline, group: MapGroup) -> YamlDict:
    """The item's share of a stage body's parameters.

    ``| tojson`` on every one, for the reason ``input_mapping`` spells out: the
    engine parses each rendered entry with ``json.loads``. A field holding the
    string ``"false"`` arrives as a boolean without it, and a boolean field
    arrives as the string ``"True"``.
    """
    return {
        name: "{{ " + reference_path(pipeline, ref) + " | tojson }}"
        for name, ref in group.bind.items()
    }


def for_each_block(pipeline: Pipeline) -> list[YamlValue]:
    """Render every map group the pipeline declares."""
    groups: list[YamlValue] = []
    for group in pipeline.maps:
        template = agent_entry(pipeline, group.body)
        for key in _NOT_ON_A_TEMPLATE:
            template.pop(key, None)
        bound = _bound_mapping(pipeline, group)
        if bound:
            shared = template.get("input_mapping")
            template["input_mapping"] = {**(shared if isinstance(shared, dict) else {}), **bound}
        entry: YamlDict = {
            "name": group.group_id,
            "type": "for_each",
            "source": reference_path(pipeline, group.source),
            "as": group.item.name,
            "agent": template,
            "max_concurrent": group.max_concurrent,
        }
        if group.description:
            entry["description"] = group.description
        if group.failure_mode is not None:
            entry["failure_mode"] = group.failure_mode.value
        if group.key_by is not None:
            entry["key_by"] = f"{group.item.name}.{group.key_by}"
        routes = route_entries(pipeline, group)
        if routes:
            entry["routes"] = routes
        groups.append(entry)
    return groups
