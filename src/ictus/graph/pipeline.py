"""The composition graph.

``Pipeline`` owns every edge. Nodes never point at each other, so a node stays
reusable across pipelines and there is exactly one place that knows the shape of
the graph.

Every rejection happens at the call that introduces the invalid state, not at
emission: a port mismatch fails in ``connect``, a foreign node fails in
``connect``, an unroutable gate choice fails in ``branch``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from ictus.errors import CompositionError, PortTypeError
from ictus.graph.node import GateNode, Node, QuestionsNode, SubGraphNode
from ictus.graph.ports import InputPort, OutputPort, PortConnection, PortType
from ictus.graph.ref import Ref, Template

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ictus.graph.requirements import McpServer
    from ictus.graph.values import YamlScalar

ContextMode = Literal["accumulate", "last_only", "explicit"]
BudgetMode = Literal["audit", "enforce"]


class FailureMode(StrEnum):
    """What a parallel group does when one of its members fails."""

    FAIL_FAST = "fail_fast"
    CONTINUE_ON_ERROR = "continue_on_error"
    ALL_OR_NOTHING = "all_or_nothing"


@dataclass(frozen=True, eq=False)
class ParallelGroup:
    """Members that run at once, addressed as one thing by the graph.

    Routing goes to and from the *group*; members carry no edges of their own.
    That is not an ictus choice — Conductor rejects a member with ``routes``,
    and rejects gates, scripts, waits, sub-workflows and terminals as members
    outright. Only model calls and computations may run in a group.

    ``node_id`` is deliberately the same attribute a node uses, so every routing
    path treats a group and a node identically.
    """

    group_id: str
    members: tuple[Node, ...]
    description: str = ""
    failure_mode: FailureMode = FailureMode.FAIL_FAST

    @property
    def node_id(self) -> str:
        """The identifier routing resolves against."""
        return self.group_id


class _End:
    """Sentinel for Conductor's ``$end`` route target."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "END"


END = _End()

type RouteEnd = Node | ParallelGroup
type EdgeTarget = RouteEnd | _End


@dataclass(frozen=True, eq=False)
class WorkflowInput:
    """A typed parameter of the whole pipeline. Emitted as ``workflow.input``."""

    name: str
    port_type: PortType
    required: bool = True
    default: YamlScalar = None
    description: str = ""

    def ref(self) -> Ref:
        """A typed reference to this pipeline parameter."""
        return Ref(
            source_id=self.name,
            port=self.name,
            port_type=self.port_type,
            from_input=True,
            source=self,
        )


@dataclass(frozen=True, eq=False)
class Edge:
    """A control edge — who runs next. Emitted as ``routes`` or ``options[].route``.

    Carries no data. Conductor separates control (``routes``) from data
    (``input``), and collapsing them loses every reference that has to cross a
    gate: a gate's branch decides where to go, not what the target reads.
    """

    source: RouteEnd
    target: EdgeTarget
    case: str | None = None
    when: str | Template | None = None

    def condition_refs(self) -> tuple[Ref, ...]:
        """Typed references inside this edge's condition."""
        return tuple(self.when.refs()) if isinstance(self.when, Template) else ()

    @property
    def is_end(self) -> bool:
        """Whether this edge terminates the run rather than naming a successor."""
        return isinstance(self.target, _End)

    @property
    def target_node(self) -> RouteEnd | None:
        """The successor, or ``None`` when this edge ends the run."""
        return None if isinstance(self.target, _End) else self.target

    @property
    def describe_target(self) -> str:
        """A human-readable target, for error messages only — never emitted."""
        target = self.target_node
        return "END" if target is None else target.node_id


@dataclass(frozen=True, eq=False)
class DataDep:
    """A data edge — what a node reads. Emitted into the target's ``input`` list."""

    source: Node
    target: Node
    connection: PortConnection


