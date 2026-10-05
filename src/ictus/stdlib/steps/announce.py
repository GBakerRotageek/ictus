"""Tell somebody something, as a step in the graph. No model call.

The other half of reporting. ``ictus watch`` subscribes from outside a run and
catches what no step can see — a budget tripping, the engine being killed. This
is for the rest, which is most of it: say something *here*, on the way past.

Being a node is the advantage. It is costed against ``max_iterations``, routed
like anything else, visible in the dashboard and in ``ictus trace`` — and a
wrong endpoint is a red step rather than a message nobody notices never arrived.

**Threading needs a bot token.** An incoming webhook accepts ``thread_ts`` but
never returns the ``ts`` of what it posted, so there is no way to learn the
parent to reply under. Give ``channel`` and the step posts through
``chat.postMessage``, which answers with the timestamp; leave it off and the
step posts to a webhook and every run's messages interleave in the channel.

The first message of a run carries the pipeline's name and the engine's own
``CONDUCTOR_RUN_ID`` — the same id ``ictus trace`` and the fleet records use, so
a message in a channel and a run on a machine can be matched up. Replies carry
neither: the thread already says which run they belong to.

No credential reaches the emitted workflow. The step names environment variables
and reads them in the subprocess.

Python rather than ``curl``: it is already required to run ictus, it builds the
JSON properly, and it can read the reply to find the thread. A shell
interpolating a gate prompt would break on the first apostrophe.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import GateNode, ScriptNode
from ictus.graph.ports import InputPort, OutputPort, PortType
from ictus.graph.ref import as_template

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.ref import Ref, Template
    from ictus.graph.requirements import EnvVar

__all__ = ["THREAD_PORT", "announce"]

#: What a thread-opening announcement publishes, and a reply reads.
THREAD_PORT = "thread_ts"

API = "https://slack.com/api/chat.postMessage"

#: Overrides it. A seam for a proxy or an Enterprise Grid host, and what lets
#: the smoke test prove threading against a local stand-in.
API_ENV = "SLACK_API_URL"

#: Reads the message on stdin and the parent thread, if any, from argv.
_POST = """import json, os, sys, urllib.request as r

text = sys.stdin.read()
parent = sys.argv[1].strip() if len(sys.argv) > 1 else ""
asks = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {{}}
secret = os.environ.get({secret!r})
if not secret:
    sys.exit({secret!r} + " is not set, so there was nowhere to report")

if not parent:
    # The root message is the only one that has to identify itself; a reply is
    # already under it.
    run = os.environ.get("CONDUCTOR_RUN_ID", "")
    head = "*{label}*" if "{label}" else ""
    if run:
        head = (head + " · " if head else "") + "run `" + run + "`"
    if head:
        text = head + chr(10) + text

channel = os.environ.get({channel!r}) if {channel!r} else ""
if {channel!r} and not channel:
    sys.exit({channel!r} + " is not set, so there was no channel to post in")

if channel:
    body = {{"channel": channel, "text": text}}
    if parent:
        body["thread_ts"] = parent
    if asks:
        # A button carries everything the responder needs to answer the right
        # gate on the right run: the run id is only knowable here, at run time.
        run_now = os.environ.get("CONDUCTOR_RUN_ID", "")
        body["blocks"] = [
            {{"type": "section", "text": {{"type": "mrkdwn", "text": text}}}},
            {{
                "type": "actions",
                "elements": [
                    {{
                        "type": "button",
                        "text": {{"type": "plain_text", "text": label}},
                        "value": json.dumps(
                            {{"run": run_now, "gate": asks["gate"], "choice": value}}
                        ),
                        "action_id": "ictus_gate_" + value,
                    }}
                    for value, label in asks["buttons"]
                ],
            }},
        ]
    endpoint = os.environ.get("SLACK_API_URL") or {api!r}
    req = r.Request(
        endpoint,
        data=json.dumps(body).encode(),
        headers={{
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer " + secret,
        }},
    )
    answer = json.loads(r.urlopen(req, timeout={timeout}).read())
    if not answer.get("ok"):
        why = str(answer.get("error"))
        fix = {{
            "missing_scope": "the bot token needs chat:write - add it under OAuth & "
            "Permissions, then reinstall the app to the workspace",
            "not_in_channel": "the app is not in that channel - /invite it there",
            "channel_not_found": "check the channel id; it looks like C0ABC123 and is "
            "at the bottom of View channel details",
            "invalid_auth": "the token is wrong or has been revoked",
            "account_inactive": "the app has been disabled in that workspace",
            "token_revoked": "the token has been rotated; export the new one",
        }}.get(why, "")
        sys.exit("slack refused the message: " + why + ((" - " + fix) if fix else ""))
    print(json.dumps({{"thread_ts": answer.get("ts", ""), "posted": "true"}}))
