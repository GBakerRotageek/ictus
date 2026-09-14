"""Lowering one node to one entry in Conductor's ``agents:`` list.

Two decisions here are load-bearing and are made for the author rather than left
to them:

* Conditional routes are emitted before unconditional ones. Conductor takes the
  first matching route, so a catch-all written first would shadow every
  condition after it. Sorting by conditionality means authoring order cannot
  create that bug.
* A reference that may not have resolved yet is emitted optional. On the first
  pass through a loop the upstream node has not run, and under
  ``context.mode: explicit`` an unresolvable reference fails at the step
  boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import EmitError
from ictus.graph.node import (
    AgentNode,
    ComputeNode,
    GateNode,
    Node,
    NodeKind,
    Question,
    QuestionsNode,
    ScriptNode,
    SubGraphNode,
    TerminateNode,
    WaitNode,
    render_output_schema,
)
from ictus.graph.pipeline import DataDep, FailureMode, Pipeline
from ictus.graph.ports import PortType
from ictus.interfaces.conductor.status import (
    REPORTER_INTERPRETER,
    reported_args,
    reported_timeout,
    result_ports,
)
from ictus.interfaces.conductor.templates import (
    guard_test,
    output_path,
    reference_path,
    render,
    render_settled,
)

if TYPE_CHECKING:
    from ictus.graph.pipeline import Edge, RouteEnd
    from ictus.graph.values import YamlDict, YamlValue

__all__ = ["agent_entry"]

# Conductor's terminal route marker. The graph says an edge ends the run; this
# is the only place that says how that is written down.
END_MARKER = "$end"

# Values Conductor reads back with json.loads, so they must be rendered as JSON.
_STRUCTURED = frozenset({PortType.OBJECT, PortType.ARRAY})

# The one place a neutral node kind becomes a Conductor type string.
CONDUCTOR_TYPE: dict[NodeKind, str] = {
    NodeKind.LLM_CALL: "agent",
    NodeKind.HUMAN_DECISION: "human_gate",
    NodeKind.SUBPROCESS: "script",
    NodeKind.COMPUTATION: "set",
    NodeKind.DELAY: "wait",
    NodeKind.EXIT: "terminate",
    NodeKind.SUB_GRAPH: "workflow",
    NodeKind.ASK: "questions",
}


def _question(pipeline: Pipeline, node: Node, question: Question) -> YamlDict:
    """One entry of a ``questions:`` list."""
    out: YamlDict = {"text": render(pipeline, node, question.text)}
    if question.id is not None:
        out["id"] = question.id
    if question.hint is not None:
        out["hint"] = render(pipeline, node, question.hint)
    if question.choices:
        out["choices"] = list(question.choices)
    if not question.allow_free_text:
        out["allow_free_text"] = False
    if question.default is not None:
        out["default"] = question.default
    if question.required:
        out["required"] = True
    if not question.multiline:
        out["multiline"] = False
    return out


def kind_fields(pipeline: Pipeline, node: Node, system_prompt: str | None) -> YamlDict:
    """Conductor's per-type keys for one node.

    The whole of ``AgentDef``'s spelling lives in this function. The graph
    carries these values as ordinary attributes with ordinary English names;
    which key each lands under, and whether it is emitted at all, is Conductor's
    business and nobody else's.
    """
    match node:
        case AgentNode():
            fields: YamlDict = {"prompt": render(pipeline, node, node.prompt)}
            # Falling back rather than omitting: an omitted system prompt is
            # not a default one, it is an empty one. The SDK turns None into
            # `--system-prompt ""`, so a step with nothing set runs with no
            # working discipline at all.
            baseline = node.system_prompt or system_prompt
            if baseline is not None:
                fields["system_prompt"] = baseline
            if node.model is not None:
                fields["model"] = node.model
            if node.provider is not None:
                fields["provider"] = node.provider
            if node.tools is not None:
                fields["tools"] = list(node.tools)
            if node.max_turns is not None:
                fields["max_agent_iterations"] = node.max_turns
            if node.reasoning is not None:
                fields["reasoning"] = {"effort": node.reasoning.value}
            if node.timeout_seconds is not None:
                fields["timeout_seconds"] = node.timeout_seconds
            if node.max_session_seconds is not None:
                fields["max_session_seconds"] = node.max_session_seconds
            if node.validator is not None:
                # `max_retries` is emitted either way rather than left to the
                # engine's default: it is the difference between two model calls
                # and three, which is not a thing to leave implicit.
                validator: YamlDict = {
                    "criteria": node.validator.criteria,
                    "max_retries": 1 if node.validator.revise else 0,
                }
                if node.validator.model is not None:
                    validator["model"] = node.validator.model
                fields["validator"] = validator
            if node.working_dir is not None:
                fields["working_dir"] = node.working_dir
            # Emitted for an empty tuple as well: `[]` is "deny every skill",
            # which is a different instruction from the omitted key's "take the
            # workflow's default set". The same three states as `tools`.
            if node.skills is not None:
                fields["skills"] = list(node.skills)
            if node.plugins is not None:
                fields["plugins"] = list(node.plugins)
            if node.retry is not None:
                # Conductor's spelling, not ours: `attempts` counts the first
                # try the same way `max_attempts` does, and an empty `on` leaves
                # the key off so the engine keeps its own categories.
                retry: YamlDict = {
                    "max_attempts": node.retry.attempts,
                    "backoff": node.retry.backoff.value,
                }
                if node.retry.first_delay_seconds is not None:
                    retry["delay_seconds"] = node.retry.first_delay_seconds
                if node.retry.on:
                    retry["retry_on"] = [category.value for category in node.retry.on]
                fields["retry"] = retry
            if node.context_tier is not None:
                fields["context_tier"] = node.context_tier.value
            if node.session_key is not None:
                fields["session_key"] = node.session_key
            if node.dialog_trigger is not None:
                fields["dialog"] = {"trigger_prompt": node.dialog_trigger}
            return fields
        case GateNode():
            return {"prompt": render(pipeline, node, node.prompt)}
        case ScriptNode():
            args = [render(pipeline, node, arg) for arg in node.args]
            timeout = node.timeout
            fields = {"command": node.command}
            if node.trusted_status:
                # The command becomes the reporter's argument; see status.py for
                # why the result cannot otherwise be trusted on this engine.
                fields = {"command": REPORTER_INTERPRETER}
                args = reported_args(node.command, args, node.timeout)
                timeout = reported_timeout(node.timeout)
            if args:
                fields["args"] = list(args)
            if node.env:
                fields["env"] = dict(node.env)
            if node.stdin is not None:
                fields["stdin"] = render(pipeline, node, node.stdin)
            if timeout is not None:
                fields["timeout"] = timeout
            if node.working_dir is not None:
                fields["working_dir"] = node.working_dir
            return fields
        case ComputeNode():
            fields = {}
            if node.value is not None:
                fields["value"] = node.value
            if node.values is not None:
                fields["values"] = dict(node.values)
            if node.value_type is not None:
                fields["output_type"] = node.value_type.value
            return fields
        case WaitNode():
            fields = {"duration": node.duration}
            if node.reason is not None:
                fields["reason"] = node.reason
            return fields
        case TerminateNode():
            fields = {"status": node.status, "reason": render(pipeline, node, node.reason)}
            if node.result is not None:
                fields["output_template"] = {
                    key: render_settled(pipeline, node, value) for key, value in node.result.items()
                }
            return fields
        case QuestionsNode():
            fields = dict(node.kind_flags())
            if node.source is not None:
                # A dotted path, not an interpolation: the engine resolves it
                # itself, so wrapping it in braces would hand it a literal.
                fields["source"] = reference_path(pipeline, node.source)
            else:
                fields["questions"] = [_question(pipeline, node, q) for q in node.questions]
            abort = next(
                (e for e in pipeline.outgoing(node) if e.case == Pipeline.ABORT_CASE), None
            )
            if abort is not None:
                fields["abort_route"] = route_target(abort)
            return fields
        case SubGraphNode():
            fields = {"workflow": node.target}
            if node.max_depth is not None:
                fields["max_depth"] = node.max_depth
            return fields
        case _:
            raise EmitError(
                f"the Conductor backend has no lowering for {type(node).__name__}; "
                "every node kind needs an entry in kind_fields()"
            )


def route_target(edge: Edge) -> str:
    """Spell an edge's target the way Conductor names route destinations."""
    target = edge.target_node
    return END_MARKER if target is None else target.node_id


