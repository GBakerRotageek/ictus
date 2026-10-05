"""The watcher: finding a run, reading it as signals, and letting go of it."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ictus import RunSignal
from ictus.interfaces.conductor.events import ENDED, SignalEvent, signals_from
from ictus.interfaces.conductor.runs import LiveRun, live_runs, token_for
from ictus.interfaces.conductor.websocket import HandshakeError, WebSocket

if TYPE_CHECKING:
    from collections.abc import Iterator

FIXTURES = Path(__file__).parent / "fixtures"

RUN = LiveRun(run_id="abc123", workflow="smoke-events", port=1234, pid=9, started_at="2026")


def _recorded(name: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


# --- reading a recorded run --------------------------------------------------


def test_a_whole_run_reads_as_the_signals_that_happened() -> None:
    seen = [e.signal for e in signals_from(_recorded("run-events-approved.jsonl"), RUN)]
    assert seen == [
        RunSignal.RUN_STARTED,
        RunSignal.DECISION_NEEDED,
        RunSignal.DECISION_MADE,
        RunSignal.DECISION_NEEDED,
        RunSignal.DECISION_MADE,
        RunSignal.RUN_FINISHED,
    ]


def test_the_trace_events_are_dropped_rather_than_reported() -> None:
    """17 events in, 6 out. A notifier forwarding the rest would be a firehose."""
    events = _recorded("run-events-approved.jsonl")
    assert len(events) == 17
    assert len(list(signals_from(events, RUN))) == 6


def test_a_signal_carries_the_run_it_happened_in() -> None:
    first = next(iter(signals_from(_recorded("run-events-approved.jsonl"), RUN)))
    assert (first.run_id, first.workflow) == ("abc123", "smoke-events")
    assert first.event_type == "workflow_started"
    assert first.at > 0


def test_a_decision_carries_what_is_being_asked() -> None:
    gate = next(
        e
        for e in signals_from(_recorded("run-events-approved.jsonl"), RUN)
        if e.signal is RunSignal.DECISION_NEEDED
    )
    assert gate.data["agent_name"] == "confirm_start"
    assert gate.data["options"] == ["start", "cancel"]


def test_the_answer_comes_back_with_the_free_text() -> None:
    """What a report needs to say who chose what."""
    answered = [
        e
        for e in signals_from(_recorded("run-events-rejected.jsonl"), RUN)
        if e.signal is RunSignal.DECISION_MADE
    ]
    assert answered[-1].data["selected_option"] == "rejected"
    assert answered[-1].data["additional_input"] == {
        "notes": "not this time — from the spike client"
    }


# --- what it must stop doing -------------------------------------------------


def test_nothing_is_reported_after_the_run_ends() -> None:
    """Holding on past the end is what stops a detached run reaping."""
    events = [*_recorded("run-events-approved.jsonl")]
    events.append({"type": "gate_presented", "timestamp": 9.9, "data": {}})
    seen = list(signals_from(events, RUN))
    assert seen[-1].signal is RunSignal.RUN_FINISHED
    assert seen[-1].ends_the_run
    assert len(seen) == 6


def test_both_endings_end_it() -> None:
    assert {RunSignal.RUN_FINISHED, RunSignal.RUN_FAILED} == ENDED
    failed = [{"type": "workflow_failed", "timestamp": 1.0, "data": {}}]
    assert next(iter(signals_from(failed, RUN))).ends_the_run


def test_the_overlap_between_history_and_the_socket_is_absorbed() -> None:
    """Connect-then-seed duplicates events on purpose; losing one is the bug."""
    events = _recorded("run-events-approved.jsonl")
    doubled = [*events, *events]
    assert len(list(signals_from(doubled, RUN))) == len(list(signals_from(events, RUN)))


def test_an_unknown_event_is_dropped_rather_than_raising() -> None:
    stream: list[dict[str, object]] = [
        {"type": "something_new_in_0_2_0", "timestamp": 1.0, "data": {}},
        {"type": "workflow_completed", "timestamp": 2.0, "data": {}},
    ]
    assert [e.signal for e in signals_from(stream, RUN)] == [RunSignal.RUN_FINISHED]


def test_a_malformed_event_does_not_stop_the_stream() -> None:
    """A truncated or half-written line on a live run is normal."""
    stream: list[dict[str, object]] = [
        {"no_type": True},
        {"type": 42},
        {"type": "workflow_completed", "timestamp": 1.0},
    ]
    assert [e.signal for e in signals_from(stream, RUN)] == [RunSignal.RUN_FINISHED]


# --- discovery ---------------------------------------------------------------


def _record(directory: Path, run_id: str, **fields: object) -> None:
    body: dict[str, object] = {
        "run_id": run_id,
        "workflow_name": "smoke-events",
        "port": 50000,
        "pid": 42,
        "started_at": "2026-10-05T09:34:59+00:00",
    }
    body.update(fields)
    (directory / f"{run_id}.json").write_text(json.dumps(body))


def test_a_run_is_found_from_its_record(tmp_path: Path) -> None:
    _record(tmp_path, "aaa", event_log_path="/tmp/aaa.events.jsonl")
    (found,) = live_runs(runs_dir=tmp_path)
    assert (found.run_id, found.port, found.workflow) == ("aaa", 50000, "smoke-events")
    assert found.event_log == Path("/tmp/aaa.events.jsonl")
    assert found.dashboard == "http://127.0.0.1:50000"
    assert found.socket_url == "ws://127.0.0.1:50000/ws"


def test_a_foreground_run_is_not_watchable(tmp_path: Path) -> None:
    """No port means no socket and no way to answer it from outside."""
    _record(tmp_path, "aaa", port=None)
    assert live_runs(runs_dir=tmp_path) == []


def test_a_record_being_written_is_skipped_not_fatal(tmp_path: Path) -> None:
    """Normal on a busy machine; a watcher that died on one would die often."""
    _record(tmp_path, "good")
    (tmp_path / "half.json").write_text('{"run_id": "half", "po')
    assert [r.run_id for r in live_runs(runs_dir=tmp_path)] == ["good"]


def test_runs_come_back_oldest_first(tmp_path: Path) -> None:
    _record(tmp_path, "later", started_at="2026-10-05T12:00:00+00:00")
    _record(tmp_path, "earlier", started_at="2026-10-05T09:00:00+00:00")
    assert [r.run_id for r in live_runs(runs_dir=tmp_path)] == ["earlier", "later"]


def test_a_reaped_run_is_gone_because_the_engine_moved_it(tmp_path: Path) -> None:
    """The archive lives in terminal/, so globbing finds only live runs."""
    (tmp_path / "terminal").mkdir()
    _record(tmp_path / "terminal", "finished")
    assert live_runs(runs_dir=tmp_path) == []


def test_the_token_is_read_from_the_file_beside_the_record(tmp_path: Path) -> None:
    (tmp_path / "dashboard-50000.token").write_text("s3cret\n")
    assert token_for(50000, runs_dir=tmp_path) == "s3cret"


def test_the_environment_overrides_the_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dashboard-50000.token").write_text("from-file")
    monkeypatch.setenv("CONDUCTOR_GATE_TOKEN", "from-env")
    assert token_for(50000, runs_dir=tmp_path) == "from-env"


def test_a_missing_token_is_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading state needs none, so a listener still works without one."""
    monkeypatch.delenv("CONDUCTOR_GATE_TOKEN", raising=False)
    assert token_for(50000, runs_dir=tmp_path) is None


