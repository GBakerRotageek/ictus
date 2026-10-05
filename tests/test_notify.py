"""Integrations: the air gap, what a report says, and what it must never leak."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import pytest

from ictus import EnvVar, Integration, RunSignal
from ictus.errors import CompositionError
from ictus.graph.node import NodeKind
from ictus.interfaces import SignalEvent
from ictus.notify import Delivered, deliver, send, summarise
from ictus.notify.slack import slack_channel, slack_webhook
from ictus.stdlib import announce, approval_gate
from ictus.stdlib.steps.announce import THREAD_PORT

if TYPE_CHECKING:
    from collections.abc import Iterator

SECRET = "/services/T000/B000/sup3rs3cr3t"
SRC = Path(__file__).resolve().parent.parent / "src" / "ictus"


def _event(signal: RunSignal = RunSignal.DECISION_NEEDED, **data: object) -> SignalEvent:
    return SignalEvent(
        signal=signal,
        run_id="bd13f80e",
        workflow="smoke-events",
        at=1791192899.88,
        event_type="gate_presented",
        data=data,
    )


def _hook(**over: object) -> Integration:
    fields: dict[str, object] = {
        "url": EnvVar("HOOK_URL", "where to post"),
        "reports": (RunSignal.DECISION_NEEDED,),
    }
    fields.update(over)
    return slack_webhook(**fields)  # type: ignore[arg-type]


def _channel(**over: object) -> Integration:
    fields: dict[str, object] = {
        "token": EnvVar("TEST_TOKEN", "a bot token"),
        "channel": EnvVar("TEST_CHANNEL", "a channel id"),
    }
    fields.update(over)
    return slack_channel(**fields)  # type: ignore[arg-type]


# --- the air gap -------------------------------------------------------------

VENDOR = ("slack", "thread_ts", "chat.postmessage", "xoxb", "block kit")


@pytest.mark.parametrize("package", ["graph", "stdlib", "lint"])
def test_no_service_is_named_above_the_notify_boundary(package: str) -> None:
    """The rule `interfaces/conductor` has for engines, one axis over.

    A pipeline declares an integration and attaches it; which service that is
    must be answerable in one package. Prose saying "this does not know Slack"
    is allowed — naming the thing being excluded is not coupling to it.
    """
    offending: list[str] = []
    for path in (SRC / package).rglob("*.py"):
        for name, line in _code_of(path):
            if any(word in name.lower() for word in VENDOR):
                offending.append(f"{path.relative_to(SRC)}:{line}: {name}")
    assert not offending, "a service's spelling above notify/:\n" + "\n".join(offending)


def _code_of(path: Path) -> list[tuple[str, int]]:
    """Every token that is code, with its line. Comments and strings dropped.

    Tokenising rather than reading lines: a docstring saying "this knows no
    Slack" is documentation of the boundary, not a breach of it, and no
    line-by-line heuristic tells the two apart reliably.
    """
    import io
    import tokenize

    kept: list[tuple[str, int]] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
                continue
            if token.string.strip():
                kept.append((token.string, token.start[0]))
    return kept


def test_the_graph_layer_carries_a_program_it_never_reads() -> None:
    """How the air gap is possible: the sending program is opaque data."""
    service = _channel()
    assert service.program
    assert service.command == "python3"


# --- declaring one -----------------------------------------------------------


def test_an_integration_with_no_program_could_never_send_anything() -> None:
    with pytest.raises(CompositionError, match="no program"):
        Integration(name="x", purpose="y")


def test_an_integration_needs_a_purpose_like_every_other_requirement() -> None:
    with pytest.raises(CompositionError, match="needs a purpose"):
        _channel(purpose="")


def test_a_repeated_signal_is_refused() -> None:
    with pytest.raises(CompositionError, match="more than once"):
        _channel(reports=(RunSignal.RUN_FAILED, RunSignal.RUN_FAILED))


def test_a_webhook_cannot_thread_and_says_so() -> None:
    """It never learns where its message landed, so there is no parent."""
    assert not _hook().threads
    assert _channel().threads


# --- what a report says ------------------------------------------------------


def test_a_decision_says_what_it_is_waiting_for() -> None:
    text = summarise(
        _event(agent_name="confirm_start", options=["start", "cancel"], prompt="Start it?\n\nx")
    )
    assert "smoke-events" in text
    assert "needs a decision" in text
    assert "confirm_start" in text
    assert "`start`, `cancel`" in text
    assert "Start it?" in text
    assert "bd13f80e" in text


def test_a_long_prompt_is_cut_to_its_opening() -> None:
    assert len(summarise(_event(prompt="x" * 500))) < 400


def test_an_answer_carries_the_choice_and_the_note() -> None:
    text = summarise(
        _event(
            RunSignal.DECISION_MADE,
            selected_option="rejected",
            additional_input={"notes": "not this time"},
        )
    )
    assert "`rejected`" in text
    assert "not this time" in text


def test_the_dashboard_link_is_omitted_rather_than_guessed() -> None:
    assert "http" not in summarise(_event())
    assert "http://127.0.0.1:1" in summarise(_event(), dashboard="http://127.0.0.1:1")


def test_a_summary_names_no_service() -> None:
    """Each program wraps it; the words are the same wherever they land."""
    for signal in RunSignal:
        assert "slack" not in summarise(_event(signal)).lower()


# --- a step that reports -----------------------------------------------------


def test_an_announce_node_is_a_script_step_not_a_model_call() -> None:
    node = announce(node_id="tell", text="hello", to=_channel())
    assert node.kind is NodeKind.SUBPROCESS


def test_no_credential_is_in_what_is_emitted() -> None:
    """Slack's own endpoint is public and fine; a token is the authorisation."""
    rendered = " ".join(str(a) for a in announce(node_id="t", text="x", to=_channel()).args)
    assert "TEST_TOKEN" in rendered
    assert "TEST_CHANNEL" in rendered
    assert "xoxb-" not in rendered
    assert "hooks.slack.com" not in rendered


