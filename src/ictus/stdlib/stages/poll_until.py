"""Wait for something to come up, and report giving up rather than raising.

A poll is ``converge`` with the model taken out. Every attempt there is a
provider call, which is the wrong price for "is the deployment healthy yet" —
that question has a command that answers it, deterministically, for nothing.

Three details are what make this worth a constructor rather than five hand-wired
nodes:

* **Success is tested before exhaustion.** Conductor takes the first matching
  route, so with the order the other way round a check that finally succeeds on
  the last permitted attempt reports ``exhausted`` — the one run where the
  distinction matters most.
* **The wait is on the catch-all.** Exhaustion is tested ahead of it, so nothing
  sleeps after the last attempt. A trailing wait is invisible in a test and
  costs a whole interval on every give-up.
* **The bound has to be a value.** A loop that runs out of Conductor's iteration
  budget raises ``MaxIterationsError``, which ``_run_child_engine`` does not
  catch, so it detonates the caller past every route the caller declared. The
  attempts are counted in a ``set`` step and routed on, and the budget derived
  from ``loop_passes`` leaves room for the exhausted exit to run.

**The status is the process's, not the command's say-so.** Conductor merges a
JSON stdout over a script step's own ``exit_code``, so a check that prints
``{"exit_code": 0}`` and exits 1 came back ``ready`` on a live run. The check
sets ``trusted_status``, which the Conductor backend lowers behind a ``python3``
reporter whose output the command cannot write to — see
``interfaces/conductor/status.py`` for the mechanism and why it is Python.

**Two failures stay outside the outcomes**: a command that never started (a
missing binary, a permission error) and a check that ran past ``timeout``. The
reporter prints nothing for either, and the check's declared schema refuses that
before any route is evaluated — the run ends rather than arriving as
``exhausted``. ``timeout`` therefore bounds one attempt, not the poll; use
``max_attempts`` for that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import Ref, Template, at_least, equals, tpl
from ictus.graph.scope import outcome_scope
from ictus.stdlib.stages.converge import EXHAUSTED
from ictus.stdlib.stages.try_shell import BASELINE, EXIT_CODE
from ictus.stdlib.steps.counter import counter as counter_step
from ictus.stdlib.steps.shell import shell
from ictus.stdlib.steps.wait import wait

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.pipeline import WorkflowInput
    from ictus.graph.scope import Scope

__all__ = ["EXHAUSTED", "READY", "poll_until"]

READY = "ready"
"""The command answered 0 within the permitted attempts."""

# `EXHAUSTED` is re-exported rather than re-spelled: it is the same word for the
# same thing converge reports, and a caller that handles one handles the other.

ATTEMPTS = "attempts"
COUNTER = "attempt_number"


def poll_until(
    *,
    stage_id: str,
    command: str,
    max_attempts: int,
    interval_seconds: float,
    args: Sequence[str | Template] = (),
    node_id: str = "check",
    parameter: str | None = None,
    timeout: int | None = None,
    working_dir: str | None = None,
    description: str = "",
) -> Scope:
    """Run ``command`` until it exits 0, at most ``max_attempts`` times.

    The first check happens immediately; ``interval_seconds`` is the gap
    *between* attempts, so a poll of N attempts waits N-1 times and a poll of one
    never waits at all.

    Outcomes are ``ready`` and ``exhausted``. Both carry ``stdout``, ``stderr``,
    ``exit_code`` and ``attempts``, so the branch that gives up can say what the
    last check actually saw rather than only that it never came good.

    ``timeout`` bounds a single check and is not part of the outcome: a check
    that runs past it, or a command that cannot start, ends the run rather than
    arriving as ``exhausted``. Both are refused by the check's own declared
    schema, which the reporter leaves unfilled for exactly those two cases — see
    the module docstring for why the check runs through ``python3`` at all.

    ``parameter`` declares an input the caller wires, appended to ``args`` as the
    command's last argument — the shape ``try_shell`` and ``script_sequence``
    use, so a host name or a namespace reaches the command without the graph
    holding a value it has no business knowing.

    Contract: optional input named by ``parameter``; outcomes ``ready`` and
    ``exhausted``.
    """
    if max_attempts < 1:
        raise CompositionError(
            f"poll_until {stage_id!r} needs max_attempts >= 1, got {max_attempts}"
        )
    if interval_seconds <= 0:
        raise CompositionError(
            f"poll_until {stage_id!r} needs a positive interval_seconds, got "
            f"{interval_seconds}. Zero is not 'poll fast' — it is a loop that spends its "
            "whole budget in the time the first check took."
        )
    if parameter is not None and not parameter.strip():
        raise CompositionError(f"poll_until {stage_id!r} has a malformed parameter {parameter!r}")

    carry: dict[str, PortType | OutputPort] = {**BASELINE, ATTEMPTS: PortType.NUMBER}
    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=(READY, EXHAUSTED),
        carry=carry,
        description=description or f"Poll {command} until it reports ready",
        loop_passes=max_attempts,
    )
    body = scope.body

    param: WorkflowInput | None = None
    threaded: tuple[str | Template, ...] = ()
    declared: tuple[InputPort, ...] = ()
    if parameter is not None:
        param = body.declare_input(
            parameter, PortType.STRING, description=f"Passed to {command} as its last argument"
        )
        threaded = (tpl(param.ref()),)
        declared = (InputPort(parameter, PortType.STRING),)

    counter = body.add(counter_step(node_id=COUNTER, description="Which attempt this is"))
    body.set_entry(counter)
    body.feed(counter, "value", counter, COUNTER)

    check = body.add(
        shell(
            node_id=node_id,
            description=f"Ask {command} whether it is ready",
            command=command,
            args=(*args, *threaded),
            # The counter is declared because this step *routes* on it, and a
            # route renders in the routing step's own scope: without it the first
            # pass dies on "'attempt_number' is undefined".
            inputs=(*declared, InputPort(COUNTER, PortType.NUMBER, "Which attempt this is")),
            timeout=timeout,
            working_dir=working_dir,
            trusted_status=True,
        )
    )
    body.connect(counter, "value", check, COUNTER)
    if param is not None:
        body.connect_input(param, check, param.name)

    last: dict[str, Ref | Template | str] = {name: check.ref(name) for name in BASELINE}
    spent = {ATTEMPTS: counter.ref("value")}
    came_up = scope.exit(
        node_id=READY,
        outcome=READY,
        reason=tpl(f"{command} reported ready on attempt ", counter.ref("value")),
        **last,
        **spent,
    )
    gave_up = scope.exit(
        node_id=EXHAUSTED,
        outcome=EXHAUSTED,
        reason=f"{command} was still not ready after {max_attempts} attempt(s)",
        **last,
        **spent,
    )
    pause = body.add(
        wait(node_id="pause", seconds=interval_seconds, reason="Before the next attempt")
    )
    body.route(pause, counter)

    # Order is the construct. Success first, so a check that comes good on the
    # final permitted attempt is `ready` rather than `exhausted`; exhaustion
    # next, so it is decided before the catch-all that waits; the wait last, so
    # nothing sleeps after the last attempt. `route_entries` keeps this order
    # and moves the unconditional route to the end, which is where it already is.
    body.route(check, came_up, when=equals(check.ref(EXIT_CODE), 0))
    body.route(check, gave_up, when=at_least(counter.ref("value"), max_attempts))
    body.route(check, pause)
    return scope
