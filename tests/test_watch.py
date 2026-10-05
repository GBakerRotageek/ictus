"""The watcher: finding a run, reading it as signals, and letting go of it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ictus import RunSignal
from ictus.interfaces import ENDED, SignalEvent
from ictus.interfaces.conductor.events import signals_from
from ictus.interfaces.conductor.runs import LiveRun, live_runs, token_for

if TYPE_CHECKING:
    import pytest

FIXTURES = Path(__file__).parent / "fixtures"

RUN = LiveRun(run_id="abc123", workflow="smoke-events", port=1234, pid=9, started_at="2026")


def _a_dead_pid() -> int:
    """A pid nothing is using. Spawn one, wait for it, and take its number —
    which beats picking a constant that might belong to something on the day."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


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
    """17 events in, 6 out. A integration forwarding the rest would be a firehose."""
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
    """A record for a live run.

    The pid is this process's, because `live_runs` checks it: a record whose
    process is gone is not a live run, however recent the file is.
    """
    body: dict[str, object] = {
        "run_id": run_id,
        "workflow_name": "smoke-events",
        "port": 50000,
        "pid": os.getpid(),
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


def test_a_record_whose_process_died_is_not_a_live_run(tmp_path: Path) -> None:
    """The engine archives a record on a graceful exit only. A killed run, a
    crash or a closed laptop leaves one behind, and five accumulated here in an
    afternoon — a watcher would have kept dialling ports that stopped answering.
    """
    _record(tmp_path, "zombie", pid=_a_dead_pid())
    assert live_runs(runs_dir=tmp_path) == []


def test_a_record_with_no_pid_is_believed(tmp_path: Path) -> None:
    """Nothing recorded is nothing to disprove; an older engine wrote no pid."""
    _record(tmp_path, "old", pid=0)
    assert [r.run_id for r in live_runs(runs_dir=tmp_path)] == ["old"]


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


def test_an_event_reaches_a_caller_as_a_signal() -> None:
    """The dataclass a integration will be handed."""
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
