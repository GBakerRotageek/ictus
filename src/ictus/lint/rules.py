"""Generic composition rules — true of any graph, whatever executes it.

Rules that depend on one engine's runtime live with that engine, in
``ictus.interfaces.<engine>.lints``. Keeping them apart is what stops "Conductor
raises here" from quietly becoming "graphs are like this".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import GateNode, NodeKind, ScopeNode, SubGraphNode
from ictus.graph.ref import Origin

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ictus.graph.mapping import MapGroup
    from ictus.graph.node import Node
    from ictus.graph.pipeline import Pipeline, RouteEnd
    from ictus.graph.ports import PortType
    from ictus.graph.ref import Ref

# Conductor carries an abandoned question set on `abort_route`, not in `routes:`.
ABORT_CASE = "__abort__"

# What to call a node in a violation. `NodeKind`'s own values name the work for a
# backend to map onto its vocabulary; a person reading a lint wants the word they
# typed. Every rule here used to say "agent", so a script's unwired input, a
# dead-ended gate and a stage's drifted contract all reported as agent problems.
_KIND_NAMES = {
    NodeKind.LLM_CALL: "agent",
    NodeKind.HUMAN_DECISION: "gate",
    NodeKind.SUBPROCESS: "script",
    NodeKind.COMPUTATION: "compute node",
    NodeKind.DELAY: "wait",
    NodeKind.EXIT: "terminal",
    NodeKind.SUB_GRAPH: "stage",
    NodeKind.ASK: "questions",
}


def describe(node: Node) -> str:
    """How a violation names one node: what it is, then which one."""
    if isinstance(node, ScopeNode):
        # A scope is a stage with a closed outcome vocabulary, and the rules
        # treat the two differently — so the message has to as well.
        return f"scope {node.node_id!r}"
    return f"{_KIND_NAMES.get(node.kind, node.kind.value)} {node.node_id!r}"


PLACEHOLDER = "CHANGE-ME"
"""What `ictus init` writes where a decision has to be made.

