"""Generic composition rules — true of any graph, whatever executes it.

Rules that depend on one engine's runtime live with that engine, in
``ictus.interfaces.<engine>.lints``. Keeping them apart is what stops "Conductor
raises here" from quietly becoming "graphs are like this".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import GateNode, SubGraphNode

if TYPE_CHECKING:
    from ictus.graph.node import Node
    from ictus.graph.pipeline import Pipeline

__all__ = ["node_problems", "reference_problems", "stage_contract_problems"]


def reference_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """Resolve every typed reference against the finished graph.

    A reference built with ``ref_to`` names a node that did not exist yet, so
    this is where it gets checked: the node must exist, declare that port, and
    declare it with the type the reference claims. Structural, unlike the
    regular expression this replaced.
    """
    by_id = {n.node_id: n for n in pipeline.nodes}
    declared_inputs = {p.name: p for p in pipeline.workflow_inputs}
    problems: list[str] = []
    refs = [
        *node.prompt_refs(),
        *(r for edge in pipeline.outgoing(node) for r in edge.condition_refs()),
    ]
    for ref in refs:
        if ref.from_input:
            param = declared_inputs.get(ref.source_id)
            if param is None:
                known = ", ".join(sorted(declared_inputs)) or "(none)"
                problems.append(
                    f"{where}: agent {node.node_id!r} references pipeline input "
                    f"{ref.source_id!r}, which is not declared; declared: {known}"
                )
            elif param.port_type is not ref.port_type:
                problems.append(
                    f"{where}: agent {node.node_id!r} reads input {ref.source_id!r} as "
                    f"{ref.port_type.value} but it is declared {param.port_type.value}"
                )
            continue
        target = by_id.get(ref.source_id)
        if target is None:
            known = ", ".join(sorted(by_id)) or "(none)"
            problems.append(
                f"{where}: agent {node.node_id!r} references unknown node "
                f"{ref.source_id!r}; nodes in this pipeline: {known}"
            )
            continue
        declared = {p.name: p for p in target.outputs}
        port = declared.get(ref.port)
        if port is None:
            known = ", ".join(sorted(declared)) or "(none declared)"
            problems.append(
                f"{where}: agent {node.node_id!r} references {ref.source_id}.{ref.port}, "
                f"which {ref.source_id!r} does not declare; declared outputs: {known}"
            )
        elif port.port_type is not ref.port_type:
            problems.append(
                f"{where}: agent {node.node_id!r} reads {ref.source_id}.{ref.port} as "
                f"{ref.port_type.value} but it is declared {port.port_type.value}"
            )
    return problems


def node_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """Problems with one node's place in the graph."""
    problems: list[str] = []
    edges = pipeline.outgoing(node)
    in_group = pipeline.group_of(node) is not None

    if edges and not isinstance(node, GateNode) and all(e.when is not None for e in edges):
        problems.append(
            f"{where}: agent {node.node_id!r} has only conditional routes; if none match, "
            "execution has nowhere to go. Add an unconditional route (or route to END)."
        )
    elif not edges and node.accepts_routes and not in_group:
        # A member of a parallel group routes as part of the group and correctly
        # has no edge of its own; Conductor rejects one that does.
        problems.append(
            f"{where}: agent {node.node_id!r} has no outgoing route, so it implicitly ends the "
            "run — indistinguishable from a forgotten edge. Use a TerminateNode or route to END."
        )

    problems.extend(reference_problems(pipeline, node, where))

    fed = {d.connection.target.name for d in pipeline.deps_into(node)}
    fed |= {port.name for _, target, port in pipeline.input_bindings if target is node}
    problems.extend(
        f"{where}: agent {node.node_id!r} declares required input {port.name!r} "
        "but nothing is wired to it"
        for port in node.inputs
        if not port.optional and port.name not in fed
    )
    return problems


def stage_contract_problems(where: str, host: SubGraphNode, child: Pipeline) -> list[str]:
    """Cross-check a stage's boundary against the workflow it hosts.

    No engine seen so far compares these two sides, and the drift is silent:
    declaring a new required input on a stage body leaves every existing
    placement of it stale.
    """
    problems: list[str] = []
    declared = {p.name: p for p in child.declared_input_ports}
    supplied = {p.name: p for p in host.inputs}

    for name in sorted(set(supplied) - set(declared)):
        known = ", ".join(sorted(declared)) or "(none)"
        problems.append(
            f"{where}: stage {host.node_id!r} maps input {name!r}, which workflow "
            f"{child.pipeline_id!r} does not declare; declared inputs: {known}"
        )
    problems.extend(
        f"{where}: stage {host.node_id!r} leaves required input {name!r} of workflow "
        f"{child.pipeline_id!r} unmapped"
        for name in sorted(set(declared) - set(supplied))
        if not declared[name].optional
    )
    problems.extend(
        f"{where}: stage {host.node_id!r} binds {name!r} as {supplied[name].port_type.value} "
        f"but workflow {child.pipeline_id!r} declares {declared[name].port_type.value}"
        for name in sorted(set(declared) & set(supplied))
        if declared[name].port_type is not supplied[name].port_type
    )

    exposed = {p.name for p in child.exposed_output_ports}
    problems.extend(
        f"{where}: stage {host.node_id!r} reads output {port.name!r}, which workflow "
        f"{child.pipeline_id!r} does not expose; exposed outputs: "
        f"{', '.join(sorted(exposed)) or '(none)'}"
        for port in host.outputs
        if port.name not in exposed
    )
    return problems
