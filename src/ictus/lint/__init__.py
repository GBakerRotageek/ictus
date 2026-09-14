"""Composition lints.

Two tiers, deliberately separate:

* the rules in ``rules.py`` are true of any graph — an unreachable node, a
  required input nothing feeds, a stage whose contract has drifted;
* engine-specific rules come from the backend, because "this raises at run time"
  is a claim about one runtime, not about graphs.

Both tiers exist because the executor's own validator cannot see them. For
Conductor, each rule here was checked against the installed validator and
confirmed to pass it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError, LintError
from ictus.graph.node import SubGraphNode
from ictus.lint.rules import (
    describe,
    group_condition_problems,
    group_routing_problems,
    map_binding_problems,
    map_source_problems,
    node_problems,
    placeholder_problems,
    previous_pass_problems,
    stage_contract_problems,
)

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline, RouteEnd
    from ictus.interfaces import Backend

__all__ = ["check", "lint_pipeline"]


def lint_pipeline(
    pipeline: Pipeline,
    *,
    backend: Backend | None = None,
    _seen: set[Pipeline] | None = None,
    _names: dict[str, Pipeline] | None = None,
) -> list[str]:
    """Every violation in ``pipeline`` and its nested stages.

    Pass ``backend`` to add that engine's own rules. Without one you get the
    graph-level rules only, which is the right default for a unit test that has
    no opinion about where the pipeline will run.
    """
    seen = _seen if _seen is not None else set()
    if pipeline in seen:
        return []
    seen.add(pipeline)

    where = pipeline.pipeline_id
    names = _names if _names is not None else {}
    previous = names.setdefault(where, pipeline)
    problems: list[str] = []
    if previous is not pipeline:
        problems.append(
            f"{where}: pipeline_id {where!r} names distinct bodies; "
            "give each stage a unique stage_id or reuse the same Stage instance"
        )
    if not pipeline.nodes:
        return [*problems, f"{where}: pipeline has no nodes"]

    try:
        entry = pipeline.entry()
    except CompositionError as exc:
        return [*problems, f"{where}: {exc}"]

    problems.extend(placeholder_problems(pipeline, where))
    problems.extend(previous_pass_problems(pipeline, where))
    reachable = pipeline.reachable_from_entry()
    problems.extend(
        f"{where}: {describe(node)} is unreachable from entry point "
        f"{entry.node_id!r} and will never run"
        for node in pipeline.nodes
        if node.node_id not in reachable
    )
    for node in pipeline.nodes:
        problems.extend(node_problems(pipeline, node, where))
    collections: tuple[RouteEnd, ...] = (*pipeline.groups, *pipeline.maps)
    for group in collections:
        problems.extend(group_routing_problems(pipeline, group, where))
        problems.extend(group_condition_problems(pipeline, group, where))
    for mapped in pipeline.maps:
        problems.extend(map_source_problems(pipeline, mapped, where))
        problems.extend(map_binding_problems(pipeline, mapped, where))

    by_id = {n.node_id: n for n in pipeline.nodes}
    for host_id, child in pipeline.children.items():
        host = by_id.get(host_id)
        if isinstance(host, SubGraphNode):
            problems.extend(stage_contract_problems(where, host, child))
        problems.extend(lint_pipeline(child, backend=backend, _seen=seen, _names=names))

    if backend is not None:
        problems.extend(backend.lint(pipeline))
    return problems


def check(pipeline: Pipeline, *, backend: Backend | None = None) -> None:
    """Raise ``LintError`` if ``pipeline`` violates any composition rule."""
    problems = lint_pipeline(pipeline, backend=backend)
    if problems:
        raise LintError(problems)
