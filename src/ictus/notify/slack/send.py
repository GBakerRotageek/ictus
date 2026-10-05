"""Building a Slack report, and posting one.

The program below is what an announcement step runs. It is a string because the
step is a subprocess: the graph carries it without reading it, the way it
carries an MCP server's command, which is what keeps Slack out of ``graph`` and
``stdlib``.

Three decisions in it are not preferences:

* The credential is read from the environment, never written into a pipeline or
  an emitted workflow.
* The message goes down stdin, not argv. A gate prompt is prose, and the first
  apostrophe would end a shell-interpolated one.
* Threading needs ``chat.postMessage``. A webhook accepts ``thread_ts`` but
  never returns the ``ts`` of what it posted, so there is no parent to reply
  under. Both shapes are here; only one can hold a conversation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from ictus.graph.requirements import Integration

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.graph.requirements import EnvVar
    from ictus.graph.signals import RunSignal

__all__ = ["API", "API_ENV", "reply", "slack_channel", "slack_webhook"]

API = "https://slack.com/api/chat.postMessage"

#: Overrides it — a proxy, an Enterprise Grid host, or a local stand-in so the
#: whole path can be exercised without a workspace.
API_ENV = "SLACK_API_URL"

TIMEOUT_SECONDS = 15


def slack_channel(
    *,
    token: EnvVar,
    channel: EnvVar,
    name: str = "slack",
    purpose: str = "Report what this run is doing, and ask for decisions",
    reports: Sequence[RunSignal] = (),
    setup_hint: str = (
        "Create a Slack app with chat:write, install it, invite it to the channel, "
        "then export the bot token and the channel id"
    ),
) -> Integration:
    """A channel reported into through ``chat.postMessage``.

    Threads, so several runs at once stay legible. Needs a bot token.
    """
    return Integration(
        name=name,
        purpose=purpose,
        env=(token, channel),
        reports=tuple(reports),
        command="python3",
        program=_program(secret=token.name, channel=channel.name),
        threads=True,
        setup_hint=setup_hint,
    )


def slack_webhook(
    *,
    url: EnvVar,
    name: str = "slack",
    purpose: str = "Report what this run is doing",
    reports: Sequence[RunSignal] = (),
    setup_hint: str = "Create an incoming webhook on a Slack app and export its URL",
) -> Integration:
    """A channel posted into through an incoming webhook.

    Simpler to set up and strictly less capable: no threads and no buttons, so
    runs interleave and nothing can be answered from Slack.
    """
    return Integration(
        name=name,
        purpose=purpose,
        env=(url,),
        reports=tuple(reports),
        command="python3",
        program=_program(secret=url.name, channel=""),
        threads=False,
        setup_hint=setup_hint,
    )


def _program(*, secret: str, channel: str) -> str:
    """The sending program, with this integration's variable names baked in."""
    return _PROGRAM.format(
        secret=secret, channel=channel, timeout=TIMEOUT_SECONDS, api=API, api_env=API_ENV
    )


#: Reads the message on stdin, the parent thread and the buttons from argv.
_PROGRAM = """import json, os, sys, urllib.request as r

text = sys.stdin.read()
parent = sys.argv[1].strip() if len(sys.argv) > 1 else ""
asks = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {{}}
secret = os.environ.get({secret!r})
if not secret:
    sys.exit({secret!r} + " is not set, so there was nowhere to report")

channel = os.environ.get({channel!r}) if {channel!r} else ""
if {channel!r} and not channel:
    sys.exit({channel!r} + " is not set, so there was no channel to post in")

if channel:
    body = {{"channel": channel, "text": text}}
    if parent:
        body["thread_ts"] = parent
    if asks:
        # The run id is only knowable here, while the step is running.
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
    endpoint = os.environ.get({api_env!r}) or {api!r}
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
    # Slack calls it ts; the graph calls it a thread.
    print(json.dumps({{"thread": answer.get("ts", ""), "posted": "true"}}))
else:
    req = r.Request(
        secret,
        data=json.dumps({{"text": text}}).encode(),
        headers={{"Content-Type": "application/json"}},
    )
    r.urlopen(req, timeout={timeout}).read()
    # A webhook never says where the message landed.
    print(json.dumps({{"thread": "", "posted": "true"}}))
"""


def reply(
    *,
    token: str,
    channel: str,
    thread_ts: str,
    text: str,
    timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Say ``text`` under ``thread_ts``. Returns "" on success, else why not.

    Not a click's ``response_url``: that posts where the *message* lives, which
    for a button in a thread is the channel root — so the answer landed beside
    every other run's. Never raises; the gate is already answered by now.
    """
    body: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    request = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read())
    except (OSError, json.JSONDecodeError) as exc:
        return f"could not reach Slack: {type(exc).__name__}"
    if isinstance(answer, dict) and answer.get("ok"):
        return ""
    error = str(answer.get("error")) if isinstance(answer, dict) else "unreadable reply"
    return f"Slack refused the reply: {error}"