A graph still carrying it has not been authored yet, and `ictus run` on one is a
billable call against a placeholder. Caught as a lint rather than at the
scaffold, because the folder is meant to be unfinished right after `init` — it
is running it that is the mistake.
"""

__all__ = [
    "describe",
    "group_condition_problems",
    "group_routing_problems",
    "map_binding_problems",
    "map_source_problems",
    "node_problems",
    "placeholder_problems",
    "previous_pass_problems",
    "reference_problems",
    "stage_contract_problems",
]


def reference_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """Resolve every typed reference against the finished graph.

    A reference built with ``ref_to`` names a node that did not exist yet, so
    this is where it gets checked: the node must exist, declare that port, and
    declare it with the type the reference claims. Structural, unlike the
    regular expression this replaced.

    Resolution is by name *and* identity wherever the reference kept a source.
    Names are only unique within one graph, so a reference carried in from
    another one resolves here to whatever local node, input or group shares the
    name — and every layer below agrees, because the emitted template says the
    same word. Checking the name alone made that the one wrong reference nothing
    could catch.
    """
    problems: list[str] = []
    refs = [
        *node.prompt_refs(),
        *node.settled_refs(),
        *node.path_refs(),
        *(r for edge in pipeline.outgoing(node) for r in edge.condition_refs()),
    ]
    for ref in refs:
        if ref.origin is Origin.LOOP_ITEM:
            problems.extend(_item_reference_problems(pipeline, node, ref, where))
            continue
        problems.extend(_resolution_problems(pipeline, describe(node), ref, where))
    return problems


def _resolution_problems(pipeline: Pipeline, subject: str, ref: Ref, where: str) -> list[str]:
    """Resolve one reference to a node, input or map group of this pipeline.

    ``subject`` is whatever is doing the reading, as a violation names it. It is
    a string rather than a node because not every reader is one: a map group's
    source is a reference too, and it went unchecked for exactly that reason.
    """
    # Origin before name. An input and a map group live in different address
    # spaces — `workflow.input.items` and a group called `items` coexist — so
    # looking the name up as a group first resolved an input to whichever group
    # shared it, and failed an identity check that was never about that group.
    if ref.from_input:
        declared_inputs = {p.name: p for p in pipeline.workflow_inputs}
        param = declared_inputs.get(ref.source_id)
        if param is None:
            known = ", ".join(sorted(declared_inputs)) or "(none)"
            return [
                f"{where}: {subject} references pipeline input "
                f"{ref.source_id!r}, which is not declared; declared: {known}"
            ]
        if _is_elsewhere(ref, param):
            return [
                f"{where}: {subject} reads input {ref.source_id!r}, but that is "
                "a different pipeline input from the one this reference was built from. "
                f"Every workflow addresses its own parameters, so this reads {where}'s "
                f"{ref.source_id!r} and not the one it was written against. Reference "
                "the input declared here, and pass the value in across the boundary."
            ]
        if param.port_type is not ref.port_type:
            return [
                f"{where}: {subject} reads input {ref.source_id!r} as "
                f"{ref.port_type.value} but it is declared {param.port_type.value}"
            ]
        return []
    maps = {m.group_id: m for m in pipeline.maps}
    if ref.source_id in maps:
        group = maps[ref.source_id]
        if _is_elsewhere(ref, group):
            return [
                f"{where}: {subject} reads {ref.source_id}.{ref.port}, but "
                f"{ref.source_id!r} here is a different map group from the one this "
                "reference was built from, so it would read this one's aggregate "
                "instead. Reference the group this pipeline declares."
            ]
        return _map_reference_problems(group, subject, ref, where)
    by_id = {n.node_id: n for n in pipeline.nodes}
    target = by_id.get(ref.source_id)
    if target is None:
        known = ", ".join(sorted(by_id)) or "(none)"
        return [
            f"{where}: {subject} references unknown node "
            f"{ref.source_id!r}; nodes in this pipeline: {known}"
        ]
    if _is_elsewhere(ref, target):
        return [
            f"{where}: {subject} references {ref.source_id}.{ref.port}, but "
            f"{ref.source_id!r} here is a different node from the one this reference "
            f"was built from, so it would read {describe(target)} instead. "
            "Reference the node this pipeline holds, or wire the value in as an input."
        ]
    mapped = pipeline.map_of(target)
    if mapped is not None:
        # `feed` refuses this where it is written; a reference cannot be, because
        # it is made before the node it names is placed in a group.
        return [
            f"{where}: {subject} references {ref.source_id}.{ref.port}, but "
            f"{ref.source_id!r} is the body of map group {mapped.group_id!r}. It runs "
            "once per item and nothing is stored under its own name — the engine keeps "
            f"only the aggregate — so this resolves to nothing. Read "
            f"{mapped.group_id}.outputs instead."
        ]
    declared = {p.name: p for p in target.outputs}
    port = declared.get(ref.port)
    if port is None:
        known = ", ".join(sorted(declared)) or "(none declared)"
        return [
            f"{where}: {subject} references {ref.source_id}.{ref.port}, "
            f"which {ref.source_id!r} does not declare; declared outputs: {known}"
        ]
    if port.port_type is not ref.port_type:
        return [
            f"{where}: {subject} reads {ref.source_id}.{ref.port} as "
            f"{ref.port_type.value} but it is declared {port.port_type.value}"
        ]
    return []


def map_source_problems(pipeline: Pipeline, group: MapGroup, where: str) -> list[str]:
    """Resolve the array a map group iterates, and the shape its items are read at.

    Two failures, and the second one only exists because the first is checked.
    The source is a reference like any other and resolves the same way — but a
    map group is not a node, so ``reference_problems`` never saw it, and a
    foreign ``split.pieces`` compiled against whichever local ``split`` shared
    the name. Past that, the *item fields* are checked against the reference's
    ``element``, not the port's; a reference that claims a shape the array does
    not have passes composition with fields no item carries.
    """
    source = group.source
    if source.origin is Origin.LOOP_ITEM:
        return []  # refused where the group was written; nothing further resolves
    subject = f"map group {group.group_id!r}"
    problems = _resolution_problems(pipeline, subject, source, where)
    if problems:
        return problems
    produced = _produced_element(pipeline, source)
    if dict(produced or {}) != dict(source.element or {}):
        return [
            f"{where}: {subject} reads each item of {source.source_id}.{source.port} as "
            f"{_shape(source.element)}, but that array's element shape is "
            f"{_shape(produced)}. The item fields are checked against the reference, so "
            "they now name fields no item carries. Reference the port itself."
        ]
    return []


def _produced_element(pipeline: Pipeline, ref: Ref) -> Mapping[str, PortType] | None:
    """The element shape of the array ``ref`` resolves to, once it has resolved.

    A workflow input declares none — its items are whatever the caller passed.
    """
    if ref.from_input:
        return None
    group = pipeline.map_named(ref.source_id)
    if group is not None:
        return group.get_output(ref.port).element
    return (
        next(n for n in pipeline.nodes if n.node_id == ref.source_id).get_output(ref.port).element
    )


def _shape(element: Mapping[str, PortType] | None) -> str:
    if not element:
        return "(no declared fields)"
    return "{" + ", ".join(f"{name}: {kind.value}" for name, kind in sorted(element.items())) + "}"


def _item_reference_problems(pipeline: Pipeline, node: Node, ref: Ref, where: str) -> list[str]:
    """A read of a loop variable, checked against the group that injects one.

    ``Item.ref`` checked the field when the reference was written, which settles
    everything except *which* item it came from — and the loop variable is
    almost always called ``item`` or ``piece``, so a graph with two fan-outs has
    two of them with the same name and identical fields. The wrong one renders
    the right word against the wrong array, on every iteration.
    """
    group = pipeline.map_of(node)
    if group is None:
        return [
            f"{where}: {describe(node)} reads loop item {ref.source_id!r}, but it is not the "
            "body of a map group, so nothing injects one and the template fails at run time "
            f"with \"'{ref.source_id}' is undefined\". Only a map body reads an item; a "
            "stage run per item receives its values as declared parameters."
        ]
    if _is_elsewhere(ref, group.item):
        return [
            f"{where}: {describe(node)} reads a field of a different loop item that is also "
            f"called {ref.source_id!r}. Items are compared by identity, so the group would "
            "inject its own and this reference means the other one. Use the Item this group "
            "iterates with."
        ]
    return []


def map_binding_problems(pipeline: Pipeline, group: MapGroup, where: str) -> list[str]:
    """Prove every parameter of a mapped body actually receives a value.

    Nothing else does. ``node_problems`` skips a map body entirely — it is not a
    step of the graph — and ``stage_contract_problems`` compares the two
    *contracts*, which a stage placed as a map body satisfies while receiving
    nothing, because its values come from two places and neither is a port
    declaration. A required parameter nothing supplies is an error inside the
    child, once per item, after the array has been paid for.
    """
    body = group.body
    wired = pipeline.wired_inputs(body)
    both = sorted(set(group.bind) & wired)
    problems = [
        f"{where}: map group {group.group_id!r} runs {body.node_id!r} once per item, and its "
        f"parameter {name!r} is supplied twice — bound to a field of the item, and wired by "
        "an edge. Only one of them reaches the child; drop whichever is not meant."
        for name in both
    ]
    supplied = set(group.bind) | wired
    problems.extend(
        f"{where}: map group {group.group_id!r} runs {body.node_id!r} once per item, but "
        f"nothing supplies its required parameter {port.name!r}. Bind it to a field of the "
        "item, or wire a shared value with feed() or connect_input()."
        for port in body.inputs
        if not port.optional and port.name not in supplied
    )
    return problems


def _is_elsewhere(ref: Ref, found: object) -> bool:
    """Whether ``ref`` names ``found`` but was built from something else.

    ``ref_to`` leaves no source — a forward reference has nothing to point at —
    so a missing one is not a mismatch, only an unchecked name.
    """
    return ref.source is not None and ref.source is not found


def _map_reference_problems(group: MapGroup, subject: str, ref: Ref, where: str) -> list[str]:
    """A reference to a map group's aggregate — outputs, errors or count."""
    try:
        port = group.get_output(ref.port)
    except CompositionError as exc:
        return [f"{where}: {subject} {exc}"]
    if port.port_type is not ref.port_type:
        return [
            f"{where}: {subject} reads {group.group_id}.{ref.port} as "
            f"{ref.port_type.value} but a map group produces {port.port_type.value}"
        ]
    return []


