"""A script step whose result a route can trust, on an engine that does not give one.

Conductor stores ``{stdout, stderr, exit_code}`` for a script step and then
*updates* that dict with stdout whenever stdout parses as a JSON object
(``engine/workflow.py``, ``output_content.update(parsed_json)``). No flag turns
it off and nothing else keeps the process's own values. So a command that prints
``{"exit_code": 0}`` and exits 1 takes a route written for success — reproduced
on live runs of ``poll_until`` and ``try_shell`` — and a clean exit whose fields
include ``"exit_code": 7`` takes the one written for failure.

Everything about the remedy lives here, because all of it exists only because of
that merge. A ``ScriptNode`` with ``trusted_status`` is lowered to run the
command under ``python3`` and a small reporter, which prints the three values as
the step's only JSON object. The command's stdout travels inside it as a string
and is never merged; the reporter writes last, so the status is the process's.

Python rather than ``/bin/sh``, because a shell cannot JSON-encode arbitrary
output. The shell alternative — a marker byte that makes stdout unparseable —
needs every reader of ``stdout`` to strip it, and turns a missing binary into
exit 127, which a caller would route as an ordinary failure.

The step always declares the whole result as its schema. For a command that
never started, or ran past its timeout, the reporter prints nothing, so
Conductor's validation raises before any route is evaluated: the run ends. The
reporter writes which it was to stderr, which the event log keeps; the engine's
headline error is "stdout is not valid JSON".

Three consumers: the lowering (``agents.kind_fields``), the lint that refuses a
route on an untrusted result, and preflight, which asks for the interpreter the
backend chose — nobody writing the pipeline declared it, so nobody else will.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import PROCESS_RESULT, Node, ScriptNode
from ictus.graph.ports import OutputPort
from ictus.graph.ref import Origin
from ictus.graph.requirements import Executable
from ictus.lint.rules import describe

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline, RouteEnd

__all__ = [
    "REPORTER",
    "REPORTER_INTERPRETER",
    "TIMEOUT_GRACE",
    "reported_args",
    "reported_timeout",
    "reporter_requirement",
    "result_ports",
    "trusted_status_problems",
]

REPORTER_INTERPRETER = "python3"

#: Runs ``argv[2:]`` and prints its result as the step's only JSON object.
#:
#: Written without a brace anywhere, because Conductor renders every script
#: argument through Jinja and expands ``${...}`` at load: ``dict(...)`` rather
#: than a literal is what keeps this a program and not a template. The child gets
#: its own session so a timeout kills everything it started, not just the direct
#: child — otherwise a grandchild holding the pipe open hangs ``communicate``.
#: stdin is inherited, so a payload Conductor pipes to the step reaches the child.
REPORTER = """\
import json, os, signal, subprocess, sys
limit = float(sys.argv[1]) if sys.argv[1] else None
command = sys.argv[2:]
try:
    child = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
except OSError as exc:
    sys.stderr.write("could not start %s: %s\\n" % (command[0], exc))
    sys.exit(127)
try:
    out, err = child.communicate(timeout=limit)
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGKILL)
    child.communicate()
    sys.stderr.write("%s timed out after %s seconds\\n" % (command[0], sys.argv[1]))
    sys.exit(124)
status = child.returncode
sys.stdout.write(json.dumps(dict(
    stdout=out.decode("utf-8", "replace"),
    stderr=err.decode("utf-8", "replace"),
    exit_code=status,
)))
sys.exit(status if status >= 0 else 128 - status)
"""

#: How long past the reporter's own limit Conductor waits before killing it.
#: A backstop only: Conductor's kill reaches the reporter and not the command,
#: so the reporter has to be the one that enforces ``timeout``.
TIMEOUT_GRACE = 10


def reported_args(command: str, args: list[str], timeout: int | None) -> list[str]:
    """The arguments that run ``command`` behind the reporter, in order."""
    return ["-c", REPORTER, "" if timeout is None else str(timeout), command, *args]


def reported_timeout(timeout: int | None) -> int | None:
    """The engine's own limit: past the reporter's, or none if the step set none."""
    return None if timeout is None else timeout + TIMEOUT_GRACE


def result_ports(node: ScriptNode) -> tuple[OutputPort, ...]:
    """The schema a trusted step is emitted with: always all three.

    All three whatever the node declared, because the reporter prints all three
    for every command that ran — and the schema is what refuses one that did not.
    A declared port keeps its description.
    """
    declared = {port.name: port for port in node.declared_outputs}
    return tuple(
        declared.get(name, OutputPort(name, port_type))
        for name, port_type in PROCESS_RESULT.items()
    )


def trusted_status_problems(pipeline: Pipeline) -> list[str]:
    """A route on a script step's result that the command could have written.

    Where the condition sits does not matter — a later step routing on an
    earlier script's ``exit_code`` reads the same merged value. A field the
    command prints is not this rule: stdout is entitled to write those.
    """
    where = pipeline.pipeline_id
    scripts = {n.node_id: n for n in pipeline.nodes if isinstance(n, ScriptNode)}
    routers: tuple[RouteEnd, ...] = (*pipeline.nodes, *pipeline.groups, *pipeline.maps)
    problems: list[str] = []
    for router in routers:
        seen: set[tuple[str, str]] = set()
        for edge in pipeline.outgoing(router):
            for ref in edge.condition_refs():
                script = scripts.get(ref.source_id)
                if (
                    ref.origin is not Origin.NODE
                    or ref.port not in PROCESS_RESULT
                    or script is None
                    or script.trusted_status
                    or (ref.source_id, ref.port) in seen
                ):
                    continue
                seen.add((ref.source_id, ref.port))
                subject = (
                    describe(router) if isinstance(router, Node) else f"group {router.node_id!r}"
                )
                problems.append(
                    f"{where}: {subject} routes on {ref.source_id}.{ref.port}, which the "
                    f"command can overwrite. Conductor merges a JSON stdout over a script "
                    f"step's own stdout, stderr and exit_code, so a command that prints "
                    f'{{"exit_code": 0}} and exits 1 takes the success branch. Declare '
                    f"{ref.source_id!r} with trusted_status=True, or use stdlib.try_shell."
                )
    return problems


def reporter_requirement(pipeline: Pipeline) -> Executable | None:
    """The interpreter this backend lowers trusted steps onto, if any need it.

    Across nested stages: a stage runs in its caller's environment, so what it
    needs is the launch's problem, the same as a declared executable.
    """
    if not _has_trusted_step(pipeline):
        return None
    return Executable(
        name=REPORTER_INTERPRETER,
        purpose=(
            "a script step with trusted_status runs under a reporter, so the exit status "
            "a route reads cannot be overwritten by what the command prints"
        ),
        probe=("--version",),
        setup_hint="Install Python 3 and put python3 on PATH.",
    )


def _has_trusted_step(pipeline: Pipeline) -> bool:
    if any(isinstance(n, ScriptNode) and n.trusted_status for n in pipeline.nodes):
        return True
    return any(_has_trusted_step(child) for child in pipeline.children.values())
