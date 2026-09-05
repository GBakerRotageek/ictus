"""Check, wait, check again — until something external becomes true."""

from __future__ import annotations

from ictus.graph.node import AgentNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import tpl
from ictus.graph.stage import Stage
from ictus.stdlib.steps.wait import wait
from ictus.stdlib.terminals.succeed import succeed

__all__ = ["poll_until"]


def poll_until(
    *,
    stage_id: str,
    condition: str,
    check_prompt: str,
    seconds_between: float = 30.0,
    max_polls: int = 5,
    description: str = "",
) -> Stage:
    """Poll until a condition holds, pausing between attempts.

    The pause is a ``type: wait`` step, not a model call — but it still costs an
    iteration, so N polls need a budget of roughly 2N. ``max_polls`` becomes the
    loop bound and the derived ``max_iterations`` accounts for both nodes.

    The check emits an unconditional catch-all route to the wait step, after the
    conditional route to the exit. Order matters: Conductor takes the first
    matching route, so a catch-all written first would swallow the exit
    condition. ictus emits conditional routes first regardless of authoring
    order, which is why that cannot be got wrong here.

    Contract: input ``target`` (string) in, output ``status`` (string) out.
    """
    stage = Stage(
        stage_id=stage_id,
        description=description or f"Poll until {condition}",
        loop_passes=max_polls,
    )
    target = stage.body.declare_input("target", PortType.STRING, description="What to check")

    check = stage.body.add(
        AgentNode(
            node_id="check",
            description=f"Check whether {condition}",
            inputs=(InputPort("target", PortType.STRING),),
            prompt=tpl(f"{check_prompt}\n\nTarget: ", target.ref()),
            declared_outputs=(
                OutputPort("ready", PortType.BOOLEAN, condition),
                OutputPort("status", PortType.STRING, "What was observed"),
            ),
        )
    )
    pause = stage.body.add(
        wait(
            node_id="pause",
            seconds=seconds_between,
            reason=f"Waiting for {condition}",
        )
    )
    ready = stage.body.add(succeed(node_id="ready", reason=f"Condition met: {condition}"))

    stage.body.set_entry(check)
    stage.body.connect_input(target, check, "target")
    stage.body.route(check, ready, when="{{ check.output.ready }}")
    stage.body.route(check, pause)
    stage.body.route(pause, check)
    stage.body.expose_output("status", check, "status")
    return stage
