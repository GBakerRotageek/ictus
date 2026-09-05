"""Node kinds — one frozen class per Conductor agent type.

Conductor's ``AgentDef`` is a single wide model whose ``validate_agent_type``
validator enforces a different required/forbidden field set for each of its
eight ``type`` values, twenty-plus fields deep. Mirroring that as one class with
a free-form config dict reproduces the trap: every illegal combination stays
representable and is only caught downstream, if at all.

Instead each kind is its own class carrying only the fields Conductor permits on
it. ``TerminateNode`` has no ``outputs`` field to set, so the "terminate agents
cannot have 'output'" rule cannot be violated. ``routes`` is not modelled here
at all — routing belongs to the graph, so ``Pipeline`` owns it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from ictus.errors import CompositionError, UnknownPortError
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import Ref, Template

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from ictus.graph.values import YamlDict, YamlValue


class NodeKind(StrEnum):
    """What a node does, named for the work rather than for any engine.

    A backend maps these onto whatever it calls them. ``Capabilities`` is
    declared in these terms, so a graph using a kind the target cannot express
    is refused while it is being composed.
    """

    LLM_CALL = "llm_call"
    HUMAN_DECISION = "human_decision"
    SUBPROCESS = "subprocess"
    COMPUTATION = "computation"
    DELAY = "delay"
    EXIT = "exit"
    SUB_GRAPH = "sub_graph"
    ASK = "ask"


NODE_KINDS = frozenset(NodeKind)


def _has_text(prompt: str | Template) -> bool:
    """Whether a prompt says anything at all."""
    if isinstance(prompt, Template):
        return bool(prompt.parts)
    return bool(prompt.strip())


_IDENT = "abcdefghijklmnopqrstuvwxyz0123456789_"


def slugify(label: str) -> str:
    """Derive a routing identifier from a human label."""
    out = "".join(c if c in _IDENT else "_" for c in label.strip().lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


@dataclass(frozen=True, kw_only=True, eq=False)
class Node(ABC):
    """A single step in a workflow.

    ``node_id`` is the identity Conductor routes on — it is emitted as
    ``AgentDef.name``; ``description`` is the human-facing string and is emitted
    as ``AgentDef.description``. Collapsing these two into one field is what made
    the previous emitter unroutable: ``entry_point`` was written from the slug
    while agent identity was the display string, so nothing resolved.

    There is deliberately no third "label" field: Conductor has one human-text
    slot, and a field that is stored, type-checked and then discarded is how the
    previous port graph came to mean nothing.
    """

    node_id: str
    description: str = ""
    inputs: tuple[InputPort, ...] = ()

    def __post_init__(self) -> None:
        if not self.node_id:
            raise CompositionError("node_id cannot be empty")
        if slugify(self.node_id) != self.node_id:
            raise CompositionError(
                f"node_id {self.node_id!r} is not a routing identifier; "
                f"use {slugify(self.node_id)!r} (lowercase, digits and underscore only)"
            )
        seen: set[str] = set()
        names = [p.name for p in self.inputs] + [p.name for p in self.outputs]
        for name in names:
            if name in seen:
                raise CompositionError(
                    f"node {self.node_id!r} declares port {name!r} more than once"
                )
            seen.add(name)

    @property
    @abstractmethod
    def kind(self) -> NodeKind:
        """What this node does, in engine-neutral terms."""

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        """Values this node produces.

        Empty for the kinds on which Conductor forbids ``output:``.
        """
        return ()

    @property
    def routes_via_options(self) -> bool:
        """Whether outgoing edges are emitted as ``options[].route``.

        True only for ``human_gate``, where ``routes:`` is accepted by the
        schema and then ignored by the engine.
        """
        return False

    @property
    def accepts_routes(self) -> bool:
        """Whether this kind may have outgoing edges at all."""
        return True

    @property
    def emits_output_schema(self) -> bool:
        """Whether Conductor permits an ``output:`` block on this kind."""
        return False

    def output_ref(self, port_name: str) -> str:
        """The path under ``<node>.output.`` that reads this port.

        Identity for most kinds. A gate overrides it because Conductor fixes the
        shape of a gate's output rather than taking a declared schema.
        """
        return port_name

    def template_strings(self) -> Iterator[str]:
        """Author-written *raw* strings a backend renders.

        Typed references are reported by ``prompt_refs`` instead; anything left
        here is literal text that should contain no template syntax at all.
        """
        return iter(())

    def prompt_refs(self) -> Iterator[Ref]:
        """Typed references this node reads."""
        return iter(())

    def ref(self, port: str) -> Ref:
        """A typed reference to one of this node's outputs.

        Checked here: an undeclared port raises now, rather than surviving as
        text until a lint parses it back out or a run fails on it.
        """
        declared = self.get_output(port)
        return Ref(
            source_id=self.node_id,
            port=declared.name,
            port_type=declared.port_type,
            source=self,
        )

    def get_input(self, name: str) -> InputPort:
        """Look up a declared input port by name."""
        for port in self.inputs:
            if port.name == name:
                return port
        known = ", ".join(p.name for p in self.inputs) or "(none)"
        raise UnknownPortError(
            f"node {self.node_id!r} has no input port {name!r}; declared inputs: {known}"
        )

    def get_output(self, name: str) -> OutputPort:
        """Look up a declared output port by name."""
        for port in self.outputs:
            if port.name == name:
                return port
        known = ", ".join(p.name for p in self.outputs) or "(none)"
        raise UnknownPortError(
            f"node {self.node_id!r} has no output port {name!r}; declared outputs: {known}"
        )


@dataclass(frozen=True, kw_only=True, eq=False)
class AgentNode(Node):
    """An LLM step. Conductor ``type: agent`` (the default)."""

    prompt: str | Template
    system_prompt: str | None = None
    model: str | None = None
    provider: str | None = None
    tools: tuple[str, ...] = ()
    declared_outputs: tuple[OutputPort, ...] = ()
    session_key: str | None = None
    dialog_trigger: str | None = None
    """When set, the node may pause after running and converse with the person.

    An evaluator judges the node's output against this criterion and decides
    whether to open a multi-turn conversation. Only a model call can do this —
    Conductor rejects it on gates, scripts, waits and terminals, which is why
    this field exists on this class and nowhere else.
    """

    def __post_init__(self) -> None:
        if not _has_text(self.prompt):
            raise CompositionError(
                f"agent node {self.node_id!r} requires a non-empty prompt; "
                "an agent with no prompt is a billable call that says nothing"
            )
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.LLM_CALL

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        return self.declared_outputs

    @property
    def emits_output_schema(self) -> bool:
        return True

    def template_strings(self) -> Iterator[str]:
        if isinstance(self.prompt, str):
            yield self.prompt
        if self.system_prompt is not None:
            yield self.system_prompt
        if self.dialog_trigger is not None:
            yield self.dialog_trigger

    def prompt_refs(self) -> Iterator[Ref]:
        if isinstance(self.prompt, Template):
            yield from self.prompt.refs()


@dataclass(frozen=True, slots=True)
class GateChoice:
    """One option offered to the human at a gate.

    Carries no route: the target belongs to the graph, so ``Pipeline.branch``
    supplies it. That keeps a gate reusable across pipelines and keeps every
    edge in one place.
    """

    value: str
    label: str
    prompt_for: str | None = None
    multiline: bool = False


@dataclass(frozen=True, kw_only=True, eq=False)
class GateNode(Node):
    """A human decision point. Conductor ``type: human_gate``.

    Outgoing edges are emitted as ``options[].route``; a ``routes:`` block on a
    gate validates and is then ignored by the engine.
    """

    prompt: str | Template
    choices: tuple[GateChoice, ...]

    def __post_init__(self) -> None:
        if not _has_text(self.prompt):
            raise CompositionError(f"gate {self.node_id!r} requires a non-empty prompt")
        if not self.choices:
            raise CompositionError(f"gate {self.node_id!r} requires at least one choice")
        values = [c.value for c in self.choices]
        dupes = {v for v in values if values.count(v) > 1}
        if dupes:
            raise CompositionError(
                f"gate {self.node_id!r} has duplicate choice values: {sorted(dupes)}"
            )
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.HUMAN_DECISION

    @property
    def routes_via_options(self) -> bool:
        return True

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        """The gate's output shape, which Conductor fixes rather than declares.

        ``selected`` carries the chosen value. Each choice with a ``prompt_for``
        contributes a port of that name, read from ``additional_input``. Exposing
        them as ports is what lets a rejection note be wired back into the loop
        with the same type checking as any other edge.
        """
        ports = [OutputPort("selected", PortType.STRING, "The chosen option value")]
        seen: set[str] = set()
        for choice in self.choices:
            if choice.prompt_for is not None and choice.prompt_for not in seen:
                seen.add(choice.prompt_for)
                ports.append(
                    OutputPort(choice.prompt_for, PortType.STRING, "Free text from the human")
                )
        return tuple(ports)

    def output_ref(self, port_name: str) -> str:
        return port_name if port_name == "selected" else f"additional_input.{port_name}"

    def template_strings(self) -> Iterator[str]:
        if isinstance(self.prompt, str):
            yield self.prompt

    def prompt_refs(self) -> Iterator[Ref]:
        if isinstance(self.prompt, Template):
            yield from self.prompt.refs()


@dataclass(frozen=True, kw_only=True, eq=False)
class ScriptNode(Node):
    """A subprocess step. Conductor ``type: script`` — no model in the loop."""

    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    stdin: str | None = None
    timeout: int | None = None
    working_dir: str | None = None
    declared_outputs: tuple[OutputPort, ...] = ()

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise CompositionError(f"script node {self.node_id!r} requires a command")
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.SUBPROCESS

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        return self.declared_outputs

    @property
    def emits_output_schema(self) -> bool:
        return True

    def template_strings(self) -> Iterator[str]:
        yield from self.args
        if self.stdin is not None:
            yield self.stdin


@dataclass(frozen=True, kw_only=True, eq=False)
class ComputeNode(Node):
    """A zero-cost computation. Conductor ``type: set`` — no provider call.

    Exactly one of ``value`` or ``values`` is set, which is Conductor's own
    requirement; the constructor rejects both and neither.
    """

    value: str | None = None
    values: Mapping[str, str] | None = None
    value_type: PortType | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.values is None):
            raise CompositionError(
                f"compute node {self.node_id!r} requires exactly one of 'value' or 'values'"
            )
        if self.values is not None and self.value_type is not None:
            raise CompositionError(
                f"compute node {self.node_id!r} cannot combine 'values' with 'output_type'; "
                "output_type coerces a single 'value'"
            )
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.COMPUTATION

    def template_strings(self) -> Iterator[str]:
        if self.value is not None:
            yield self.value
        if self.values is not None:
            yield from self.values.values()


@dataclass(frozen=True, kw_only=True, eq=False)
class WaitNode(Node):
    """A timed pause. Conductor ``type: wait`` — costs one iteration, no model."""

    duration: float
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise CompositionError(
                f"wait node {self.node_id!r} requires a positive duration, got {self.duration}"
            )
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.DELAY

    def template_strings(self) -> Iterator[str]:
        if self.reason is not None:
            yield self.reason


@dataclass(frozen=True, kw_only=True, eq=False)
class TerminateNode(Node):
    """An explicit exit. Conductor ``type: terminate``.

    A node with no outgoing edge implicitly means ``$end``, which makes a
    forgotten edge indistinguishable from an intended finish. Terminating
    explicitly gives the run a distinguishable exit status and marks the stop
    as deliberate in the event log.
    """

    status: Literal["success", "failed"]
    reason: str | Template
    result: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not _has_text(self.reason):
            raise CompositionError(f"terminate node {self.node_id!r} requires a non-empty reason")
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.EXIT

    @property
    def accepts_routes(self) -> bool:
        return False

    def template_strings(self) -> Iterator[str]:
        if isinstance(self.reason, str):
            yield self.reason
        if self.result is not None:
            yield from self.result.values()

    def prompt_refs(self) -> Iterator[Ref]:
        if isinstance(self.reason, Template):
            yield from self.reason.refs()


@dataclass(frozen=True, slots=True)
class Question:
    """One thing to ask a person.

    ``id`` is the key its answer lands under, so downstream references survive
    a question being inserted above them. Leave it unset only for questions
    nothing reads by name.
    """

    text: str
    id: str | None = None
    hint: str | None = None
    choices: tuple[str, ...] = ()
    allow_free_text: bool = True
    default: str | None = None
    required: bool = False
    multiline: bool = True

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise CompositionError("a question needs text")
        if not self.choices and not self.allow_free_text:
            raise CompositionError(
                f"question {self.id or self.text!r} is unanswerable: no choices and no free "
                "text, so there is nothing the person can do with it"
            )
        if self.id is not None and slugify(self.id) != self.id:
            raise CompositionError(
                f"question id {self.id!r} is not an identifier; use {slugify(self.id)!r}"
            )


@dataclass(frozen=True, kw_only=True, eq=False)
class QuestionsNode(Node):
    """Ask a person for things the run could not work out. ``type: questions``.

    Distinct from a gate: a gate offers a decision among known options, this
    collects *values*. All of them in one engine step — N questions cost one
    iteration, not N.

    Either the questions are known when the pipeline is written, or an upstream
    node produces them: a ticket touching an unknown number of repositories
    cannot have its questions written in advance. Exactly one of ``questions``
    and ``source`` is set, which is Conductor's rule and this constructor's.
    """

    questions: tuple[Question, ...] = ()
    source: Ref | None = None
    allow_back: bool | None = None
    allow_skip: bool | None = None
    allow_skip_all: bool | None = None
    allow_abort: bool | None = None

    def __post_init__(self) -> None:
        if bool(self.questions) == (self.source is not None):
            raise CompositionError(
                f"questions node {self.node_id!r} needs exactly one of 'questions' "
                "(known when written) or 'source' (produced by an earlier node)"
            )
        ids = [q.id for q in self.questions if q.id]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise CompositionError(
                f"questions node {self.node_id!r} reuses answer id(s) {sorted(dupes)}"
            )
        super().__post_init__()

    @property
    def kind(self) -> NodeKind:
        return NodeKind.ASK

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        """The fixed shape, plus one port per question that named itself.

        Conductor fixes the output of this kind, so nothing is declared by the
        author — but an inline question with an ``id`` is known here, and giving
        it a port is what lets ``node.ref("repo_path")`` be checked.
        """
        ports = [
            OutputPort("answers", PortType.OBJECT, "Every answer, keyed by question id"),
            OutputPort("transcript", PortType.STRING, "The exchange, as text"),
            OutputPort("answered_count", PortType.NUMBER, "How many were answered"),
            OutputPort("outcome", PortType.STRING, "How the exchange ended"),
        ]
        reserved = {p.name for p in ports}
        ports += [
            OutputPort(q.id, PortType.STRING, q.text)
            for q in self.questions
            if q.id and q.id not in reserved
        ]
        return tuple(ports)

    def output_ref(self, port_name: str) -> str:
        fixed = {"answers", "transcript", "answered_count", "outcome"}
        return port_name if port_name in fixed else f"answers.{port_name}"

    def template_strings(self) -> Iterator[str]:
        for question in self.questions:
            yield question.text
            if question.hint is not None:
                yield question.hint

    def kind_flags(self) -> dict[str, bool]:
        """Navigation flags the author set explicitly."""
        named = {
            "allow_back": self.allow_back,
            "allow_skip": self.allow_skip,
            "allow_skip_all": self.allow_skip_all,
            "allow_abort": self.allow_abort,
        }
        return {k: v for k, v in named.items() if v is not None}


@dataclass(frozen=True, kw_only=True, eq=False)
class SubGraphNode(Node):
    """A nested workflow. Conductor ``type: workflow``.

    Emitted by ``Stage``; not usually constructed directly. This is Conductor's
    only nesting construct — it has its own entry point, its own graph, and
    costs the parent exactly one iteration.
    """

    target: str
    declared_outputs: tuple[OutputPort, ...] = ()
    max_depth: int | None = None

    @property
    def kind(self) -> NodeKind:
        return NodeKind.SUB_GRAPH

    @property
    def outputs(self) -> tuple[OutputPort, ...]:
        """The child's exposed results.

        Known to ictus but never emitted: the child declares its own ``output:``
        map, and re-declaring a schema on the parent node would be a second
        source of truth for the same values.
        """
        return self.declared_outputs


def render_output_schema(ports: Sequence[OutputPort]) -> YamlDict:
    """Lower output ports to Conductor's ``output:`` block."""
    out: YamlDict = {}
    for port in ports:
        entry: dict[str, YamlValue] = {"type": port.port_type.value}
        if port.description:
            entry["description"] = port.description
        out[port.name] = entry
    return out
