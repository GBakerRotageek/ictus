"""Tell somebody something, as a step in the graph. No model call.

The other half of reporting. ``ictus watch`` subscribes to a run from outside
and catches what no step can see — a budget tripping, the engine being killed,
the run dying. This is for the rest, which is most of it: say something *here*,
at this point in the graph, on the way past.

Being a node is the whole advantage. It is costed against ``max_iterations``,
it is routed like anything else, it shows up in the dashboard and in ``ictus
trace`` — and if the endpoint is wrong the step fails visibly rather than a
message quietly not arriving. Put one before a gate and the gate's opening is
announced; put one after and the answer is.

The URL never enters the emitted workflow. The step names an environment
variable and reads it in the subprocess, so what is committed carries the
variable's name and nothing else.

Python rather than ``curl``: it is already required to run ictus, and it builds
the JSON body properly. A shell interpolating a prompt into a string would break
on the first apostrophe, and a gate prompt is prose written by a person.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import ScriptNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ports import InputPort
    from ictus.graph.ref import Template
    from ictus.graph.requirements import EnvVar

__all__ = ["announce"]

#: Reads the message on stdin, posts it as `{"text": ...}`, says so if it cannot.
_POST = (
    "import json,os,sys,urllib.request as r\n"
    "url=os.environ.get({var!r})\n"
    "if not url:\n"
    "    sys.exit({var!r} + ' is not set, so there was nowhere to report')\n"
    "body=json.dumps({{'text':sys.stdin.read()}}).encode()\n"
    "req=r.Request(url,data=body,headers={{'Content-Type':'application/json'}})\n"
    "r.urlopen(req,timeout={timeout}).read()\n"
    "print('reported')\n"
)


def announce(
    *,
    node_id: str,
    text: str | Template,
    to: EnvVar,
    description: str = "",
    inputs: Sequence[InputPort] = (),
    timeout: int = 15,
) -> ScriptNode:
    """Post ``text`` to the webhook URL held in the ``to`` environment variable.

    ``text`` is a template, so it can carry what an earlier step produced — the
    point of announcing from inside the graph rather than outside it is that the
    message can say what just happened.

    The step fails if the variable is unset. That is deliberate and it is the
    node's advantage over a subscriber: a misconfigured report is a red step in
    the dashboard, not a message nobody notices never arrived. Declare the same
    variable on a ``Notifier`` as well to have preflight refuse the run before
    it starts.
    """
    if not to.name:
        raise CompositionError(f"announce node {node_id!r} needs a variable to read the URL from")
    if timeout < 1:
        raise CompositionError(f"announce node {node_id!r} needs a timeout of at least 1s")
    return ScriptNode(
        node_id=node_id,
        description=description or f"Report to ${to.name}",
        inputs=tuple(inputs),
        command="python3",
        args=("-c", _POST.format(var=to.name, timeout=timeout)),
        stdin=text,
        timeout=timeout + 5,
        # stdout is prose, not a contract: enforcing it would make the engine
        # parse "reported" as JSON and raise after the message had already gone.
        enforce_outputs=False,
    )
