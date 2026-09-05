"""Rules that are true because of how Conductor runs, not how graphs are shaped.

Each was checked against the installed validator and confirmed to pass it, so
none of them duplicates ``conductor validate``. They live here rather than in
``ictus.lint`` because every one of them is a claim about Conductor's runtime:
its template dialect, its strict-undefined rendering, its output wrapper.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ictus.graph.node import GateNode

if TYPE_CHECKING:
    from ictus.graph.node import Node
    from ictus.graph.pipeline import Pipeline

__all__ = ["conductor_problems"]

# Conductor's template namespace. Its own validator checks the agent segment of
# a reference and stops there, so a typo in the field name survives validation.
# ``\.output`` must not swallow the ``\.outputs`` of a parallel group — doing so
# reports the group as an unknown agent and hides whatever the reference meant.
OUTPUT_REF = re.compile(
    r"\b([a-z_][a-z0-9_]*)\.output(?!s)(?:\.([a-z_][a-z0-9_.]*))?", re.IGNORECASE
)

# A group's results are addressed through the group: ``group.outputs.member.field``.
GROUP_REF = re.compile(
    r"\b([a-z_][a-z0-9_]*)\.(?:outputs|errors)(?:\.([a-z_][a-z0-9_]*))?", re.IGNORECASE
)
INPUT_REF = re.compile(r"\bworkflow\.input\.([a-z_][a-z0-9_]*)", re.IGNORECASE)

# Conductor wraps a step's result under ``.output.``; a field with one of these
# names collides with the wrapper and reads back empty.
RESERVED_OUTPUT_NAMES = frozenset({"outputs", "errors", "output"})


def conductor_problems(pipeline: Pipeline) -> list[str]:
    """Every Conductor-specific violation in one pipeline (not its children)."""
    where = pipeline.pipeline_id
    by_id = {n.node_id: n for n in pipeline.nodes}
    declared_inputs = {p.name for p in pipeline.workflow_inputs}
    problems: list[str] = []

    for node in pipeline.nodes:
        problems.extend(_deferred_reference_problems(pipeline, node, where))
        problems.extend(_template_problems(node, by_id, declared_inputs, where))
        problems.extend(_group_reference_problems(pipeline, node, where))
        problems.extend(
            f"{where}: agent {node.node_id!r} declares output {port.name!r}, which collides "
            "with Conductor's output wrapper and reads back empty"
            for port in node.outputs
            if port.name in RESERVED_OUTPUT_NAMES
        )

    if pipeline.context_mode == "explicit":
        for node in pipeline.nodes:
            problems.extend(_undeclared_reference_problems(pipeline, node, where))

    problems.extend(
        f"{where}: exposed output {name!r} collides with Conductor's output wrapper and will "
        "read back empty; rename it"
        for name in pipeline.exposed_outputs
        if name in RESERVED_OUTPUT_NAMES
    )
    return problems


def _undeclared_reference_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """Under ``context.mode: explicit`` a node sees only what its ``input:`` names.

    Referencing anything else is an undefined variable at render time. That is
    late: a gate's terminal step failed this way *after* the human had answered,
    losing the run. Conductor cannot catch it — the reference is well-formed and
    the agent exists — so it has to be caught here.
    """
    referenced: set[str] = set()
    for ref in node.prompt_refs():
        if not ref.from_input:
            referenced.add(ref.source_id)
    for edge in pipeline.outgoing(node):
        for ref in edge.condition_refs():
            if not ref.from_input:
                referenced.add(ref.source_id)
    for template in node.template_strings():
        referenced.update(name for name, _ in OUTPUT_REF.findall(template))
        referenced.update(name for name, _ in GROUP_REF.findall(template))

    declared: set[str] = {node.node_id}
    for dep in pipeline.deps_into(node):
        declared.add(dep.source.node_id)
        group = pipeline.group_of(dep.source)
        if group is not None:
            # A member is addressed through its group, so either name resolves.
            declared.add(group.group_id)
    known = {n.node_id for n in pipeline.nodes} | {g.group_id for g in pipeline.groups}

    return [
        f"{where}: agent {node.node_id!r} references {name!r} but does not declare it as an "
        f"input. Under context.mode 'explicit' it will not be in scope, and the template "
        f"fails at run time with \"'{name}' is undefined\". Wire it with feed() or connect()."
        for name in sorted(referenced - declared)
        if name in known
    ]


def _deferred_reference_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """A reference to a node that may not have run yet must be guarded.

    The ``?`` suffix makes the *dependency* optional. It does not make the
    template variable defined, and Conductor renders with strict undefined, so
    the first pass through a loop dies on ``'<node>' is undefined``.
    """
    problems: list[str] = []
    for dep in pipeline.deps_into(node):
        deferred = dep.connection.target.optional or pipeline.may_be_unresolved(dep.source, node)
        if not deferred:
            continue
        source_id = dep.source.node_id
        for template in node.template_strings():
            if f"{source_id}." not in template or f"{source_id} is defined" in template:
                continue
            problems.append(
                f"{where}: agent {node.node_id!r} references {source_id!r} in a template, but "
                f"{source_id!r} may not have run yet (the dependency is optional). Guard it "
                f"with `{{% if {source_id} is defined %}}` or the first pass fails with "
                f"\"'{source_id}' is undefined\"."
            )
            break
    return problems


def _group_reference_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """Check references addressed through a parallel group."""
    groups = {g.group_id: g for g in pipeline.groups}
    problems: list[str] = []
    for template in node.template_strings():
        for group_name, member in GROUP_REF.findall(template):
            group = groups.get(group_name)
            if group is None:
                known = ", ".join(sorted(groups)) or "(none)"
                problems.append(
                    f"{where}: agent {node.node_id!r} reads {group_name}.outputs, but "
                    f"{group_name!r} is not a parallel group; groups here: {known}"
                )
                continue
            if member and member not in {m.node_id for m in group.members}:
                known = ", ".join(sorted(m.node_id for m in group.members))
                problems.append(
                    f"{where}: agent {node.node_id!r} reads {group_name}.outputs.{member}, "
                    f"but {member!r} is not in that group; members: {known}"
                )
    return problems


def _template_problems(
    node: Node, by_id: dict[str, Node], declared_inputs: set[str], where: str
) -> list[str]:
    """Check the field segment of every reference, which Conductor never does."""
    problems: list[str] = []
    for template in node.template_strings():
        for ref_node, ref_field in OUTPUT_REF.findall(template):
            target = by_id.get(ref_node)
            if target is None:
                problems.append(
                    f"{where}: agent {node.node_id!r} references unknown agent {ref_node!r}"
                )
                continue
            if not ref_field:
                continue
            head = ref_field.split(".")[0]
            if isinstance(target, GateNode) and head == "additional_input":
                continue
            if head not in {p.name for p in target.outputs}:
                known = ", ".join(p.name for p in target.outputs) or "(none declared)"
                problems.append(
                    f"{where}: agent {node.node_id!r} references {ref_node}.output.{head}, "
                    f"which {ref_node!r} does not declare; declared outputs: {known}"
                )
        problems.extend(
            f"{where}: agent {node.node_id!r} references workflow input {name!r}, which is not "
            f"declared; declared inputs: {', '.join(sorted(declared_inputs)) or '(none declared)'}"
            for name in INPUT_REF.findall(template)
            if name not in declared_inputs
        )
    return problems