def agent_entry(pipeline: Pipeline, node: Node, system_prompt: str | None = None) -> YamlDict:
    agent: YamlDict = {"name": node.node_id}
    if node.description:
        agent["description"] = node.description
    agent["type"] = CONDUCTOR_TYPE[node.kind]
    agent.update(kind_fields(pipeline, node, system_prompt))

    refs = input_refs(pipeline, node)
    if refs:
        agent["input"] = list(refs)
    if isinstance(node, ScriptNode) and node.trusted_status:
        agent["output"] = render_output_schema(result_ports(node))
    elif node.emits_output_schema and node.outputs:
        agent["output"] = render_output_schema(node.outputs)

    if isinstance(node, SubGraphNode):
        mapping = input_mapping(pipeline, node)
        if mapping:
            agent["input_mapping"] = mapping

    if isinstance(node, GateNode):
        agent["options"] = gate_options(pipeline, node)
    elif node.accepts_routes:
        edges = route_entries(pipeline, node)
        if edges:
            agent["routes"] = edges
    return agent


def input_refs(pipeline: Pipeline, node: Node) -> list[str]:
    refs: list[str] = []
    for param, target, port in pipeline.input_bindings:
        if target is node:
            optional = port.optional or not param.required
            refs.append(f"workflow.input.{param.name}" + ("?" if optional else ""))
    for dep in pipeline.deps_into(node):
        ref = output_path(pipeline, dep.source, dep.connection.source.name)
        if _may_be_absent(pipeline, node, dep):
            ref += "?"
        refs.append(ref)
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return unique


