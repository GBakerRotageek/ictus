"""One command, whose failure the caller routes on instead of inheriting.

A script step is the only node kind whose failure had no expression. A gate has
branches, an agent has a validator, a loop has ``converge`` — but a command that
exits non-zero produced a value nothing could read, and a command that died
produced an exception that ended the run.

Three facts about Conductor decide the shape, and each one was a bug here:

* **A non-zero exit is not a failure to Conductor.** ``executor/script.py``
  returns ``exit_code`` alongside stdout and stderr, and nothing in
  ``engine/workflow.py`` branches on it. The next step runs. For "reset the
  database, then apply the admin configuration" that is the expensive shape:
  the restore fails, the configuration lands on the database the restore was
  supposed to replace, and the run reports success.
* **Declaring ``output:`` raises before routing.** The engine parses stdout as
  JSON and raises when it is not an object carrying the declared fields — and
  that lands *before* ``_evaluate_routes``, so a route written for a failing
  command, which owes nobody JSON, never fires.
* **Stdout can overwrite the status.** Whenever stdout parses as a JSON object
  the engine merges it over ``{stdout, stderr, exit_code}``. The previous
  version emitted no ``output:`` to avoid the second fact and so routed on a
  field its command could write: reproduced on a live run, a command printing
  ``{"exit_code": 0}`` and exiting 1 reported ``ok``, and one printing
  ``"exit_code": 7`` among its fields and exiting 0 reported ``failed``.

So the status and the fields come from two steps, because one step cannot give
both. ``run`` sets ``trusted_status``, which the Conductor backend lowers so the
command's stdout cannot reach its result (``interfaces/conductor/status.py``),
and whose only schema is that result, so a failing command routes normally. Only once that
status says 0 does ``<node_id>_fields`` pipe the command's real stdout through
``cat`` with the declared ``outputs`` as its schema — Conductor's own parser and
validator, applied on the one branch where the command promised the fields.

What stays outside the outcomes: a command that never started, and one that ran
past ``timeout``. The reporter prints nothing for either, ``run``'s schema
refuses that, and the run ends. A missing binary is also worth
``require_executable``, so ``ictus preflight`` refuses it before anything runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import PROCESS_RESULT
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import equals, tpl
from ictus.graph.scope import outcome_scope
from ictus.stdlib.steps.shell import shell

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.pipeline import WorkflowInput
    from ictus.graph.ref import Ref, Template
    from ictus.graph.scope import Scope

__all__ = ["FAILED", "OK", "try_shell"]

OK = "ok"
FAILED = "failed"

STDOUT = "stdout"
EXIT_CODE = "exit_code"

#: What the command's own step always carries, whatever it printed. Both exits
#: carry all of it, so a caller reading them cannot hit an undefined variable.
BASELINE: dict[str, PortType] = dict(PROCESS_RESULT)

#: Reads JSON on stdin and prints it back, so the engine parses it as a step's
#: stdout. Byte-exact, which a Python relay in text mode is not: universal
#: newlines would rewrite a ``\r\n`` inside the payload.
FIELD_READER = "cat"


def try_shell(
    *,
    stage_id: str,
    command: str,
    args: Sequence[str | Template] = (),
    node_id: str = "run",
    parameter: str | None = None,
    outputs: Sequence[OutputPort] = (),
    stdin: str | Template | None = None,
    timeout: int | None = None,
    working_dir: str | None = None,
    description: str = "",
) -> Scope:
    """Run one command; report ``ok`` or ``failed`` rather than raising.

    Both outcomes carry ``stdout``, ``stderr`` and ``exit_code``, so the branch
    that handles the failure can say what went wrong instead of only that
    something did. ``exit_code`` is the process's own: nothing the command
    prints can change which outcome is taken.

    ``parameter`` declares an input the caller wires, appended to ``args`` as
    the command's last argument — the shape ``script_sequence`` uses, so an
    environment name or a backup path reaches the command without the graph
    holding a value it has no business knowing.

    ``outputs`` names JSON fields the command prints, addressable on the ``ok``
    branch. **Print every one of them whenever the command exits 0.** They are
    parsed in a step of their own, after the exit status has said 0, and a
    missing or mistyped field ends the run there, naming the field. The
    ``failed`` exit never parses them: they arrive there as empty values of
    their declared type, because a key on one branch and absent on the other is
    a template error waiting for the branch nobody exercised.

    On Conductor the command runs under ``python3``, which ``ictus preflight``
    checks for; see ``ScriptNode.trusted_status`` for why. A command that never starts, or
    runs past ``timeout``, ends the run instead of reaching either outcome.

    Contract: optional input named by ``parameter``; outcomes ``ok`` and
    ``failed``.
    """
    reserved = sorted({port.name for port in outputs} & set(BASELINE))
    if reserved:
        raise CompositionError(
            f"try_shell {stage_id!r} declares {reserved} in outputs, which the scope already "
            "carries as the process's own result. A JSON field of that name would share a "
            "name with it on the `ok` exit — rename the field the command prints."
        )
    if parameter is not None and not parameter.strip():
        raise CompositionError(f"try_shell {stage_id!r} has a malformed parameter {parameter!r}")

    carry: dict[str, PortType | OutputPort] = {**BASELINE}
    carry.update({port.name: port for port in outputs})

    scope = outcome_scope(
        stage_id=stage_id,
        outcomes=(OK, FAILED),
        carry=carry,
        description=description or f"Run {command}, reporting whether it worked",
    )
    body = scope.body

    param: WorkflowInput | None = None
    threaded: tuple[str | Template, ...] = ()
    inputs: tuple[InputPort, ...] = ()
    if parameter is not None:
        param = body.declare_input(
            parameter, PortType.STRING, description=f"Passed to {command} as its last argument"
        )
        threaded = (tpl(param.ref()),)
        inputs = (InputPort(parameter, PortType.STRING),)

    run = body.add(
        shell(
            node_id=node_id,
            description=f"Run {command}",
            command=command,
            args=(*args, *threaded),
            inputs=inputs,
            stdin=stdin,
            timeout=timeout,
            working_dir=working_dir,
            # The whole point of the step: what it routes on is the process's own
            # result, not whatever its stdout says the result was.
            trusted_status=True,
        )
    )
    body.set_entry(run)
    if param is not None:
        body.connect_input(param, run, param.name)

    baseline: dict[str, Ref | Template | str] = {name: run.ref(name) for name in BASELINE}
    broke = scope.exit(
        node_id=FAILED,
        outcome=FAILED,
        reason=tpl(f"{command} exited with status ", run.ref(EXIT_CODE)),
        **baseline,
    )

    # Success is the *tested* branch and failure the catch-all, which is the
    # only arrangement that is right for both ends of the range. A child killed
    # by a signal exits `-N` — asyncio reports SIGKILL as -9 — so "failed" spelt
    # as `exit_code >= 1` reads a killed command as a clean one, which is the
    # exact bug this scope exists to remove.
    succeeded = equals(run.ref(EXIT_CODE), 0)
    if not outputs:
        worked = scope.exit(node_id=OK, outcome=OK, reason=f"{command} succeeded", **baseline)
        body.route(run, worked, when=succeeded)
        body.route(run, broke)
        return scope

    fields = body.add(
        shell(
            node_id=f"{node_id}_fields",
            description=f"Read the fields {command} printed",
            command=FIELD_READER,
            inputs=(InputPort(STDOUT, PortType.STRING, "What the command printed"),),
            stdin=tpl(run.ref(STDOUT)),
            outputs=tuple(outputs),
            # Enforced here and nowhere else: this step only runs once the command
            # exited 0, which is when it promised the fields. A missing one ends
            # the run on this step, named, instead of at whichever template reads
            # it later.
            enforce_outputs=True,
        )
    )
    body.feed(run, STDOUT, fields, STDOUT)
    worked = scope.exit(
        node_id=OK,
        outcome=OK,
        reason=f"{command} succeeded",
        **baseline,
        **{port.name: fields.ref(port.name) for port in outputs},
    )
    body.route(run, fields, when=succeeded)
    body.route(run, broke)
    body.route(fields, worked)
    return scope
