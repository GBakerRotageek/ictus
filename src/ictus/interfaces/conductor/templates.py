"""Rendering typed references into Conductor's Jinja dialect.

This is where a ``Ref`` becomes ``{{ plan.output.plan }}``. Two things the
author no longer has to get right:

* the spelling, including a gate's fixed ``additional_input`` shape;
* the guard. Conductor renders with strict undefined, so a reference to a node
  that has not run yet aborts the step. The compiler already knows which
  references are deferred — ``Pipeline.may_be_unresolved`` computes exactly
  that — so it emits ``{% if x is defined %}`` itself rather than relying on
  the author to remember. A live run died on precisely that omission.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import Node
from ictus.graph.ref import Comparison, OptionalBlock, Ref, Template

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline, RouteEnd

__all__ = ["reference_path", "render"]


def resolve(pipeline: Pipeline, ref: Ref) -> Node | None:
    """The node a reference names.

    A forward reference carries no node — it was written before its target
    existed — so it is resolved here, against the finished graph. Without this
    a gate's fixed output shape and the deferral check are both lost, and the
    reference renders to something that looks right and reads nothing.
    """
    if isinstance(ref.source, Node):
        return ref.source
    if ref.from_input:
        return None
    return next((n for n in pipeline.nodes if n.node_id == ref.source_id), None)


def reference_path(pipeline: Pipeline, ref: Ref) -> str:
    """How Conductor addresses the value this reference names."""
    if ref.from_input:
        return f"workflow.input.{ref.source_id}"
    source = resolve(pipeline, ref)
    path = source.output_ref(ref.port) if source is not None else ref.port
    if source is not None:
        group = pipeline.group_of(source)
        if group is not None:
            # A member's output is addressed through its group. The direct form
            # passes validation and renders empty, which is the worst kind of
            # wrong, so the choice is made here rather than by the author.
            return f"{group.group_id}.outputs.{ref.source_id}.{path}"
    return f"{ref.source_id}.output.{path}"


def render(pipeline: Pipeline, node: RouteEnd, value: str | Template) -> str:
    """Render a prompt for ``node``.

    A plain string is literal text and is emitted unchanged; only a ``Template``
    carries references.
    """
    if isinstance(value, str):
        return value
    return "".join(_part(pipeline, node, part, frozenset()) for part in value.parts)


def _part(
    pipeline: Pipeline,
    node: RouteEnd,
    part: str | Ref | Comparison | OptionalBlock | Template,
    guarded: frozenset[str],
) -> str:
    """Render one part. ``guarded`` names sources an enclosing block already covers."""
    if isinstance(part, str):
        return part
    if isinstance(part, Template):
        return "".join(_part(pipeline, node, inner, guarded) for inner in part.parts)
    if isinstance(part, Comparison):
        operator = "!=" if part.negated else "=="
        path = reference_path(pipeline, part.ref)
        return "{{ " + f"{path} {operator} '{part.value}'" + " }}"
    if isinstance(part, Ref):
        expression = _expression(reference_path(pipeline, part), part.fallback)
        variable = guard_variable(pipeline, part)
        if variable in guarded or not _deferred(pipeline, node, part):
            return expression
        # A bare deferred reference renders empty rather than aborting the step.
        return _guarded(f"{variable} is defined", expression)
    guards = sorted(
        {guard_variable(pipeline, r) for r in part.refs() if _deferred(pipeline, node, r)}
    )
    inner_guarded = guarded | frozenset(guards)
    body = "".join(_part(pipeline, node, inner, inner_guarded) for inner in part.parts)
    if not guards:
        return body
    return _guarded(" and ".join(f"{g} is defined" for g in guards), body)


def _expression(path: str, fallback: str | None = None) -> str:
    """Conductor interpolates with Jinja's ``{{ }}``."""
    if fallback is not None:
        return "{{ " + path + " | default('" + fallback.replace("'", "\\'") + "') }}"
    return "{{ " + path + " }}"


def _guarded(condition: str, body: str) -> str:
    """Wrap ``body`` so it renders only when ``condition`` holds."""
    return "{% if " + condition + " %}" + body + "{% endif %}"


def guard_variable(pipeline: Pipeline, ref: Ref) -> str:
    """The context variable a guard must test for this reference.

    For a member of a parallel group that is the *group*: the member's own name
    is not bound in the context at all, so a guard naming it is always false and
    silently swallows the value it was protecting.
    """
    source = resolve(pipeline, ref)
    if isinstance(source, Node):
        group = pipeline.group_of(source)
        if group is not None:
            return group.group_id
    return ref.source_id


def _deferred(pipeline: Pipeline, node: RouteEnd, ref: Ref) -> bool:
    """Whether this reference can be read before its source has produced anything."""
    if ref.from_input:
        return False
    source = resolve(pipeline, ref)
    return source is not None and pipeline.may_be_unresolved(source, node)