def test_the_message_is_piped_rather_than_put_on_the_command_line() -> None:
    """A gate prompt is prose; argv would break on the first apostrophe."""
    node = announce(node_id="t", text='it\'s "quoted"', to=_channel())
    assert node.stdin == 'it\'s "quoted"'


def test_the_thread_is_published_as_a_typed_port() -> None:
    ports = [p.name for p in announce(node_id="t", text="x", to=_channel()).outputs]
    assert ports == [THREAD_PORT, "posted"]


def test_replying_under_a_message_needs_a_service_that_threads() -> None:
    opener = announce(node_id="open", text="x", to=_channel())
    with pytest.raises(CompositionError, match="has no threads"):
        announce(node_id="reply", text="y", to=_hook(), thread=opener.ref(THREAD_PORT))


def test_a_reply_declares_the_input_it_needs_without_being_asked() -> None:
    opener = announce(node_id="open", text="x", to=_channel())
    reply = announce(node_id="r", text="y", to=_channel(), thread=opener.ref(THREAD_PORT))
    assert [p.name for p in reply.inputs] == ["open"]


def test_buttons_are_read_off_the_gate_they_answer() -> None:
    gate = approval_gate(node_id="ship_it", prompt="Deploy?")
    node = announce(node_id="ask", text="?", to=_channel(), answers=gate)
    spec = json.loads(str(node.args[3]))
    assert spec["gate"] == "ship_it"
    assert spec["buttons"] == [["approved", "Approve"], ["rejected", "Reject"]]


def test_buttons_need_a_service_that_can_carry_an_answer_back() -> None:
    gate = approval_gate(node_id="ship_it", prompt="Deploy?")
    with pytest.raises(CompositionError, match="cannot carry an answer back"):
        announce(node_id="ask", text="?", to=_hook(), answers=gate)


def test_the_program_is_valid_python() -> None:
    """It is a string in a YAML file, so nothing else would check it."""
    compile(_channel().program, "<integration>", "exec")
    compile(_hook().program, "<integration>", "exec")


# --- a server that records what arrives --------------------------------------


class _Collector(BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, dict[str, object]]]] = []
    status: ClassVar[int] = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        type(self).received.append((self.path, body))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "ts": "1700000000.000100"}).encode())

    def log_message(self, *_: object) -> None:
        """Silence; the assertions are the output."""


