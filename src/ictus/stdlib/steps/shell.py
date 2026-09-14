"""A subprocess step. Conductor ``type: script`` — deterministic, no model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import ScriptNode
from ictus.graph.ports import OutputPort, PortType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort
    from ictus.graph.ref import Template

__all__ = ["shell"]


def shell(
    *,
    node_id: str,
    command: str,
    args: Sequence[str | Template] = (),
    outputs: Sequence[OutputPort] = (),
    description: str = "",
    inputs: Sequence[InputPort] = (),
    stdin: str | Template | None = None,
    timeout: int | None = None,
    working_dir: str | None = None,
    enforce_outputs: bool = True,
    trusted_status: bool = False,
) -> ScriptNode:
    """Run a command, with no model in the loop.

    Three things bite here:

    * A relative ``command`` resolves against ``working_dir``, which defaults to
      **the process's current directory** — not the repo root, and not the
      directory the workflow file lives in. Set ``working_dir`` when you want
      that to be predictable; otherwise the command is only correct relative to
      wherever the run happens to be launched from.
    * ``args`` goes on the command line and hits the OS length cap. Pass large
      payloads through ``stdin``.

    * Declaring ``outputs`` makes stdout a **contract, not a log**: the command
      must print a JSON object, and Conductor raises "declares an output schema
      but stdout is not valid JSON" if it does not — after the command has
      already run and done whatever it does. Leave ``outputs`` empty for a
      command that prints prose, and send progress to stderr.

    A stdout object is merged over ``{stdout, stderr, exit_code}``, so declared
    ``outputs`` can name its fields directly.

    ``enforce_outputs=False`` keeps the ports and drops the contract: references
    are still typed at composition, and the baseline three are still readable by
    a route, but nothing is checked once the command has run. The contract's
    raise lands before routes are evaluated, so with it on a dying command ends
    the run rather than taking a branch.

    Off does not make ``exit_code`` trustworthy. A JSON stdout is still merged
    over it, so a command that prints ``{"exit_code": 0}`` and exits 1 routes as
    a success. ``trusted_status=True`` is what does: the three values are then
    the process's own, and the backend lint refuses a route on them from a step
    without it. The step then has no stdout fields — leave ``outputs`` empty and
    it declares the three for you. ``stdlib.try_shell`` is the whole shape, with
    fields parsed in a second step once the status says 0.
    """
    if trusted_status and not outputs:
        outputs = (
            OutputPort("stdout", PortType.STRING, "What the command printed"),
            OutputPort("stderr", PortType.STRING, "What it printed to stderr"),
            OutputPort("exit_code", PortType.NUMBER, "The status it exited with"),
        )
    return ScriptNode(
        node_id=node_id,
        description=description,
        inputs=tuple(inputs),
        command=command,
        args=tuple(args),
        stdin=stdin,
        timeout=timeout,
        working_dir=working_dir,
        declared_outputs=tuple(outputs),
        enforce_outputs=enforce_outputs,
        trusted_status=trusted_status,
    )