class Pipeline:
    """A workflow graph plus the run-level settings Conductor needs.

    ``loop_passes`` is mandatory once the graph contains a cycle. Conductor's
    ``limits.max_iterations`` defaults to 10 *total step executions*, so a
    six-node graph with one retry loop is force-stopped on its second pass by a
    number nobody chose. Requiring the bound makes the choice explicit and lets
    the emitter derive a value from the actual graph.
    """

    def __init__(
        self,
        *,
        pipeline_id: str,
        description: str = "",
        version: str = "1",
        provider: str | None = None,
        default_model: str | None = None,
        context_mode: ContextMode = "explicit",
        loop_passes: int | None = None,
        budget_usd: float | None = None,
        budget_mode: BudgetMode = "audit",
        max_iterations: int | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        if not pipeline_id:
            raise CompositionError("pipeline_id cannot be empty")
        if loop_passes is not None and loop_passes < 1:
            raise CompositionError(f"loop_passes must be >= 1, got {loop_passes}")
        self.pipeline_id = pipeline_id
        self.description = description
        self.version = version
        # None means "whatever the backend defaults to". The graph has no
        # opinion about providers; leaving it unset here is not the same as
        # leaving it unset in the emitted file, which is what silently selected
        # copilot before.
        self.provider = provider
        self.default_model = default_model
        self.context_mode: ContextMode = context_mode
        self.loop_passes = loop_passes
        self.budget_usd = budget_usd
        self.budget_mode: BudgetMode = budget_mode
        self.max_iterations = max_iterations
        self.metadata: dict[str, str] = dict(metadata or {})

        self._nodes: list[Node] = []
        self._by_id: dict[str, Node] = {}
        self._edges: list[Edge] = []
        self._deps: list[DataDep] = []
        self._inputs: dict[str, WorkflowInput] = {}
        self._input_edges: list[tuple[WorkflowInput, Node, InputPort]] = []
        self._outputs: dict[str, tuple[str, PortType]] = {}
        self._children: dict[str, Pipeline] = {}
        self._groups: dict[str, ParallelGroup] = {}
        self._mcp: dict[str, McpServer] = {}
        self._entry: RouteEnd | None = None

    # -- construction ----------------------------------------------------

    def add[N: Node](self, node: N) -> N:
        """Register a node. Returns it unchanged so callers keep the concrete type."""
        existing = self._by_id.get(node.node_id)
        if existing is not None:
            what = "the same node twice" if existing is node else "two nodes"
            raise CompositionError(
                f"pipeline {self.pipeline_id!r} would register {what} under node_id "
                f"{node.node_id!r}; ids are the routing keyspace and must be unique"
            )
        self._nodes.append(node)
        self._by_id[node.node_id] = node
        return node

    def add_subworkflow(self, node: SubGraphNode, body: Pipeline) -> SubGraphNode:
        """Register a nested workflow and the pipeline it compiles from.

        The child is a full ``Pipeline`` with its own entry point and graph;
        the parent spends exactly one iteration on it.
        """
        self.add(node)
        self._children[node.node_id] = body
        return node

    @property
    def children(self) -> dict[str, Pipeline]:
        """Nested pipelines, keyed by the sub-workflow node that hosts them."""
        return dict(self._children)

    def parallel(
        self,
        group_id: str,
        members: Sequence[Node],
        *,
        description: str = "",
        failure_mode: FailureMode = FailureMode.FAIL_FAST,
    ) -> ParallelGroup:
        """Run ``members`` at once, as one addressable step.

        The members must already be part of this pipeline; the group is a way of
        scheduling them, not a second place to declare them. A group costs its
        member count against the iteration budget all at once.
        """
        if group_id in self._groups or group_id in self._by_id:
            raise CompositionError(
                f"pipeline {self.pipeline_id!r} already has something named {group_id!r}; "
                "groups and nodes share one routing keyspace"
            )
        if len(members) < 2:
            raise CompositionError(
                f"parallel group {group_id!r} needs at least two members; "
                f"got {len(members)}. One member is just a node."
            )
        for member in members:
            self._require_member(member, f"member of parallel group {group_id!r}")
        group = ParallelGroup(
            group_id=group_id,
            members=tuple(members),
            description=description,
            failure_mode=failure_mode,
        )
        self._groups[group_id] = group
        return group

    @property
    def groups(self) -> tuple[ParallelGroup, ...]:
        """Every parallel group, in declaration order."""
        return tuple(self._groups.values())

    def group_of(self, node: Node) -> ParallelGroup | None:
        """The group ``node`` runs inside, if any.

        Load-bearing for references: a member's output is read through the
        group, not directly, and emitting the direct form renders empty.
        """
        for group in self._groups.values():
            if any(m is node for m in group.members):
                return group
        return None

    def require_mcp(self, server: McpServer) -> McpServer:
        """Declare an MCP server this pipeline needs access to.

        Declared where the pipeline is written so it can be checked before the
        pipeline launches, rather than discovered when an agent reaches for a
        tool that was never connected.
        """
        existing = self._mcp.get(server.name)
        if existing is not None:
            raise CompositionError(
                f"pipeline {self.pipeline_id!r} already requires an MCP server named "
                f"{server.name!r}; names are how a server is addressed and must be unique"
            )
        self._mcp[server.name] = server
        return server

    @property
    def mcp_servers(self) -> tuple[McpServer, ...]:
        """Every MCP server this pipeline declares, in declaration order."""
        return tuple(self._mcp.values())

    def all_mcp_servers(self) -> tuple[McpServer, ...]:
        """This pipeline's servers and those of every stage it contains.

        A stage is compiled as its own workflow with its own runtime block, but
        preflight is about the environment the whole launch needs — so the
        requirement of a nested stage is the caller's problem too.
        """
        seen: dict[str, McpServer] = {s.name: s for s in self._mcp.values()}
        for child in self._children.values():
            for server in child.all_mcp_servers():
                seen.setdefault(server.name, server)
        return tuple(seen.values())

    def declare_input(
        self,
        name: str,
        port_type: PortType,
        *,
        required: bool = True,
        default: YamlScalar = None,
        description: str = "",
    ) -> WorkflowInput:
        """Declare a pipeline-level parameter."""
        if name in self._inputs:
            raise CompositionError(f"workflow input {name!r} is already declared")
        param = WorkflowInput(
            name=name,
            port_type=port_type,
            required=required,
            default=default,
            description=description,
        )
        self._inputs[name] = param
        return param

    def set_entry(self, start: RouteEnd) -> None:
        """Pin the entry point rather than letting it fall out of insertion order.

        A parallel group is a legal entry point: Conductor resolves
        ``entry_point`` against groups as well as agents.
        """
        self._require_routable(start, "entry point")
        self._entry = start

    # -- edges -----------------------------------------------------------

    def connect(
        self,
        source: Node,
        from_port: str,
        target: Node,
        to_port: str,
        *,
        when: str | Template | None = None,
    ) -> Edge:
        """Wire an output port to an input port.

        Raises before the edge exists if either node is foreign, either port is
        undeclared, or the port types differ.
        """
        self._require_member(source, "edge source")
        self._require_member(target, "edge target")
        self._reject_gate_source(source)
        self._reject_terminal_source(source)
        self._reject_grouped_source(source)

        out_port = source.get_output(from_port)
        in_port = target.get_input(to_port)
        if not out_port.accepts(in_port):
            raise PortTypeError(
                f"cannot connect {source.node_id}.{out_port.name} "
                f"({out_port.port_type.value}) to {target.node_id}.{in_port.name} "
                f"({in_port.port_type.value}): port types differ"
            )
        if when is None:
            self._reject_second_default_route(source, target)
        edge = Edge(source=source, target=target, when=when)
        self._edges.append(edge)
        self._deps.append(
            DataDep(source=source, target=target, connection=PortConnection(out_port, in_port))
        )
        return edge

    def connect_input(self, param: WorkflowInput, target: Node, to_port: str) -> None:
        """Feed a pipeline parameter into a node's input port."""
        if self._inputs.get(param.name) is not param:
            raise CompositionError(
                f"workflow input {param.name!r} was not declared on pipeline {self.pipeline_id!r}"
            )
        self._require_member(target, "input target")
        in_port = target.get_input(to_port)
        if param.port_type is not in_port.port_type:
            raise PortTypeError(
                f"cannot bind workflow input {param.name!r} ({param.port_type.value}) "
                f"to {target.node_id}.{in_port.name} ({in_port.port_type.value}): "
                "types differ"
            )
        self._input_edges.append((param, target, in_port))

    def feed(self, source: Node, from_port: str, target: Node, to_port: str) -> DataDep:
        """Declare that ``target`` reads a value from ``source``, with no control edge.

        Needed whenever data and control diverge — most often across a gate. The
        gate decides *where* execution goes; the node it routes to still has to
        read the value produced before the gate, and Conductor will not infer
        that under ``context.mode: explicit``.
        """
        self._require_member(source, "data source")
        self._require_member(target, "data target")
        out_port = source.get_output(from_port)
        in_port = target.get_input(to_port)
        if not out_port.accepts(in_port):
            raise PortTypeError(
                f"cannot feed {source.node_id}.{out_port.name} ({out_port.port_type.value}) "
                f"to {target.node_id}.{in_port.name} ({in_port.port_type.value}): "
                "port types differ"
            )
        dep = DataDep(source=source, target=target, connection=PortConnection(out_port, in_port))
        self._deps.append(dep)
        return dep

    def route(
        self, source: RouteEnd, target: EdgeTarget, *, when: str | Template | None = None
    ) -> Edge:
        """Add a control-only edge carrying no data.

        Use this when the target needs nothing from the source. It is a distinct
        method rather than an optional-port variant of ``connect`` so that "no
        data flows here" is a decision in the source, not an omission.
        """
        self._require_routable(source, "edge source")
        if not isinstance(target, _End):
            self._require_routable(target, "edge target")
        if isinstance(source, Node):
            self._reject_gate_source(source)
            self._reject_terminal_source(source)
            self._reject_grouped_source(source)
        if when is None:
            self._reject_second_default_route(source, target)
        edge = Edge(source=source, target=target, when=when)
        self._edges.append(edge)
        return edge

    def branch(self, gate: GateNode, routes: Mapping[str, EdgeTarget]) -> None:
        """Route each of a gate's choices to a target.

        Every declared choice must be routed and no unknown value may appear:
        an unrouted choice is a dead button in the dashboard, and an unknown one
        is a route the human can never reach.
        """
        self._require_member(gate, "branch source")
        declared = {c.value for c in gate.choices}
        given = set(routes)
        missing = sorted(declared - given)
        unknown = sorted(given - declared)
        if missing:
            raise CompositionError(
                f"gate {gate.node_id!r} has unrouted choice(s) {missing}; "
                "every option needs a target or the button does nothing"
            )
        if unknown:
            raise CompositionError(
                f"gate {gate.node_id!r} has no choice(s) {unknown}; "
                f"declared choices are {sorted(declared)}"
            )
        if any(e.source is gate for e in self._edges):
            raise CompositionError(f"gate {gate.node_id!r} has already been branched")
        for choice in gate.choices:
            target = routes[choice.value]
            if isinstance(target, Node):
                self._require_member(target, "branch target")
            self._edges.append(Edge(source=gate, target=target, case=choice.value))

    ABORT_CASE = "__abort__"

    def abort_route(self, node: QuestionsNode, target: EdgeTarget) -> Edge:
        """Where a person goes if they abandon a set of questions.

        A real edge, not a footnote: without it in the graph, whatever handles
        an abandoned run looks unreachable and the lint says so.
        """
        self._require_member(node, "abort source")
        if not isinstance(target, _End):
            self._require_routable(target, "abort target")
        if any(e.source is node and e.case == self.ABORT_CASE for e in self._edges):
            raise CompositionError(f"{node.node_id!r} already has an abort route")
        edge = Edge(source=node, target=target, case=self.ABORT_CASE)
        self._edges.append(edge)
        return edge

    def expose_output(
        self, name: str, node: Node, from_port: str, *, default: str | None = None
    ) -> None:
        """Publish a node output as part of the pipeline's final result.

        The port type is retained even though Conductor's top-level ``output:``
        is ``dict[str, str]`` — a rendered template with JSON coercion, not a
        typed contract. Keeping the type here is what lets a stage be wired into
        a parent with the same checking as any other edge.
        """
        self._require_member(node, "output source")
        port = node.get_output(from_port)
        if name in self._outputs:
            raise CompositionError(f"pipeline output {name!r} is already exposed")
        path = node.output_ref(port.name)
        expression = f"{node.node_id}.output.{path}"
        if default is not None:
            # Some values exist only on some branches — a gate's free-text field
            # is the standard case. Reading one on a branch that never set it is
            # a hard template error, so the fallback is not optional politeness.
            expression += f" | default('{default}')"
        self._outputs[name] = (f"{{{{ {expression} }}}}", port.port_type)

    # -- guards ----------------------------------------------------------

    def _require_routable(self, end: RouteEnd, role: str) -> None:
        """A routing endpoint must be a node or a group of this pipeline."""
        if isinstance(end, ParallelGroup):
            if self._groups.get(end.group_id) is not end:
                raise CompositionError(
                    f"{role} {end.group_id!r} is not a parallel group of pipeline "
                    f"{self.pipeline_id!r}"
                )
            return
        self._require_member(end, role)

    def _reject_grouped_source(self, source: Node) -> None:
        """A member routes as part of its group, never on its own."""
        group = self.group_of(source)
        if group is not None:
            raise CompositionError(
                f"{source.node_id!r} runs inside parallel group {group.group_id!r} and "
                "cannot have its own outgoing edge; route from the group instead"
            )

    def _require_member(self, node: Node, role: str) -> None:
        if self._by_id.get(node.node_id) is not node:
            raise CompositionError(
                f"{role} {node.node_id!r} is not part of pipeline {self.pipeline_id!r}; "
                "add() it first (a node from another pipeline is never implicitly shared)"
            )

    @staticmethod
    def _reject_gate_source(source: Node) -> None:
        if source.routes_via_options:
            raise CompositionError(
                f"gate {source.node_id!r} routes through its options; use branch() so every "
                "choice gets a target"
            )

    @staticmethod
    def _reject_terminal_source(source: Node) -> None:
        if not source.accepts_routes:
            raise CompositionError(
                f"{source.node_id!r} is a terminate node and cannot have outgoing edges"
            )

    def _reject_second_default_route(self, source: RouteEnd, target: EdgeTarget) -> None:
        for edge in self._edges:
            if edge.source is source and edge.when is None and edge.case is None:
                tid = "END" if isinstance(target, _End) else target.node_id
                raise CompositionError(
                    f"{source.node_id!r} already has an unconditional route to "
                    f"{edge.describe_target!r}; adding one to {tid!r} would silently drop it "
                    "because Conductor takes the first matching route. Give one a "
                    "`when` condition, or use a gate."
                )

    # -- graph queries ---------------------------------------------------

    @property
    def nodes(self) -> tuple[Node, ...]:
        """Every registered node, in insertion order."""
        return tuple(self._nodes)

    @property
    def edges(self) -> tuple[Edge, ...]:
        """Every edge, in insertion order."""
        return tuple(self._edges)

    @property
    def workflow_inputs(self) -> tuple[WorkflowInput, ...]:
        """Every declared pipeline parameter."""
        return tuple(self._inputs.values())

    @property
    def input_bindings(self) -> tuple[tuple[WorkflowInput, Node, InputPort], ...]:
        """Every parameter-to-port binding."""
        return tuple(self._input_edges)

    @property
    def exposed_outputs(self) -> dict[str, str]:
        """The pipeline's final output templates."""
        return {name: template for name, (template, _) in self._outputs.items()}

    @property
    def exposed_output_ports(self) -> tuple[OutputPort, ...]:
        """The pipeline's result as typed ports, for use by a parent pipeline."""
        return tuple(OutputPort(name, port_type) for name, (_, port_type) in self._outputs.items())

    @property
    def declared_input_ports(self) -> tuple[InputPort, ...]:
        """The pipeline's parameters as typed ports, for use by a parent pipeline."""
        return tuple(
            InputPort(p.name, p.port_type, p.description, optional=not p.required)
            for p in self._inputs.values()
        )

    @property
    def data_deps(self) -> tuple[DataDep, ...]:
        """Every data dependency, in declaration order."""
        return tuple(self._deps)

    def deps_into(self, node: Node) -> list[DataDep]:
        """Data dependencies that ``node`` reads."""
        return [d for d in self._deps if d.target is node]

    def outgoing(self, node: RouteEnd) -> list[Edge]:
        """Edges leaving ``node``, in insertion order."""
        return [e for e in self._edges if e.source is node]

    def inbound(self, node: Node) -> list[Edge]:
        """Edges arriving at ``node``, in insertion order."""
        return [e for e in self._edges if e.target is node]

    def entry(self) -> RouteEnd:
        """Resolve the entry point.

        An explicit ``set_entry`` wins. Otherwise the unique node with no
        inbound edge is used; if that is ambiguous the caller is told to pin it
        rather than having insertion order decide silently.
        """
        if self._entry is not None:
            return self._entry
        if not self._nodes:
            raise CompositionError(f"pipeline {self.pipeline_id!r} has no nodes")
        targeted = {e.target.node_id for e in self._edges if not isinstance(e.target, _End)}
        grouped = {m.node_id for g in self._groups.values() for m in g.members}
        bound = {n.node_id for _, n, _ in self._input_edges}
        roots: list[RouteEnd] = [
            n for n in self._nodes if n.node_id not in targeted and n.node_id not in grouped
        ]
        roots += [g for g in self._groups.values() if g.group_id not in targeted]
        if len(roots) == 1:
            return roots[0]
        if not roots:
            raise CompositionError(
                f"pipeline {self.pipeline_id!r} has no node without an inbound edge; "
                "call set_entry() to pin the entry point"
            )
        preferred = [n for n in roots if n.node_id in bound]
        if len(preferred) == 1:
            return preferred[0]
        names = ", ".join(sorted(n.node_id for n in roots))
        raise CompositionError(
            f"pipeline {self.pipeline_id!r} has {len(roots)} nodes with no inbound edge "
            f"({names}); call set_entry() to pin the entry point"
        )

    def _successors(self, end: RouteEnd) -> Iterable[RouteEnd]:
        """What runs after ``end``.

        Entering a group reaches its members: they have no inbound edge of their
        own, so without this they would look unreachable.
        """
        if isinstance(end, ParallelGroup):
            yield from end.members
        for edge in self._edges:
            if edge.source is end and not isinstance(edge.target, _End):
                yield edge.target
        if isinstance(end, Node):
            group = self.group_of(end)
            if group is not None:
                # A member's continuation is whatever the group routes to.
                for edge in self._edges:
                    if edge.source is group and not isinstance(edge.target, _End):
                        yield edge.target

    def reaches(self, start: RouteEnd, goal: RouteEnd) -> bool:
        """Whether ``goal`` is reachable from ``start`` by following edges."""
        seen: set[str] = set()
        stack: list[RouteEnd] = [start]
        while stack:
            current = stack.pop()
            if current.node_id in seen:
                continue
            seen.add(current.node_id)
            for nxt in self._successors(current):
                if nxt is goal:
                    return True
                stack.append(nxt)
        return False

    def reachable_from_entry(self) -> set[str]:
        """Node ids reachable from the entry point."""
        start = self.entry()
        seen: set[str] = {start.node_id}
        stack: list[RouteEnd] = [start]
        while stack:
            for nxt in self._successors(stack.pop()):
                if nxt.node_id not in seen:
                    seen.add(nxt.node_id)
                    stack.append(nxt)
        return seen

    def back_edges(self) -> list[Edge]:
        """Control edges that close a cycle, by depth-first search.

        Detected as edges into a node still on the DFS stack. The cheaper test
        "target can reach source" is wrong: in a two-node loop both edges pass
        it, so the forward edge gets reported as a back edge too.
        """
        state: dict[str, int] = {}
        found: list[Edge] = []

        def visit(node: RouteEnd) -> None:
            state[node.node_id] = 1
            for edge in self.outgoing(node):
                # A group is a routing endpoint like a node: an edge back into
                # one closes a cycle exactly as an edge back into a node does.
                if isinstance(edge.target, _End):
                    continue
                mark = state.get(edge.target.node_id, 0)
                if mark == 1:
                    found.append(edge)
                elif mark == 0:
                    visit(edge.target)
            state[node.node_id] = 2

        roots: list[RouteEnd] = [self.entry(), *self._nodes] if self._nodes else []
        for node in roots:
            if state.get(node.node_id, 0) == 0:
                visit(node)
        return found

    def may_be_unresolved(self, source: RouteEnd, target: RouteEnd) -> bool:
        """Whether ``target`` can run before ``source`` has produced anything.

        True when ``target`` is reachable from the entry point without passing
        through ``source`` — which is exactly when a reference to a
        ``source`` output has to be emitted optional.
        """
        start = self.entry()
        if start is source:
            return False
        if start is target:
            return True

        # Passing a group means every one of its members has run: the group does
        # not route onward until they finish. So a member is a barrier, and so is
        # the group that contains it — without the second, the group's outward
        # edge looks like a way to reach the target with the member skipped, and
        # a perfectly available value gets emitted as optional.
        barriers = {source.node_id}
        if isinstance(source, Node):
            group = self.group_of(source)
            if group is not None:
                barriers.add(group.group_id)

        # The entry can itself be the barrier — a pipeline whose first step is
        # the group. Expanding it would walk straight past the very thing that
        # has to run first.
        if start.node_id in barriers:
            return False

        seen: set[str] = {start.node_id}
        stack: list[RouteEnd] = [start]
        while stack:
            for nxt in self._successors(stack.pop()):
                if nxt.node_id in barriers or nxt.node_id in seen:
                    continue
                if nxt is target:
                    return True
                seen.add(nxt.node_id)
                stack.append(nxt)
        return False

    def has_cycle(self) -> bool:
        """Whether the graph loops."""
        return bool(self.back_edges())

    def has_gate(self) -> bool:
        """Whether any node pauses for a human."""
        return any(isinstance(n, GateNode) for n in self._nodes)

    def require_loop_bound(self) -> None:
        """Refuse a cyclic graph that never says how many times it may go round.

        Generic, not engine-specific: every executor needs a bound, and an
        unbounded loop is an authoring omission rather than a target's problem.
        How the bound is spent is the backend's arithmetic.
        """
        if self.has_cycle() and self.loop_passes is None and self.max_iterations is None:
            raise CompositionError(
                f"pipeline {self.pipeline_id!r} contains a loop but sets no loop_passes; "
                "an unbounded loop has no safe default. Pass loop_passes=N (or an "
                "explicit max_iterations) so the bound is a decision, not an accident."
            )

    def longest_cycle_length(self) -> int:
        """Nodes on the longest cycle, or 0 when the graph is acyclic.

        Exposed so a backend can price a loop in whatever unit it counts.
        """
        back = self.back_edges()
        return max((self._cycle_length(e) for e in back), default=0)

    def _cycle_length(self, back_edge: Edge) -> int:
        """Number of nodes on the shortest cycle closed by ``back_edge``."""
        if isinstance(back_edge.target, _End):
            return 1
        start, goal = back_edge.target, back_edge.source
        if start is goal:
            return 1
        depth: dict[str, int] = {start.node_id: 1}
        queue: list[RouteEnd] = [start]
        while queue:
            current = queue.pop(0)
            for nxt in self._successors(current):
                if nxt.node_id in depth:
                    continue
                depth[nxt.node_id] = depth[current.node_id] + 1
                if nxt is goal:
                    return depth[nxt.node_id]
                queue.append(nxt)
        return len(self._nodes)