def group_routing_problems(pipeline: Pipeline, group: RouteEnd, where: str) -> list[str]:
    """A group routes like a step, so it can dead-end like one.

    Only nodes were ever linted, and a group is not a node — so a parallel or map
    group with nothing but conditional routes passed every check and then hit
    ``ValueError: No matching route found`` (engine/router.py:109) at run time,
    after every member had already been paid for.
    """
    edges = pipeline.outgoing(group)
    if not edges:
        return [
            f"{where}: group {group.node_id!r} has no outgoing route, so the run stops "
            "there — indistinguishable from a forgotten edge. Route it to a step or END."
        ]
    if all(e.when is not None for e in edges):
        return [
            f"{where}: group {group.node_id!r} has only conditional routes; if none match, "
            "execution has nowhere to go. Add an unconditional route (or route to END)."
        ]
    return []


def group_condition_problems(pipeline: Pipeline, group: RouteEnd, where: str) -> list[str]:
    """Resolve what a group's route conditions read, as a step's are resolved.

    ``reference_problems`` walks a *node's* outgoing edges, and a parallel or map
    group is not a node, so its conditions were never resolved at all. A
    condition built from another graph's ``flag`` beside a local ``flag`` linted
    clean, and on a live run the group took whichever branch the local one chose.
    """
    subject = f"group {group.node_id!r}"
    problems: list[str] = []
    for edge in pipeline.outgoing(group):
        for ref in edge.condition_refs():
            if ref.origin is Origin.LOOP_ITEM:
                problems.append(
                    f"{where}: {subject} routes on loop item {ref.source_id!r}, but a group "
                    "routes once, after every item has finished, where no item exists — the "
                    "condition fails at run time. Route on the group's aggregate instead."
                )
                continue
            problems.extend(_resolution_problems(pipeline, subject, ref, where))
    return problems


