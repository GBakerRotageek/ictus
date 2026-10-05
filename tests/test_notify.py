"""Delivery: what a report says, where it goes, and what it must never leak."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, ClassVar

import pytest

from ictus import EnvVar, Notifier, NotifierKind, RunSignal
from ictus.errors import CompositionError
from ictus.graph.node import NodeKind
from ictus.interfaces import SignalEvent
from ictus.notify import Delivered, DeliveryError, body_for, deliver, endpoint_for
from ictus.notify.slack import HEADLINE, message
from ictus.notify.webhook import post
from ictus.stdlib import announce

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SECRET = "/services/T000/B000/sup3rs3cr3t"


def _event(signal: RunSignal = RunSignal.DECISION_NEEDED, **data: object) -> SignalEvent:
    return SignalEvent(
        signal=signal,
        run_id="bd13f80e",
        workflow="smoke-events",
        at=1791192899.88,
        event_type="gate_presented",
        data=data,
    )


def _notifier(**kwargs: object) -> Notifier:
    fields: dict[str, object] = {
        "name": "slack",
        "purpose": "Tell the team",
        "signals": (RunSignal.DECISION_NEEDED,),
        "env": (EnvVar("HOOK_URL", "where to post"),),
    }
    fields.update(kwargs)
    return Notifier(**fields)  # type: ignore[arg-type]


# --- a server that records what arrives --------------------------------------


class _Collector(BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, dict[str, object]]]] = []
    status: ClassVar[int] = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length)) if length else {}
        type(self).received.append((self.path, body))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_: object) -> None:
        """Silence; the test output is the assertion."""


@pytest.fixture
def collector() -> Iterator[tuple[str, list[tuple[str, dict[str, object]]]]]:
    _Collector.received = []
    _Collector.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}{SECRET}", _Collector.received
    server.shutdown()


# --- posting -----------------------------------------------------------------


def test_a_body_arrives_as_json(collector: tuple[str, list[tuple[str, dict[str, object]]]]) -> None:
    url, received = collector
    post(url, {"text": "hello"}, name="slack")
    path, body = received[0]
    assert path == SECRET
    assert body == {"text": "hello"}


def test_a_refusal_names_the_notifier_and_the_status_only(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    """The URL is the whole credential; a failure must not print it."""
    url, _ = collector
    _Collector.status = 404
    with pytest.raises(DeliveryError) as raised:
        post(url, {"text": "x"}, name="slack")
    assert "404" in str(raised.value)
    assert "slack" in str(raised.value)
    assert SECRET not in str(raised.value)
    assert "127.0.0.1" not in str(raised.value)


def test_an_unreachable_endpoint_does_not_leak_it_either() -> None:
    with pytest.raises(DeliveryError) as raised:
        post("http://127.0.0.1:1/nope" + SECRET, {"a": 1}, name="slack", timeout=2.0)
    assert SECRET not in str(raised.value)
    assert "slack" in str(raised.value)


def test_a_non_http_endpoint_is_refused_before_anything_is_sent() -> None:
    with pytest.raises(DeliveryError, match="not an http"):
        post("file:///etc/passwd", {"a": 1}, name="slack")


# --- choosing where to post --------------------------------------------------


def test_the_endpoint_is_read_from_the_environment_not_the_pipeline() -> None:
    target = _notifier()
    assert endpoint_for(target, {"HOOK_URL": "https://example.invalid/x"}) == (
        "https://example.invalid/x"
    )


def test_an_unset_variable_gives_no_endpoint_rather_than_raising() -> None:
    """Preflight is where this is refused; a watcher must not die of it."""
    assert endpoint_for(_notifier(), {}) is None


def test_the_first_declared_variable_holding_a_value_wins() -> None:
    target = _notifier(env=(EnvVar("FIRST", "a"), EnvVar("SECOND", "b")))
    assert endpoint_for(target, {"SECOND": "https://second.invalid"}) == "https://second.invalid"


# --- what the message says ---------------------------------------------------


def test_a_gate_says_what_it_is_waiting_for() -> None:
    body = message(
        _event(agent_name="confirm_start", options=["start", "cancel"], prompt="Start spike?\n\nx")
    )
    text = str(body["text"])
    assert "smoke-events" in text
    assert "needs a decision" in text
    assert "confirm_start" in text
    assert "`start`, `cancel`" in text
    assert "Start spike?" in text
    assert "bd13f80e" in text


def test_a_long_prompt_is_cut_to_its_opening() -> None:
    """A channel is not a document."""
    body = message(_event(prompt="x" * 500))
    assert len(str(body["text"])) < 400


def test_an_answer_carries_the_choice_and_the_note() -> None:
    body = message(
        _event(
            RunSignal.DECISION_MADE,
            agent_name="smoke_gate",
            selected_option="rejected",
            additional_input={"notes": "not this time"},
        )
    )
    text = str(body["text"])
    assert "`rejected`" in text
    assert "not this time" in text


def test_the_dashboard_link_is_omitted_rather_than_guessed() -> None:
    """A link to a port since reused is worse than no link."""
    assert "http" not in str(message(_event())["text"])
    linked = message(_event(), dashboard="http://127.0.0.1:50984")
    assert "http://127.0.0.1:50984" in str(linked["text"])


def test_every_signal_is_phrased_for_a_person() -> None:
    """A raw enum value arriving in a channel is the implementation leaking."""
    for signal in RunSignal:
        text = str(message(_event(signal))["text"])
        assert "smoke-events" in text, signal
        assert HEADLINE[signal] in text, signal
        assert signal.value not in text, signal


def test_a_webhook_gets_the_raw_signal_not_slack_markup() -> None:
    body = body_for(_notifier(kind=NotifierKind.WEBHOOK), _event(agent_name="g"))
    assert body["signal"] == "decision_needed"
    assert body["run_id"] == "bd13f80e"
    assert body["event_type"] == "gate_presented"
    assert body["data"] == {"agent_name": "g"}
    assert "text" not in body


def test_slack_gets_a_message_rather_than_the_raw_signal() -> None:
    body = body_for(_notifier(kind=NotifierKind.SLACK), _event())
    assert set(body) == {"text"}


# --- dispatch ----------------------------------------------------------------


def test_only_subscribers_to_that_signal_are_told(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    wants = _notifier(name="wants", signals=(RunSignal.DECISION_NEEDED,))
    ignores = _notifier(name="ignores", signals=(RunSignal.RUN_FAILED,))
    results = deliver(_event(), [wants, ignores], env={"HOOK_URL": url})
    assert results == [Delivered("wants", "decision_needed", sent=True)]
    assert len(received) == 1


def test_an_unreachable_notifier_is_reported_not_raised() -> None:
    """A channel being down says nothing about whether the run succeeded."""
    target = _notifier(name="down")
    results = deliver(_event(), [target], env={"HOOK_URL": "http://127.0.0.1:1/x"})
    assert not results[0].sent
    assert "down" in results[0].detail


def test_one_broken_notifier_does_not_stop_the_others(
    collector: tuple[str, list[tuple[str, dict[str, object]]]],
) -> None:
    url, received = collector
    broken = _notifier(name="broken", env=(EnvVar("MISSING_URL", "unset"),))
    working = _notifier(name="working")
    results = deliver(_event(), [broken, working], env={"HOOK_URL": url})
    assert [r.sent for r in results] == [False, True]
    assert len(received) == 1


def test_an_unconfigured_notifier_says_which_variable_is_unset() -> None:
    target = _notifier(name="slack", env=(EnvVar("SLACK_WEBHOOK_URL", "where"),))
    (result,) = deliver(_event(), [target], env={})
    assert not result.sent
    assert "SLACK_WEBHOOK_URL" in result.detail


def test_nothing_delivered_is_an_empty_result_not_an_error() -> None:
    assert deliver(_event(), [], env={}) == []


# --- announcing from inside the graph ----------------------------------------


def test_an_announce_node_is_a_script_step_not_a_model_call() -> None:
    node = announce(node_id="tell", text="hello", to=EnvVar("HOOK", "where"))
    assert node.kind is NodeKind.SUBPROCESS
    assert node.command == "python3"


def test_the_url_is_never_in_what_is_emitted() -> None:
    """The step names the variable and reads it in the subprocess."""
    node = announce(node_id="tell", text="hello", to=EnvVar("HOOK", "where"))
    rendered = " ".join(str(a) for a in node.args)
    assert "HOOK" in rendered
    assert "hooks.slack.com" not in rendered
    assert "https://" not in rendered


def test_the_message_is_piped_rather_than_put_on_the_command_line() -> None:
    """A gate prompt is prose; interpolating it into argv breaks on an apostrophe."""
    node = announce(node_id="tell", text='it\'s a prompt "with" quotes', to=EnvVar("HOOK", "w"))
    assert node.stdin == 'it\'s a prompt "with" quotes'
    assert "apostrophe" not in " ".join(str(a) for a in node.args)


def test_stdout_is_not_a_contract() -> None:
    """Enforcing it would make the engine parse 'reported' as JSON and raise
    after the message had already gone."""
    assert not announce(node_id="t", text="x", to=EnvVar("H", "w")).enforce_outputs


def test_an_announce_step_refuses_a_nameless_variable() -> None:
    with pytest.raises(CompositionError, match="needs a variable"):
        announce(node_id="t", text="x", to=EnvVar("", "w"))


def test_the_posting_script_is_valid_python() -> None:
    """It is built as a string, so nothing else checks it."""
    node = announce(node_id="t", text="x", to=EnvVar("HOOK", "w"))
    compile(str(node.args[1]), "<announce>", "exec")


def test_an_unset_variable_fails_the_step_rather_than_posting_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A red step in the dashboard beats a message nobody notices never arrived."""
    node = announce(node_id="t", text="x", to=EnvVar("DEFINITELY_UNSET_HOOK", "w"))
    monkeypatch.delenv("DEFINITELY_UNSET_HOOK", raising=False)
    done = subprocess.run(
        [sys.executable, "-c", str(node.args[1])],
        input="hello",
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode != 0
    assert "DEFINITELY_UNSET_HOOK" in done.stderr


def test_the_posting_script_actually_posts(
    collector: tuple[str, list[tuple[str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The script is a string in a YAML file; only running it proves it works."""
    url, received = collector
    node = announce(node_id="t", text="x", to=EnvVar("TEST_HOOK_URL", "w"))
    monkeypatch.setenv("TEST_HOOK_URL", url)
    done = subprocess.run(
        [sys.executable, "-c", str(node.args[1])],
        input='it\'s a gate, "really"',
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert received[0][1] == {"text": 'it\'s a gate, "really"'}
