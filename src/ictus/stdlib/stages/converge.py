"""Try, judge, try again — bounded, with the exhaustion routable.

Every loop in the previous stdlib had the same hole. ``revise_loop`` with three
passes and a reviewer who never approves does not "give up after three"; it runs
until Conductor's iteration budget is spent and raises ``MaxIterationsError``,
which ``_run_child_engine`` does not catch — so it detonates the caller past
every route the caller declared. ``poll_until`` had it too. The bound was a
number that decided *when* the run would crash, not what would happen.

``converge`` counts its own passes in a ``type: set`` step (zero provider calls,
one iteration) and routes on that count, so running out is an ordinary exit with
a payload. It is a :class:`Scope`, so the caller branches on ``converged`` vs
``exhausted`` the same way it branches on anything else.

The counter is not decoration. Verified on a live run: a ``set`` step can read
its own previous output, needs ``{% if n is defined %}`` on the first pass
because the *root* is undefined and ``| default()`` cannot rescue that, and is
addressed as ``n.output`` — reading ``n.output.value`` raises "'int object' has
no attribute 'value'".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ictus.errors import CompositionError
from ictus.graph.node import AgentNode, ComputeNode, GateNode, Node
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import Ref, Template, TemplatePart, at_least, optional, ref_to, tpl
from ictus.graph.scope import Scope, outcome_scope
from ictus.stdlib.gates.approval import approval_gate
from ictus.stdlib.steps.counter import counter as counter_step
from ictus.stdlib.steps.wait import wait

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["CONVERGED", "EXHAUSTED", "Attempt", "converge"]

CONVERGED = "converged"
EXHAUSTED = "exhausted"

JudgeMode = Literal["model", "human", "self"]

FEEDBACK = "feedback"
PASSES = "passes"
COUNTER = "pass_number"


@dataclass(frozen=True, slots=True)
class Attempt:
    """One step of the work a pass performs.

    A sequence of these becomes a chain — write, then test, then lint — retried
    as a unit. Only the last one's outputs are what the loop converges *on*;
    the earlier ones are how it got there.
    """

    node_id: str
    prompt: str
    produces: tuple[OutputPort, ...] = ()
    description: str = ""


def converge(
    *,
    stage_id: str,
    attempt: Attempt | Sequence[Attempt],
    judge: JudgeMode = "model",
    judge_prompt: str = "",
    verdict_port: str = "approved",
    passes: int = 3,
    pause_between: float | None = None,
    remember: bool = False,
    description: str = "",
    brief: str = "What to work from",
) -> Scope:
    """A bounded try/judge loop whose give-up is a value, not a crash.

    ``judge`` picks who decides:

    * ``model`` — an agent reads the last attempt and emits ``approved`` plus
      ``notes``. The notes are fed back into the next pass.
    * ``human`` — an approval gate. The rejection branch collects free text,
      which is what makes this a revise loop rather than a retry loop; without
      that edge the second pass knows nothing the first did not.
    * ``self`` — no judge node. The last attempt declares the verdict port
      itself, which is the shape a poll wants: one step that checks and reports.

    ``remember`` keeps each attempt's session across passes, so pass two revises
    what it wrote rather than writing it again from the brief and a note.

    Outcomes are ``converged`` and ``exhausted``. Both carry every output of the
    final attempt, the last ``feedback``, and the ``passes`` actually spent, so
    a caller can salvage a nearly-good artifact instead of only learning that
    something failed.

    Contract: input ``brief`` (string) in; outcomes out.
    """
    steps = [attempt] if isinstance(attempt, Attempt) else list(attempt)
    if not steps:
        raise CompositionError(f"converge {stage_id!r} needs at least one attempt")
    if passes < 1:
        raise CompositionError(f"converge {stage_id!r} needs passes >= 1, got {passes}")
    last = steps[-1]
    if judge == "self":
        if judge_prompt:
            raise CompositionError(
                f"converge {stage_id!r} uses judge='self', so the verdict comes from "
                f"{last.node_id!r} and there is no judge to prompt"
            )
        if not any(p.name == verdict_port for p in last.produces):
            declared = ", ".join(p.name for p in last.produces) or "(none)"
            raise CompositionError(
                f"converge {stage_id!r} uses judge='self' but {last.node_id!r} declares no "
                f"{verdict_port!r} output; declared: {declared}"
            )
    elif not judge_prompt:
        raise CompositionError(f"converge {stage_id!r} needs a judge_prompt for judge={judge!r}")

    carry = {p.name: p.port_type for p in last.produces}
    carry[FEEDBACK] = PortType.STRING
    carry[PASSES] = PortType.NUMBER

    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=(CONVERGED, EXHAUSTED),
        carry=carry,
        description=description or f"Converge on {last.node_id}",
        loop_passes=passes,
    )
    body = scope.body
    work = body.declare_input("brief", PortType.STRING, description=brief)

    # Counting first: the bound has to be readable by the time the judge routes.
    counter = body.add(counter_step(node_id=COUNTER, description="Which pass this is"))
    body.set_entry(counter)
    body.feed(counter, "value", counter, COUNTER)

    notes_source = "judge" if judge == "model" else "review"
    feedback = ref_to(notes_source, "notes", PortType.STRING)

    previous: Node = counter
    made: list[AgentNode] = []
    for index, step in enumerate(steps):
        node = body.add(
            AgentNode(
                node_id=step.node_id,
                description=step.description or step.node_id,
                inputs=(
                    InputPort("brief", PortType.STRING),
                    *(
                        InputPort(f"{steps[index - 1].node_id}__{p.name}", p.port_type)
                        for p in (steps[index - 1].produces if index else ())
                    ),
                    *(
                        ()
                        if judge == "self" or index > 0
                        else (InputPort("notes", PortType.STRING, "Last verdict", optional=True),)
                    ),
                ),
                session_key=f"{stage_id}-{step.node_id}" if remember else None,
                prompt=_prompt(
                    step,
                    work.ref(),
                    feedback,
                    first=index == 0,
                    judged=judge != "self",
                    upstream=made[-1] if index else None,
                    upstream_ports=steps[index - 1].produces if index else (),
                ),
                declared_outputs=step.produces,
            )
        )
        body.connect_input(work, node, "brief")
        body.route(counter if index == 0 else previous, node)
        if index:
            # A control edge carries no data. Without these the second step of a
            # sequence cannot see what the first produced, and "write, then test"
            # is two unrelated steps that happen to run in order.
            for port in steps[index - 1].produces:
                body.feed(made[-1], port.name, node, f"{steps[index - 1].node_id}__{port.name}")
        made.append(node)
        previous = node

    produced = made[-1]

    # The judge exists before the exits do: the exhausted exit *carries* the
    # last verdict, and a carried value is wired as well as rendered, which a
    # forward reference cannot be. The attempt's prompt still refers forward —
    # that one is only rendered, and the compiler adds its first-pass guard.
    assessor: Node | None = None
    if judge == "model":
        assessor = body.add(
            AgentNode(
                node_id="judge",
                description="Judge the attempt",
                inputs=tuple(InputPort(p.name, p.port_type) for p in last.produces),
                prompt=tpl(judge_prompt, "\n\n", *_readback(produced, last.produces)),
                declared_outputs=(
                    OutputPort(verdict_port, PortType.BOOLEAN, "Whether this is acceptable"),
                    OutputPort("notes", PortType.STRING, "What to fix on the next pass"),
                ),
            )
        )
    elif judge == "human":
        assessor = body.add(
            approval_gate(
                node_id="review",
                description="Review the attempt",
                inputs=tuple(InputPort(p.name, p.port_type) for p in last.produces),
                prompt=tpl(judge_prompt, "\n\n", *_readback(produced, last.produces)),
                reject_label="Reject and revise",
            )
        )
    if assessor is not None:
        for port in last.produces:
            body.connect(produced, port.name, assessor, port.name)
        # The edge that makes this a revise loop rather than a retry loop. The
        # graph looks identical without it and the second pass learns nothing
        # the first did not.
        body.feed(assessor, "notes", made[0], "notes")

    exhausted_when = at_least(counter.ref("value"), passes)
    carried: dict[str, Ref | Template | str] = {p.name: produced.ref(p.name) for p in last.produces}
    hit = scope.exit(
        node_id="converged",
        outcome=CONVERGED,
        reason="Converged",
        **carried,
        **{PASSES: counter.ref("value")},
    )
    gave_up = scope.exit(
        node_id="exhausted",
        outcome=EXHAUSTED,
        reason=f"Still not accepted after {passes} pass(es)",
        **carried,
        **{
            PASSES: counter.ref("value"),
            **({FEEDBACK: assessor.ref("notes")} if assessor is not None else {}),
        },
    )

    # Back to the counter, not to the first attempt: a retry edge that re-enters
    # the loop below the counter leaves it stuck at 1, and the exhausted exit —
    # the whole point of the construct — becomes unreachable.
    retry: Node = counter
    if pause_between is not None:
        retry = body.add(
            wait(node_id="pause", seconds=pause_between, reason="Before the next pass")
        )
        body.route(retry, counter)

    if assessor is None:
        body.route(produced, hit, when=tpl(produced.ref(verdict_port)))
        body.route(produced, gave_up, when=exhausted_when)
        body.route(produced, retry)
    elif judge == "model":
        body.route(assessor, hit, when=tpl(assessor.ref(verdict_port)))
        body.route(assessor, gave_up, when=exhausted_when)
        body.route(assessor, retry)
    else:
        # A gate cannot test a counter — its branches are the human's buttons —
        # so exhaustion is decided one step later, by a zero-cost set node that
        # forwards the rejection into somewhere routable.
        rejected = body.add(
            ComputeNode(
                node_id="rejected",
                description="Rejected; decide whether another pass is left",
                value="rejected",
                value_type=PortType.STRING,
                inputs=(InputPort(COUNTER, PortType.NUMBER),),
                declared_outputs=(OutputPort("value", PortType.STRING, "Rejection marker"),),
            )
        )
        body.feed(counter, "value", rejected, COUNTER)
        assert isinstance(assessor, GateNode)
        body.branch(assessor, {"approved": hit, "rejected": rejected})
        body.route(rejected, gave_up, when=exhausted_when)
        body.route(rejected, retry)
    return scope


def _prompt(
    step: Attempt,
    brief: Ref,
    feedback: Ref,
    *,
    first: bool,
    judged: bool,
    upstream: AgentNode | None = None,
    upstream_ports: Sequence[OutputPort] = (),
) -> Template:
    """The attempt's instruction, with last pass's verdict folded in.

    The feedback block only renders once there is feedback: "a previous attempt
    was rejected with these notes" reads as a lie on the first pass, and the
    model acts on it.
    """
    parts: list[TemplatePart] = [step.prompt, "\n\n", brief]
    if upstream is not None:
        for port in upstream_ports:
            parts += [f"\n\n--- {upstream.node_id}.{port.name} ---\n", upstream.ref(port.name)]
    if first and judged:
        parts += [
            "\n\n",
            optional(
                "A previous attempt was rejected with these notes — address each one:\n",
                feedback,
            ),
        ]
    return tpl(*parts)


def _readback(node: AgentNode, ports: Sequence[OutputPort]) -> list[TemplatePart]:
    """The attempt's outputs, labelled, for whoever is judging them."""
    out: list[TemplatePart] = []
    for port in ports:
        out += [f"{port.name}:\n", node.ref(port.name), "\n\n"]
    return out