def placeholder_problems(pipeline: Pipeline, where: str) -> list[str]:
    """Scaffold text left where a decision was supposed to go."""
    problems: list[str] = []
    if PLACEHOLDER in pipeline.pipeline_id:
        problems.append(
            f"{where}: pipeline_id is still {pipeline.pipeline_id!r}. Name the pipeline "
            "before running it — every file it emits is named after this."
        )
    for node in pipeline.nodes:
        if PLACEHOLDER in node.node_id:
            problems.append(f"{where}: {describe(node)} still carries {PLACEHOLDER} in its id")
        if any(PLACEHOLDER in text for text in node.template_strings()):
            problems.append(
                f"{where}: {describe(node)} has {PLACEHOLDER} in its prompt, so the model "
                "would be paid to act on the placeholder"
            )
    return problems


def previous_pass_problems(pipeline: Pipeline, where: str) -> list[str]:
    """A read of the last pass, on a graph that never takes a second one.

    ``feed(..., previous_pass=True)`` is how a member of a parallel group reads a
    sibling: the engine keys the group's result by the group's name and
    overwrites it only when the group next finishes, so a second pass sees the
    first. With no loop there is no first — the reference renders empty, every
    round, and a council wired this way would look like it was deliberating.
    """
    if pipeline.has_cycle():
        return []
    return [
        f"{where}: {dep.target.node_id!r} reads {dep.source.node_id!r} with "
        "previous_pass=True, but this graph has no loop, so there is never a previous "
        "pass and the reference renders empty every time. Drop the flag and put the "
        "reader after the group, or give the graph the loop it was written for."
        for dep in pipeline.data_deps
        if dep.previous_pass
    ]


def node_problems(pipeline: Pipeline, node: Node, where: str) -> list[str]:
    """Problems with one node's place in the graph."""
    problems: list[str] = []
    edges = pipeline.outgoing(node)
    # A map body is not a step of the graph: it is reached by the group that
    # spawns it, has no routes of its own, and is never emitted in `agents:`.
    # Every rule about edges is therefore silent about it.
    if pipeline.map_of(node) is not None:
        return reference_problems(pipeline, node, where)
    in_group = pipeline.group_of(node) is not None

    # A scope is the one node whose conditional routes are provably exhaustive:
    # its outcome vocabulary is closed, `branch_on_outcome` refuses to leave a
    # member unrouted, and outcome names that a JSON parse would turn into
    # non-strings are rejected at composition. Nothing else can reach the port.
    exhaustive = isinstance(node, ScopeNode) and {e.case for e in edges} >= set(node.outcomes)
    routed = [e for e in edges if e.case != ABORT_CASE]
    if (
        edges
        and routed
        and not isinstance(node, GateNode)
        and not exhaustive
        and all(e.when is not None for e in routed)
    ):
        problems.append(
            f"{where}: {describe(node)} has only conditional routes once its abort "
            "edge is set aside. An abort is emitted as `abort_route`, not in `routes:`, so "
            "it is not the fallback the answered path needs."
        )
    elif not edges and node.accepts_routes and not in_group:
        # A member of a parallel group routes as part of the group and correctly
        # has no edge of its own; Conductor rejects one that does.
        problems.append(
            f"{where}: {describe(node)} has no outgoing route, so it implicitly ends the "
            "run — indistinguishable from a forgotten edge. Use a TerminateNode or route to END."
        )

    problems.extend(reference_problems(pipeline, node, where))

    fed = pipeline.wired_inputs(node)
    problems.extend(
        f"{where}: {describe(node)} declares required input {port.name!r} "
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

    exposed = set(child.output_contract_names)
    problems.extend(
        f"{where}: stage {host.node_id!r} reads output {port.name!r}, which workflow "
        f"{child.pipeline_id!r} does not expose; exposed outputs: "
        f"{', '.join(sorted(exposed)) or '(none)'}"
        for port in host.outputs
        if port.name not in exposed
    )
    return problems
