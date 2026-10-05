"""Button presses: decoding them, and what happens when the run has moved on."""

from __future__ import annotations

import json

import pytest

from ictus.interfaces.conductor.respond import Answered, answer_gate
from ictus.interfaces.conductor.runs import LiveRun
from ictus.interfaces.conductor.websocket import HandshakeError
from ictus.interfaces.conductor.websocket import connect as ws_connect
from ictus.notify.slack_socket import Click, _pressed, resolve

RUN = LiveRun(run_id="abc12345", workflow="w", port=59999, pid=1, started_at="2026")


def _envelope(**action: object) -> dict[str, object]:
    return {
        "envelope_id": "env-1",
        "type": "interactive",
        "payload": {
            "type": "block_actions",
            "user": {"id": "U123"},
            "channel": {"id": "C0TEST"},
            "message": {"ts": "1700000000.000100"},
            "actions": [{"type": "button", "value": json.dumps(action)} if action else {}],
        },
    }


# --- decoding a press --------------------------------------------------------


def test_a_press_carries_the_run_the_gate_and_the_choice() -> None:
    (click,) = _pressed(_envelope(run="abc12345", gate="ship_it", choice="approved"))
    assert (click.run_id, click.gate, click.choice) == ("abc12345", "ship_it", "approved")
    assert click.who == "U123"
    assert click.channel == "C0TEST"
    assert click.thread_ts == "1700000000.000100", "a reply must go under the question"


def test_a_button_that_is_not_ours_is_ignored() -> None:
    """Another app's buttons arrive too if it shares the channel."""
    envelope = _envelope()
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["actions"] = [{"type": "button", "value": "not json at all"}]
    assert list(_pressed(envelope)) == []


def test_a_button_of_ours_missing_a_field_is_ignored() -> None:
    assert list(_pressed(_envelope(run="abc", gate="g"))) == []


def test_anything_that_is_not_a_block_action_is_ignored() -> None:
    """Slash commands and shortcuts come down the same socket."""
    assert list(_pressed({"payload": {"type": "shortcut"}})) == []
    assert list(_pressed({"type": "hello"})) == []


# --- what happens to it ------------------------------------------------------


def _click(**over: object) -> Click:
    fields: dict[str, object] = {
        "run_id": "abc12345",
        "gate": "ship_it",
        "choice": "approved",
        "who": "U123",
    }
    fields.update(over)
    return Click(**fields)  # type: ignore[arg-type]


def test_a_press_for_a_run_that_has_finished_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commonest failure: the thread outlives the run by a long way."""
    monkeypatch.setattr("ictus.notify.slack_socket.live_runs", list)
    answer = resolve(_click())
    assert "no longer running" in answer
    assert "abc12345" in answer


def test_a_press_from_somebody_not_allowed_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _never() -> list[LiveRun]:
        nonlocal called
        called = True
        return [RUN]

    monkeypatch.setattr("ictus.notify.slack_socket.live_runs", _never)
    answer = resolve(_click(), allowed=frozenset({"UOTHER"}))
    assert "not allowed" in answer
    assert not called, "the run must not even be looked up for somebody who may not answer"


def test_an_allowed_press_is_carried_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ictus.notify.slack_socket.live_runs", lambda: [RUN])
    monkeypatch.setattr(
        "ictus.notify.slack_socket.answer_gate",
        lambda *a, **k: Answered(True),  # noqa: ARG005
    )
    assert "answered *approved*" in resolve(_click(), allowed=frozenset({"U123"}))


def test_a_refused_answer_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A button that silently does nothing is worse than no button."""
    monkeypatch.setattr("ictus.notify.slack_socket.live_runs", lambda: [RUN])
    monkeypatch.setattr(
        "ictus.notify.slack_socket.answer_gate",
        lambda *a, **k: Answered(False, "'ship_it' has already been answered"),  # noqa: ARG005
    )
    assert "already been answered" in resolve(_click())


# --- answering the run -------------------------------------------------------


def test_answering_a_run_that_is_not_there_is_reported() -> None:
    gone = LiveRun(run_id="x", workflow="w", port=1, pid=1, started_at="2026")
    outcome = answer_gate(gone, gate="g", choice="c", token="t")
    assert not outcome.accepted
    assert "no longer listening" in outcome.detail


def test_answering_without_a_token_refuses_before_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONDUCTOR_GATE_TOKEN", raising=False)
    monkeypatch.setattr("ictus.interfaces.conductor.respond.token_for", lambda *a, **k: None)  # noqa: ARG005
    outcome = answer_gate(RUN, gate="g", choice="c")
    assert not outcome.accepted
    assert "token" in outcome.detail


# --- the websocket url ------------------------------------------------------


def test_a_non_websocket_url_is_refused() -> None:
    with pytest.raises(HandshakeError, match="not a websocket scheme"):
        ws_connect("https://slack.com/api/apps.connections.open")


def test_the_query_string_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slack puts credentials in it, so dropping it refuses the handshake."""
    seen: dict[str, object] = {}

    class _Fake:
        def __init__(self, host: str, port: int, path: str, **kwargs: object) -> None:
            seen.update({"host": host, "port": port, "path": path, **kwargs})

    monkeypatch.setattr("ictus.interfaces.conductor.websocket.WebSocket", _Fake)
    ws_connect("wss://wss-primary.slack.com/link/?ticket=abc&app_id=A1")
    assert seen["host"] == "wss-primary.slack.com"
    assert seen["port"] == 443
    assert seen["tls"] is True
    assert seen["path"] == "/link/?ticket=abc&app_id=A1"


def test_a_button_already_inside_a_thread_replies_in_that_thread() -> None:
    """Its own ts is the reply's, not the thread's; the parent is thread_ts."""
    envelope = _envelope(run="r", gate="g", choice="c")
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["message"] = {"ts": "1700000009.000999", "thread_ts": "1700000000.000100"}
    (click,) = _pressed(envelope)
    assert click.thread_ts == "1700000000.000100"


def test_a_button_on_a_thread_root_opens_that_thread() -> None:
    """The root has no thread_ts of its own, so its ts is what replies hang off."""
    (click,) = _pressed(_envelope(run="r", gate="g", choice="c"))
    assert click.thread_ts == "1700000000.000100"