# --- the websocket client ----------------------------------------------------


@pytest.fixture
def server() -> Iterator[tuple[int, list[str]]]:
    """A socket that completes the handshake, echoes a frame, then closes."""
    received: list[str] = []
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        connection, _ = listener.accept()
        request = b""
        while b"\r\n\r\n" not in request:
            request += connection.recv(4096)
        received.append(request.decode(errors="replace"))
        connection.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        body = b'{"type":"workflow_completed","timestamp":1.0,"data":{}}'
        connection.sendall(bytes([0x81, len(body)]) + body)
        # Brief, and tolerant of nothing arriving: only one test sends a frame,
        # and a blocking recv here would hang the other until its read deadline.
        connection.settimeout(2.0)
        try:
            received.append(repr(connection.recv(4096)))
        except (TimeoutError, OSError):
            received.append("")
        connection.sendall(bytes([0x88, 0x00]))
        connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield port, received
    listener.close()


def test_the_client_handshakes_and_reads_a_frame(server: tuple[int, list[str]]) -> None:
    port, received = server
    with WebSocket("127.0.0.1", port, "/ws", headers={"Authorization": "Bearer t"}) as ws:
        messages = list(ws.messages())
    assert json.loads(messages[0])["type"] == "workflow_completed"
    assert "GET /ws HTTP/1.1" in received[0]
    assert "Sec-WebSocket-Key:" in received[0]
    assert "Authorization: Bearer t" in received[0]


def test_a_refused_handshake_says_the_token_is_the_usual_cause() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def refuse() -> None:
        connection, _ = listener.accept()
        connection.recv(4096)
        connection.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        connection.close()

    threading.Thread(target=refuse, daemon=True).start()
    with pytest.raises(HandshakeError, match="403"):
        WebSocket("127.0.0.1", port, "/ws")
    listener.close()


def test_a_sent_frame_is_masked(server: tuple[int, list[str]]) -> None:
    """A client frame must be masked; an unmasked one is a protocol error."""
    port, received = server
    with WebSocket("127.0.0.1", port, "/ws") as ws:
        ws.send('{"type":"gate_response"}')
        list(ws.messages())
    sent = received[1]
    assert "\\x81" in sent, sent
    assert "gate_response" not in sent, "an unmasked payload would be readable"


def test_an_event_reaches_a_caller_as_a_signal() -> None:
    """The dataclass a notifier will be handed."""
    event = SignalEvent(
        signal=RunSignal.DECISION_NEEDED,
        run_id="abc",
        workflow="w",
        at=1.0,
        event_type="gate_presented",
        data={"agent_name": "approve"},
    )
    assert not event.ends_the_run
    assert event.data["agent_name"] == "approve"
