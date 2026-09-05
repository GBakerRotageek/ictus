"""Write a value another step produced to a file.

Conductor has no way to write a file declaratively — an agent's output lives in
the run's context and is printed as JSON at the end, and that is all. So a
pipeline whose point is to produce a document had nowhere to put it.

This is a ``script`` step, which is the mechanism the engine does have. Two
details make it safe rather than merely working:

* **The text goes in on stdin, never into the command line.** A report
  containing backticks, ``$(...)``, quotes or newlines is a payload, not shell
  source, and interpolating it into a ``sh -c`` string would make a model's
  output executable. The path travels as an argv element for the same reason.
* **Parent directories are created.** Otherwise the last step of a council that
  cost real money fails on a missing ``reports/`` and the report is gone.

The file lands relative to the run's working directory — the project you
launched against — because a script step's ``working_dir`` is the process cwd.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import ScriptNode
from ictus.graph.ports import OutputPort, PortType
from ictus.graph.ref import Ref, Template, tpl

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort

__all__ = ["save_text"]

# `$1` is the path, passed as an argument rather than spliced into the script.
# `sh` is $0 so the path lands in $1 where the script expects it.
#
# stdout is a JSON object because a script that declares `output:` must print
# one — Conductor parses it and raises "declares an output schema but stdout is
# not valid JSON" otherwise, *after* the file has already been written. The path
# is escaped through sed rather than interpolated, so one containing a quote
# still produces valid JSON.
_ESCAPED = r"""$(printf %s "$1" | sed 's/\\/\\\\/g;s/"/\\"/g')"""
_REPORT = f'printf \'{{"path":"%s"}}\' "{_ESCAPED}"'
_WRITE = f'mkdir -p "$(dirname "$1")" && cat > "$1" && {_REPORT}'
_APPEND = f'mkdir -p "$(dirname "$1")" && cat >> "$1" && {_REPORT}'


def save_text(
    *,
    node_id: str,
    text: Ref | Template | str,
    to: str | Template,
    append: bool = False,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    working_dir: str | None = None,
) -> ScriptNode:
    """Write ``text`` to the file at ``to``.

    ``to`` may be a template, so a path can carry a value from the run — one
    file per item in a fan-out, or a name taken from the ticket being worked on.

    Costs one iteration and no provider call. The written path comes back as
    ``path``, so a terminal can tell the person where to look rather than
    leaving them to guess.

    Wire ``text``'s source with ``feed`` or ``connect`` and declare it in
    ``inputs``: under ``context.mode: explicit`` a value this step does not
    declare is not in scope, and the file would be written empty.
    """
    if isinstance(to, str) and not to.strip():
        raise CompositionError(f"save_text {node_id!r} needs somewhere to write")
    payload = tpl(text) if isinstance(text, Ref) else text
    return ScriptNode(
        node_id=node_id,
        description=description or f"Write {to if isinstance(to, str) else 'a file'}",
        inputs=tuple(inputs),
        command="sh",
        args=("-c", _APPEND if append else _WRITE, "sh", to),
        stdin=payload,
        working_dir=working_dir,
        declared_outputs=(OutputPort("path", PortType.STRING, "The file that was written"),),
    )
