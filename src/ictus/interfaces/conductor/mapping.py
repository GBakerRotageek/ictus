"""Lowering map groups to Conductor's top-level ``for_each:`` list.

Unlike a parallel group — whose members stay ordinary ``agents:`` entries and
are merely *listed* here — a for-each group carries its body inline. The body
must therefore be kept out of ``agents:`` entirely: emitting it in both places
leaves a step that Conductor would schedule once on its own, before the group
ever runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.interfaces.conductor.agents import agent_entry, route_entries
from ictus.interfaces.conductor.templates import reference_path

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict, YamlValue

__all__ = ["for_each_block"]

# The template still needs a `name` — Conductor requires it, and it labels each
# iteration in the dashboard — but it is not a step in the graph, so it has no
# routes of its own and nothing to bind a parent's inputs to.
_NOT_ON_A_TEMPLATE = ("routes", "input_mapping")


def for_each_block(pipeline: Pipeline) -> list[YamlValue]:
    """Render every map group the pipeline declares."""
    groups: list[YamlValue] = []
    for group in pipeline.maps:
        template = agent_entry(pipeline, group.body)
        for key in _NOT_ON_A_TEMPLATE:
            template.pop(key, None)
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
