"""A subprocess step. Conductor ``type: script`` — deterministic, no model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import ScriptNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort, OutputPort

__all__ = ["shell"]


def shell(
    *,
    node_id: str,
    command: str,
    args: Sequence[str] = (),
    outputs: Sequence[OutputPort] = (),
    description: str = "",
    inputs: Sequence[InputPort] = (),
    stdin: str | None = None,
    timeout: int | None = None,
    working_dir: str | None = None,
) -> ScriptNode:
    """Run a command, with no model in the loop.

    Two things bite here:

    * A relative ``command`` resolves against ``working_dir``, which defaults to
      **the process's current directory** — not the repo root, and not the
      directory the workflow file lives in. Set ``working_dir`` when you want
      that to be predictable; otherwise the command is only correct relative to
      wherever the run happens to be launched from.
    * ``args`` goes on the command line and hits the OS length cap. Pass large
      payloads through ``stdin``.

    Stdout that parses as JSON is merged over ``{stdout, stderr, exit_code}``,
    so declared ``outputs`` can name those JSON fields directly.
    """
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
    )
