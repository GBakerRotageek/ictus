"""Run a whole stage once per item of an array resolved at run time.

A map body is ordinarily one step, which caps the shape at work a single prompt
can do. A stage body lifts that: "find every repository, then review each one"
is a review with its own gates, its own loop and its own file, run per item.

The engine could always do this. ``_execute_for_each_group`` injects the loop
variable into the iteration's context and *then* renders the sub-workflow's
``input_mapping`` against it, so a child's parameters can name item fields. What
was missing above it was the binding: a child is wired from graph edges, and a
loop item is not a node an edge can start from, so an unbound stage received the
parent's own inputs on every iteration.

This constructor is deliberately two calls. It places the stage with
``Stage.instantiate`` and maps over it with ``Pipeline.map_over``, so there is
no second lowering path to keep in step with the first, and everything that
checks an ordinary stage placement — its contract, its identity, its inherited
settings — checks this one unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ictus.graph.mapping import Item, MapGroup
    from ictus.graph.pipeline import FailureMode, Pipeline
    from ictus.graph.ref import Ref
    from ictus.graph.stage import Stage

__all__ = ["map_stage"]


def map_stage(
    pipeline: Pipeline,
    *,
    group_id: str,
    stage: Stage,
    source: Ref,
    item: Item,
    bind: Mapping[str, Ref],
    expect_items: int,
    node_id: str | None = None,
    description: str = "",
    max_concurrent: int = 10,
    failure_mode: FailureMode | None = None,
    key_by: str | None = None,
) -> MapGroup:
    """Place ``stage`` in ``pipeline`` and run it once per element of ``source``.

    ``bind`` says which of the stage's parameters each item supplies, as
    ``{parameter: item.ref(field)}``. A parameter that is the same every
    iteration — a brief, a target directory — is *not* bound: wire it to
    ``group.body`` with the ordinary ``feed`` or ``connect_input``, exactly as
    you would on any other placement of the stage::

        group = map_stage(p, group_id="reviews", stage=review, source=..., ...)
        p.connect_input(brief, group.body, "brief")

    Supplying one parameter both ways is refused — only one of them can reach
    the child — and a required parameter neither of them supplies is a lint,
    because an unfilled parameter is an error inside the child once per item.

    ``expect_items`` buys iteration budget and caps nothing; a stage body costs
    the parent one iteration per item, and the child's own steps are charged
    against the child's budget rather than this one.

    Returns the group. Its ``outputs`` is one entry per item, each the shape the
    stage exposes, so a later step can read the collected results as an array.
    """
    # The description belongs to the group, which is the step a reader sees in
    # the dashboard. The placement keeps the stage's own, as any other would.
    host = stage.instantiate(pipeline, node_id=node_id)
    return pipeline.map_over(
        group_id,
        source=source,
        item=item,
        body=host,
        expect_items=expect_items,
        description=description,
        max_concurrent=max_concurrent,
        failure_mode=failure_mode,
        key_by=key_by,
        bind=bind,
    )
