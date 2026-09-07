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

import json
from typing import TYPE_CHECKING

from ictus.graph.mapping import MapGroup
from ictus.graph.node import Node
from ictus.graph.ports import PortType
from ictus.graph.ref import (
    AtLeast,
    Comparison,
    Every,
    Origin,
    Ref,
    Template,
    TemplatePart,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.pipeline import Pipeline, RouteEnd
    from ictus.graph.values import YamlDict

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
    if ref.origin is not Origin.NODE:
        # A workflow input and a loop item are context roots, not steps. Looking
        # either up among the nodes would find nothing and say so misleadingly.
        return None
    return next((n for n in pipeline.nodes if n.node_id == ref.source_id), None)


_STRUCTURED = frozenset({PortType.OBJECT, PortType.ARRAY})


def reference_path(pipeline: Pipeline, ref: Ref) -> str:
    """How Conductor addresses the value this reference names."""
    if ref.origin is Origin.WORKFLOW_INPUT:
        return f"workflow.input.{ref.source_id}"
    if ref.origin is Origin.LOOP_ITEM:
        # The loop variable is injected as a context root, not as a step's
        # output, so there is no `.output.` in the middle of it.
        return f"{ref.source_id}.{ref.port}" if ref.port else ref.source_id
    mapped = pipeline.map_named(ref.source_id)
    if mapped is not None:
        # A for-each group stores one aggregate under its own name — outputs,
        # errors, count — rather than an `output:` map like a step.
        return f"{ref.source_id}.{ref.port}"
    source = resolve(pipeline, ref)
    if source is None:
        # A forward reference: the node is not in the graph yet, so its own
        # spelling rule cannot be consulted and the port name is the best guess.
        return f"{ref.source_id}.output.{ref.port}"
    # A member's output is addressed through its group. The direct form passes
    # validation and renders empty, which is the worst kind of wrong, so the
    # choice is made here rather than by the author.
    return output_path(pipeline, source, ref.port)


def output_path(pipeline: Pipeline, source: Node | MapGroup, port_name: str) -> str:
    """How Conductor addresses one output port of one node.

    A port whose ``output_ref`` is empty *is* the node's whole output — a
    ``set`` step with a single ``value:`` stores the bare scalar — so the
    trailing dot has to go with it.
    """
    path = source.output_ref(port_name)
    if isinstance(source, MapGroup):
        return f"{source.group_id}.{path}"
    group = pipeline.group_of(source)
    root = (
        f"{group.group_id}.outputs.{source.node_id}"
        if group is not None
        else f"{source.node_id}.output"
    )
    return f"{root}.{path}" if path else root


# Types whose rendered text `_maybe_parse_json` can read back as something else.
# STRING belongs here: a step that produced "0700", "true" or "[a]" has that value
# silently retyped on the way out, which `| tojson` prevents by quoting it.
_RETYPED = frozenset({PortType.STRING, PortType.OBJECT, PortType.ARRAY})


def output_block(pipeline: Pipeline) -> YamlDict:
    """The workflow's final ``output:`` map.

    Rendered here rather than at composition time because addressing a value is
    the backend's rule: a parallel-group member is read through its group and a
    for-each group publishes its aggregate under its own name, neither of which
    the graph layer can know.
    """
    out: YamlDict = {}
    for name, exposed in pipeline.exposed_outputs.items():
        path = output_path(pipeline, exposed.source, exposed.port.name)
        retyped = exposed.port.port_type in _RETYPED
        if retyped:
            path += " | tojson"
        expression = "{{ " + path + " }}"
        if exposed.default is not None:
            # A step the graph may skip is an undefined *root*, and the attribute
            # chain raises before any filter is reached — so the guard, not
            # `| default()`, is what rescues it. Both branches must render the
            # same JSON, or the value comes back a different type depending on
            # which one ran: `| tojson` quotes the live one, so the fallback for
            # a string has to be quoted too.
            root = _root_of(pipeline, exposed.source)
            fallback = (
                json.dumps(exposed.default)
                if exposed.port.port_type is PortType.STRING
                else exposed.default
            )
            expression = _guarded(f"{root} is defined", expression, fallback)
        out[name] = expression
    return out


def _root_of(pipeline: Pipeline, source: Node | MapGroup) -> str:
    """The context name a guard must test for this source."""
    if isinstance(source, Node):
        group = pipeline.group_of(source)
        if group is not None:
            return group.group_id
    return source.node_id


def render(pipeline: Pipeline, node: RouteEnd, value: str | Template) -> str:
    """Render a prompt for ``node``.

    A plain string is literal text and is emitted unchanged; only a ``Template``
    carries references.
    """
    if isinstance(value, str):
        return value
    return "".join(_part(pipeline, node, part, frozenset()) for part in value.parts)


def render_settled(pipeline: Pipeline, node: RouteEnd, value: str | Template) -> str:
    """Render a value whose rendered text Conductor parses back with ``json.loads``.

    ``output_template``, ``input_mapping`` and the workflow ``output:`` map are
    all round-tripped through JSON, so a structured value interpolated bare
    arrives as a Python repr — single quotes, so the parse fails and the value
    survives as a string that looks like data. ``| tojson`` is what makes the
    round trip lossless, and it only applies to a lone reference: a structured
    value spliced into surrounding prose is prose.

    A fallback does not opt the value out of that. It used to: the reference fell
    through to ``| default('[]')``, which both dropped ``| tojson`` from the
    branch that had a value *and* quoted the fallback into a string. What a
    fallback changes is where the empty case comes from, not what type either
    case arrives as — so both branches emit JSON, the same way ``output_block``
    already does for the workflow's own output map.
    """
    if isinstance(value, Template) and len(value.parts) == 1:
        part = value.parts[0]
        if isinstance(part, Ref) and part.port_type in _STRUCTURED:
            live = "{{ " + reference_path(pipeline, part) + " | tojson }}"
            # A reference that needs no guard is always defined, so `default()`
            # could never have fired and there is no branch for a fallback to be.
            if not _needs_guard(pipeline, node, part):
                return live
            # Raw, not quoted: a structured fallback is already a JSON literal —
            # `_EMPTY_FOR` spells the empty ones "{}" and "[]".
            return _guarded(guard_test(pipeline, part), live, part.fallback)
    return render(pipeline, node, value)


def _part(
    pipeline: Pipeline,
    node: RouteEnd,
    part: TemplatePart,
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
        # One spelling per value type, and bool is tested first because it is a
        # subclass of int. `| int` for a number, for AtLeast's reason: the value
        # arrives as whatever `_maybe_parse_json` made of it, and an unfiltered
        # `0 == 0` against a rendered string is false rather than an error.
        # A bool renders Jinja's bare literal — quoted, it would never match the
        # real bool the engine stored. Which spelling is legal for which port is
        # settled at composition by `equals`.
        if isinstance(part.value, bool):
            test = f"{path} {operator} {str(part.value).lower()}"
        elif isinstance(part.value, int):
            test = f"{path} | int {operator} {part.value}"
        else:
            test = f"{path} {operator} '{part.value}'"
        return _condition(pipeline, node, test, (part.ref,))
    if isinstance(part, Every):
        # `not (a and b)` rather than `not a or not b`: one negation to read, and
        # it stays correct however many references the conjunction was built from.
        joined = " and ".join(reference_path(pipeline, r) for r in part.refs_)
        body = f"not ({joined})" if part.negated else joined
        return _condition(pipeline, node, body, part.refs_)
    if isinstance(part, AtLeast):
        path = reference_path(pipeline, part.ref)
        return _condition(pipeline, node, f"{path} | int >= {part.threshold}", (part.ref,))
    if isinstance(part, Ref):
        expression = _expression(reference_path(pipeline, part), part.fallback)
        test = guard_test(pipeline, part)
        if test in guarded or not _needs_guard(pipeline, node, part):
            return expression
        # A bare deferred reference renders empty rather than aborting the step.
        return _guarded(test, expression)
    guards = sorted(
        {guard_test(pipeline, r) for r in part.refs() if _needs_guard(pipeline, node, r)}
    )
    inner_guarded = guarded | frozenset(guards)
    body = "".join(_part(pipeline, node, inner, inner_guarded) for inner in part.parts)
    if not guards:
        return body
    return _guarded(" and ".join(guards), body)


def _condition(pipeline: Pipeline, node: RouteEnd, test: str, refs: Sequence[Ref]) -> str:
    """A route condition, false rather than fatal when it cannot be evaluated.

    Conditions took the reference path directly and never asked whether the step
    behind it had run. A route testing a value from a branch this run did not
    take then raised under strict undefined and killed the whole run, when the
    only sensible reading of "that value is not there" is that the condition does
    not hold. Jinja short-circuits ``and``, so the definedness tests go inline
    rather than wrapping the expression in ``{% if %}``, which would render the
    empty string — falsy, but by accident rather than by construction.
    """
    guards = [guard_test(pipeline, r) for r in refs if _needs_guard(pipeline, node, r)]
    seen = list(dict.fromkeys(guards))
    if not seen:
        return "{{ " + test + " }}"
    return "{{ " + " and ".join([*seen, f"({test})"]) + " }}"


def _expression(path: str, fallback: str | None = None) -> str:
    """Conductor interpolates with Jinja's ``{{ }}``."""
    if fallback is not None:
        return "{{ " + path + " | default('" + fallback.replace("'", "\\'") + "') }}"
    return "{{ " + path + " }}"


def _guarded(condition: str, body: str, otherwise: str | None = None) -> str:
    """Wrap ``body`` so it renders only when ``condition`` holds.

    ``otherwise`` is what the other branch emits. Both branches have to produce
    the same shape wherever the result is parsed back, so the two callers that
    need one build it here rather than each spelling out the block.
    """
    tail = "" if otherwise is None else "{% else %}" + otherwise
    return "{% if " + condition + " %}" + body + tail + "{% endif %}"


def guard_test(pipeline: Pipeline, ref: Ref) -> str:
    """The condition under which this reference is safe to read.

    Usually just the node's name. Where the node says otherwise — a gate's
    free-text field — the trailing segments are tested one at a time, because
    Jinja's strict undefined raises on the *first* missing attribute and
    ``| default()`` never gets to run. Jinja short-circuits ``and``, so the
    chain is safe to evaluate left to right.
    """
    if ref.origin is Origin.WORKFLOW_INPUT:
        # An optional workflow input is always *defined* — the engine binds it to
        # None so templates can test it (engine/context.py) — so `is defined` is
        # true and the block renders the literal "None" into the prompt. What the
        # author means by "optional" is "has a value", which is the truthiness
        # test, and that covers the empty string too.
        return reference_path(pipeline, ref)
    root = guard_variable(pipeline, ref)
    source = resolve(pipeline, ref)
    depth = source.guard_depth(ref.port) if isinstance(source, Node) else 0
    if depth < 1:
        return f"{root} is defined"
    path = reference_path(pipeline, ref).split(".")
    tests = [f"{root} is defined"]
    tests += [".".join(path[: len(path) - i]) + " is defined" for i in range(depth - 1, -1, -1)]
    return " and ".join(dict.fromkeys(tests))


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


def _needs_guard(pipeline: Pipeline, node: RouteEnd, ref: Ref) -> bool:
    """Whether reading this reference has to be guarded.

    Two separate reasons, and only the first used to be checked. A reference can
    be unsafe because its *step* may not have run, and it can be unsafe because
    the *field* is conditional on a branch inside a step that certainly did —
    a gate's free-text answer exists only where an option asked for one. A gate
    that every path crosses is never "deferred", so the second reason has to be
    asked separately or the value is emitted bare and the run dies on the branch
    that answered nothing.
    """
    if _deferred(pipeline, node, ref):
        return True
    if ref.origin is Origin.WORKFLOW_INPUT:
        declared = next((p for p in pipeline.workflow_inputs if p.name == ref.source_id), None)
        return declared is not None and not declared.required
    source = resolve(pipeline, ref)
    return isinstance(source, Node) and source.guard_depth(ref.port) > 0


def _deferred(pipeline: Pipeline, node: RouteEnd, ref: Ref) -> bool:
    """Whether this reference can be read before its source has produced anything."""
    if ref.origin is not Origin.NODE:
        # Both are bound before the step runs: an input by the caller, a loop
        # item by the group that spawned this iteration.
        return False
    source = resolve(pipeline, ref)
    return source is not None and pipeline.may_be_unresolved(source, node)