else:
    req = r.Request(
        secret,
        data=json.dumps({{"text": text}}).encode(),
        headers={{"Content-Type": "application/json"}},
    )
    r.urlopen(req, timeout={timeout}).read()
    # A webhook never says where the message landed, so there is no thread to
    # publish and a reply cannot be wired to this one.
    print(json.dumps({{"thread_ts": "", "posted": "true"}}))
"""


def announce(
    *,
    node_id: str,
    text: str | Template,
    to: EnvVar,
    channel: EnvVar | None = None,
    thread: Ref | None = None,
    answers: GateNode | str | None = None,
    buttons: Sequence[tuple[str, str]] | None = None,
    label: str = "",
    description: str = "",
    inputs: Sequence[InputPort] = (),
    timeout: int = 15,
) -> ScriptNode:
    """Report ``text``, reading the endpoint from the ``to`` environment variable.

    Without ``channel``, ``to`` names a webhook URL and every run's messages land
    in the channel root together. With it, ``to`` names a bot token, ``channel``
    names where to post, and the step publishes ``thread_ts`` so later
    announcements can reply under the same message.

    ``thread`` is that published port, read from the announcement that opened the
    thread. The input it needs is declared for you; the *data edge* is still
    yours to wire with ``feed``, and the lint refuses the graph without it.

    ``label`` goes on the root message beside the run id — pass the pipeline's
    name, so a channel carrying several runs says which is which.

    The step fails if a variable is unset, which is the node's advantage over a
    subscriber: a misconfigured report is a red step rather than silence.
    """
    if not to.name:
        raise CompositionError(f"announce node {node_id!r} needs a variable holding the endpoint")
    if timeout < 1:
        raise CompositionError(f"announce node {node_id!r} needs a timeout of at least 1s")
    if thread is not None and channel is None:
        raise CompositionError(
            f"announce node {node_id!r} replies in a thread but names no channel. "
            "Threading goes through chat.postMessage, which needs a bot token and a "
            "channel; a webhook never reports where its message landed, so there is "
            "no thread to reply under."
        )
    if '"' in label:
        raise CompositionError(f"announce node {node_id!r}: label cannot contain a quote")

    asks = _buttons_for(node_id, answers, buttons, channel)

    declared = list(inputs)
    if thread is not None and not any(port.name == thread.source_id for port in declared):
        # The reply reads the opener's output, so the opener has to be in scope.
        declared.append(InputPort(thread.source_id, PortType.STRING, optional=True))

    return ScriptNode(
        node_id=node_id,
        description=description or f"Report to ${to.name}",
        inputs=tuple(declared),
        command="python3",
        args=(
            "-c",
            _POST.format(
                secret=to.name,
                channel=channel.name if channel else "",
                label=label,
                api=API,
                timeout=timeout,
            ),
            # Through argv rather than baked into the source: the value is a
            # rendered template, and interpolating one into Python text is how a
            # quote in somebody's data becomes a syntax error at run time.
            as_template(thread) if thread is not None else "",
            asks,
        ),
        stdin=text,
        timeout=timeout + 5,
        declared_outputs=(
            OutputPort(THREAD_PORT, PortType.STRING, "The thread this opened, if any"),
            OutputPort("posted", PortType.STRING, "Always 'true'; the step fails otherwise"),
        ),
    )


def _buttons_for(
    node_id: str,
    answers: GateNode | str | None,
    buttons: Sequence[tuple[str, str]] | None,
    channel: EnvVar | None,
) -> str:
    """The button spec, as the JSON the posting script reads from argv.

    Taking the gate itself is the point: its choices are the buttons, so the two
    cannot drift and a renamed option cannot leave a button that answers nothing.
    A gate reached by name needs its ``buttons`` spelled out, which is what the
    start gate requires — it does not exist until the pipeline is loaded.
    """
    if answers is None:
        if buttons:
            raise CompositionError(
                f"announce node {node_id!r} offers buttons but names no gate for them "
                "to answer; pass answers=<gate>"
            )
        return ""
    if channel is None:
        raise CompositionError(
            f"announce node {node_id!r} offers buttons but names no channel. Buttons are "
            "Block Kit, which a webhook cannot carry back — they need a bot token."
        )
    gate = answers.node_id if isinstance(answers, GateNode) else answers
    if buttons is None:
        if not isinstance(answers, GateNode):
            raise CompositionError(
                f"announce node {node_id!r} answers {gate!r} by name, so its buttons "
                "cannot be read off it; pass buttons=((value, label), ...)"
            )
        buttons = [(choice.value, choice.label or choice.value) for choice in answers.choices]
    if not buttons:
        raise CompositionError(f"announce node {node_id!r} offers an empty set of buttons")
    seen = {value for value, _ in buttons}
    if len(seen) != len(buttons):
        raise CompositionError(
            f"announce node {node_id!r} repeats a button value; each one is a distinct answer"
        )
    return json.dumps({"gate": gate, "buttons": [list(pair) for pair in buttons]})
