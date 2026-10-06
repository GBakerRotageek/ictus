"""Starting a run because somebody asked for one in a channel."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ictus import EnvVar, Integration, Pipeline, PortType, RunSignal, WorkflowInput
from ictus.errors import CompositionError
from ictus.integrate import OPENER_ID, apply_integrations
from ictus.notify.slack import slack_channel
from ictus.notify.slack.listen import events
from ictus.notify.slack.trigger import DEFAULT_PREFIX, Asked, Trigger, asked, start
from ictus.stdlib import approval_gate, succeed

STR = PortType.STRING
TRIGGER = Trigger(folder=Path("demo_work/pipelines/asked"))


def _envelope(text: str, **over: object) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "message",
        "text": text,
        "ts": "1700000000.000100",
        "channel": "C0TEST",
        "user": "U123",
    }
    event.update(over)
    return {
        "envelope_id": "e1",
        "type": "events_api",
        "payload": {"type": "event_callback", "event": event},
    }


# --- recognising the ask -----------------------------------------------------


def test_the_question_is_whatever_followed_the_prefix() -> None:
    (ask,) = asked(_envelope("Start test run: why is the bus failing?"), TRIGGER)
    assert ask.question == "why is the bus failing?"
    assert ask.channel == "C0TEST"
    assert ask.who == "U123"


def test_the_message_s_own_timestamp_is_the_conversation() -> None:
    """Replies hang under the question, which is the whole point."""
    (ask,) = asked(_envelope(f"{DEFAULT_PREFIX} anything"), TRIGGER)
    assert ask.thread == "1700000000.000100"


def test_an_ask_inside_someone_else_s_thread_answers_in_that_thread() -> None:
    """Its own ts, never the parent's: the answer belongs to the question."""
    (ask,) = asked(
        _envelope(f"{DEFAULT_PREFIX} x", ts="1700000009.000999", thread_ts="1700000000.000100"),
        TRIGGER,
    )
    assert ask.thread == "1700000009.000999"


def test_capitalisation_and_spacing_do_not_decide_it() -> None:
    """Somebody typing in a channel is not writing a command line."""
    (ask,) = asked(_envelope("  start TEST run:   spaced  "), TRIGGER)
    assert ask.question == "spaced"


def test_an_ordinary_message_starts_nothing() -> None:
    assert list(asked(_envelope("start test run is a phrase I used"), TRIGGER)) == []
    assert list(asked(_envelope("Start test run:"), TRIGGER)) == []


def test_the_app_s_own_messages_are_ignored() -> None:
    """It reports into the channel it watches; without this it starts itself."""
    assert list(asked(_envelope(f"{DEFAULT_PREFIX} loop", bot_id="B1"), TRIGGER)) == []


def test_edits_joins_and_deletions_are_ignored() -> None:
    """A subtype is the message changing, not somebody asking."""
    for subtype in ("message_changed", "message_deleted", "channel_join", "thread_broadcast"):
        assert list(asked(_envelope(f"{DEFAULT_PREFIX} x", subtype=subtype), TRIGGER)) == []


def test_a_press_is_not_an_ask() -> None:
    envelope: dict[str, object] = {"payload": {"type": "block_actions", "actions": []}}
    assert list(asked(envelope, TRIGGER)) == []


def test_a_custom_prefix_is_honoured() -> None:
    mine = Trigger(folder=Path("x"), prefix="!run")
    (ask,) = asked(_envelope("!run the thing"), mine)
    assert ask.question == "the thing"
    assert list(asked(_envelope(f"{DEFAULT_PREFIX} x"), mine)) == []


def test_the_listener_yields_asks_alongside_presses() -> None:
    """Both arrive down one socket; neither needs its own connection."""
    (ask,) = events(_envelope(f"{DEFAULT_PREFIX} x"), TRIGGER)
    assert isinstance(ask, Asked)
    assert list(events(_envelope(f"{DEFAULT_PREFIX} x"))) == [], "no trigger, no asks"


# --- launching ---------------------------------------------------------------


def _ask() -> Asked:
    return Asked(question="why's it failing?", thread="1.5", channel="C", who="U")


def test_a_missing_ictus_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listener is driven by somebody typing; it must not die of this."""

    def _absent(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", _absent)
    assert "not on PATH" in start(_ask(), TRIGGER)


def test_a_refusal_comes_back_as_its_last_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight has already said which variable is missing; do not bury it."""

    def _refuse(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [], 1, "", "lint said no\nerror: $SLACK_BOT_TOKEN is not set"
        )

    monkeypatch.setattr(subprocess, "run", _refuse)
    assert start(_ask(), TRIGGER) == "error: $SLACK_BOT_TOKEN is not set"


def test_the_question_and_the_thread_are_passed_as_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through argv, so an apostrophe in a question is only an apostrophe."""
    seen: list[list[str]] = []

    def _record(command: list[str], **__: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _record)
    assert start(_ask(), TRIGGER) == ""
    assert "question=why's it failing?" in seen[0]
    assert "reply_to=1.5" in seen[0]


# --- a run that reports into somebody else's conversation --------------------


def _pipeline(*, named: str = "reply_to") -> tuple[Pipeline, WorkflowInput, Integration]:
    p = Pipeline(pipeline_id="asked")
    where = p.declare_input(named, STR, required=False)
    service = slack_channel(
        token=EnvVar("T", "t"),
        channel=EnvVar("C", "c"),
        reports=(RunSignal.DECISION_NEEDED,),
    )
    gate = p.add(approval_gate(node_id="go_ahead", prompt="Go?"))
    done = p.add(succeed(node_id="done", reason="done"))
    p.set_entry(gate)
    p.branch(gate, {"approved": done, "rejected": done})
    return p, where, service


def test_a_given_thread_means_no_opener_is_added() -> None:
    """The conversation was open before the run started."""
    p, where, service = _pipeline()
    p.integrate(service, thread=where)
    apply_integrations(p)
    assert not any(n.node_id == OPENER_ID for n in p.nodes)
    assert any(n.node_id.startswith("report_") for n in p.nodes)


def test_without_one_a_run_opens_its_own() -> None:
    p, _, service = _pipeline()
    p.integrate(service)
    apply_integrations(p)
    assert any(n.node_id == OPENER_ID for n in p.nodes)


def test_the_thread_input_must_be_declared_by_the_pipeline() -> None:
    p, _, service = _pipeline()
    other = Pipeline(pipeline_id="elsewhere")
    stray = other.declare_input("reply_to", STR, required=False)
    with pytest.raises(CompositionError, match="does not declare as an input"):
        p.integrate(service, thread=stray)


def test_a_thread_input_must_be_a_string() -> None:
    p = Pipeline(pipeline_id="asked")
    wrong = p.declare_input("reply_to", PortType.NUMBER, required=False)
    service = slack_channel(token=EnvVar("T", "t"), channel=EnvVar("C", "c"))
    with pytest.raises(CompositionError, match="addressed by a string"):
        p.integrate(service, thread=wrong)


def test_an_input_called_thread_collides_with_what_announcements_publish() -> None:
    """A node's ports share one namespace, so the two cannot both be `thread`."""
    p, _, service = _pipeline(named="thread")
    p.integrate(service, thread=p.workflow_inputs[0])
    with pytest.raises(CompositionError, match="share one namespace"):
        apply_integrations(p)