def _may_be_absent(pipeline: Pipeline, node: Node, dep: DataDep) -> bool:
    """Whether this dependency can be missing when the target's context is built.

    ``_add_agent_input`` raises ``KeyError`` for a required entry whose path is
    not there (engine/context.py), which happens *before* a template guard can
    run. Three ways a value can be legitimately missing, and only the first two
    were asked about:

    * the author declared the port optional, or the source may not have run;
    * the source is a group member and the group is allowed to finish with a
      member that failed — ``continue_on_error`` is unusable otherwise;
    * the source's own spelling says the field is conditional, as a gate's
      free-text answer is on every branch that did not ask for one.
    """
    if dep.connection.target.optional or pipeline.may_be_unresolved(dep.source, node):
        return True
    if isinstance(dep.source, Node):
        if dep.source.guard_depth(dep.connection.source.name) > 0:
            return True
        group = pipeline.group_of(dep.source)
        if group is not None and group.failure_mode is not FailureMode.FAIL_FAST:
            return True
    return False


def input_mapping(pipeline: Pipeline, node: SubGraphNode) -> YamlDict:
    """Bind the parent's values to the child's declared parameters.

    Derived from the same edges that produced ``input:``, so a stage is wired
    with ordinary ``connect``/``feed`` calls and the parameter binding cannot
    drift from the data dependency it was meant to express.

    Every value is ``| tojson``, whatever its type, because the engine renders
    each entry to text and hands it to ``json.loads`` with a fall back to the
    raw string (``_build_subworkflow_inputs``). Only ``number`` survives that
    bare. A ``boolean`` renders as Python's ``True``, which is not JSON, so it
    reaches the child as the string ``"True"``; a ``string`` holding ``"false"``
    or ``"0700"`` parses as something that is not a string at all. Both are
    quiet — the child gets a value of the wrong type and runs anyway.
    """
    mapping: YamlDict = {}
    for param, target, port in pipeline.input_bindings:
        if target is node:
            mapping[port.name] = "{{ workflow.input." + param.name + " | tojson }}"
    for dep in pipeline.deps_into(node):
        expression = output_path(pipeline, dep.source, dep.connection.source.name)
        rendered = "{{ " + expression + " | tojson }}"
        if _may_be_absent(pipeline, node, dep):
            # The mapping is rendered against the same explicit context as the
            # step's own templates, so a source that may not have run is an
            # undefined variable here too — and the engine turns that into an
            # ExecutionError rather than an empty string.
            empty = _EMPTY_FOR[dep.connection.source.port_type]
            # Both branches have to be the same JSON, or the parameter arrives
            # as a different type depending on which one ran.
            rendered = (
                "{% if "
                + guard_test(pipeline, dep.source.ref(dep.connection.source.name))
                + " %}"
                + rendered
                + "{% else %}"
                + empty
                + "{% endif %}"
            )
        mapping[dep.connection.target.name] = rendered
    return mapping


# What an absent value becomes on the way into a child. A missing key is not the
# same as an empty one, but the child declared the parameter, so something of the
# right type has to arrive.
_EMPTY_FOR = {
    # A JSON string literal, not nothing: the live branch is `| tojson`, and an
    # empty render would parse as a failure and survive as a raw empty string
    # only by luck.
    PortType.STRING: '""',
    PortType.OBJECT: "{}",
    PortType.ARRAY: "[]",
    PortType.NUMBER: "0",
    PortType.BOOLEAN: "false",
}


def gate_options(pipeline: Pipeline, gate: GateNode) -> list[YamlValue]:
    by_case = {edge.case: edge for edge in pipeline.outgoing(gate)}
    options: list[YamlValue] = []
    for choice in gate.choices:
        edge = by_case.get(choice.value)
        if edge is None:
            raise EmitError(
                f"gate {gate.node_id!r} was never branched: choice {choice.value!r} has "
                "no route. Call pipeline.branch(gate, {...}) before emitting."
            )
        option: YamlDict = {
            "label": choice.label,
            "value": choice.value,
            "route": route_target(edge),
        }
        if choice.prompt_for is not None:
            option["prompt_for"] = choice.prompt_for
        if choice.multiline:
            option["multiline"] = True
        options.append(option)
    return options


def route_entries(pipeline: Pipeline, node: RouteEnd) -> list[YamlValue]:
    # An abort is carried by `abort_route`, not by the route list. Emitting it in
    # both places leaves a second unconditional route that can never be taken.
    edges = [e for e in pipeline.outgoing(node) if e.case != Pipeline.ABORT_CASE]
    conditional = [e for e in edges if e.when is not None]
    unconditional = [e for e in edges if e.when is None]
    routes: list[YamlValue] = []
    for edge in (*conditional, *unconditional):
        route: YamlDict = {"to": route_target(edge)}
        if edge.when is not None:
            route["when"] = render(pipeline, node, edge.when)
        routes.append(route)
    return routes