@pytest.fixture
def collector() -> Iterator[tuple[str, list[tuple[str, dict[str, object]]]]]:
    _Collector.received = []
    _Collector.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}{SECRET}", _Collector.received
    server.shutdown()


# --- the program actually running --------------------------------------------


def _run(
    service: Integration, text: str, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-c", service.program, *args],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env},
    )


def test_a_webhook_program_posts_what_it_is_given(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    done = _run(_hook(), 'it\'s "quoted"', HOOK_URL=url)
    assert done.returncode == 0, done.stderr
    assert received[0][1] == {"text": 'it\'s "quoted"'}


def test_a_channel_program_threads_and_publishes_the_thread(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    done = _run(
        _channel(),
        "under here",
        "1699999999.000001",
        "",
        TEST_TOKEN="xoxb-pretend",
        TEST_CHANNEL="C0TEST",
        SLACK_API_URL=url,
    )
    assert done.returncode == 0, done.stderr
    assert received[0][1]["thread_ts"] == "1699999999.000001"
    assert json.loads(done.stdout)["thread"] == "1700000000.000100", "named for what it is"


def test_a_button_carries_the_run_the_gate_and_the_choice(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    gate = approval_gate(node_id="ship_it", prompt="Deploy?")
    node = announce(node_id="ask", text="Deploy?", to=_channel(), answers=gate)
    done = _run(
        _channel(),
        "Deploy?",
        "",
        str(node.args[3]),
        TEST_TOKEN="xoxb-pretend",
        TEST_CHANNEL="C0TEST",
        SLACK_API_URL=url,
        CONDUCTOR_RUN_ID="abc12345",
    )
    assert done.returncode == 0, done.stderr
    blocks = received[0][1]["blocks"]
    assert isinstance(blocks, list)
    assert [json.loads(e["value"]) for e in blocks[1]["elements"]] == [
        {"run": "abc12345", "gate": "ship_it", "choice": "approved"},
        {"run": "abc12345", "gate": "ship_it", "choice": "rejected"},
    ]


def test_an_unset_credential_fails_the_step_rather_than_posting_nowhere() -> None:
    done = _run(_hook(), "x", HOOK_URL="")
    assert done.returncode != 0
    assert "HOOK_URL" in done.stderr


# --- the watcher's half ------------------------------------------------------


def test_the_watcher_sends_through_the_same_program(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    """One way to send, so a report cannot drift between the two callers."""
    url, received = collector
    (result,) = deliver(_event(), [_hook()], env={"HOOK_URL": url})
    assert result == Delivered("slack", "decision_needed", sent=True)
    assert "needs a decision" in str(received[0][1]["text"])


def test_only_subscribers_to_that_signal_are_told(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    wants = _hook(name="wants", reports=(RunSignal.DECISION_NEEDED,))
    ignores = _hook(name="ignores", reports=(RunSignal.RUN_FAILED,))
    results = deliver(_event(), [wants, ignores], env={"HOOK_URL": url})
    assert [r.integration for r in results] == ["wants"]
    assert len(received) == 1


def test_a_failure_is_reported_with_the_program_s_own_reason() -> None:
    """It knows what the service said and what to do about it."""
    (result,) = deliver(_event(), [_hook()], env={"HOOK_URL": ""})
    assert not result.sent
    assert "HOOK_URL" in result.detail


def test_one_broken_integration_does_not_stop_the_others(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    broken = _hook(name="broken", url=EnvVar("MISSING_URL", "unset"))
    working = _hook(name="working")
    results = deliver(_event(), [broken, working], env={"HOOK_URL": url, "MISSING_URL": ""})
    assert [r.sent for r in results] == [False, True]
    assert len(received) == 1


def test_a_missing_interpreter_is_reported_not_raised() -> None:
    absent = Integration(
        name="absent",
        purpose="a service whose sender is not installed",
        reports=(RunSignal.DECISION_NEEDED,),
        command="definitely-not-a-command",
        program="pass",
    )
    (result,) = deliver(_event(), [absent], env={})
    assert not result.sent
    assert "not on PATH" in result.detail


def test_send_never_puts_a_credential_in_its_reason() -> None:
    why = send(_hook(), "x", env={"HOOK_URL": "http://127.0.0.1:1" + SECRET})
    assert why
    assert SECRET not in why
