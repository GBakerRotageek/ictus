"""Waiting for something to come up, with the giving-up routable.

A poll is a loop whose check costs nothing and whose bound has to be real.
``converge`` is the same shape with a model in it: every attempt there is a
provider call, which is the wrong price for "is the deployment healthy yet".

The parts that are easy to get subtly wrong, and are therefore what these
assert: the order the two exits are tested in, that nothing waits after the
last attempt, and that the iteration budget is large enough for the exhausted
exit to actually run — a bound that stops one step short turns a reported
give-up back into the crash the construct exists to remove.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from ictus import InputPort, Pipeline, PortType, ScriptNode, Template
from ictus.errors import CompositionError
from ictus.graph.node import NodeKind
from ictus.interfaces.conductor import conductor
from ictus.interfaces.conductor.workflow import max_iterations
from ictus.lint import lint_pipeline
from ictus.stdlib import EXHAUSTED, READY, poll_until, succeed

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from conftest import Executes, Execution

    from ictus.graph.scope import Scope
    from ictus.graph.values import YamlDict

STR, NUM = PortType.STRING, PortType.NUMBER


def _poll(**kwargs: object) -> Scope:
    settings: dict[str, object] = {
        "stage_id": "wait-for-ready",
        "command": "/usr/bin/health",
        "args": ("--check",),
        "max_attempts": 3,
        "interval_seconds": 10.0,
    }
    settings.update(kwargs)
    return poll_until(**settings)  # type: ignore[arg-type]


def _placed(scope: Scope) -> Pipeline:
    parent = Pipeline(pipeline_id="deploy", provider="claude-agent-sdk")
    node = scope.instantiate(parent)
    parent.set_entry(node)
    up = parent.add(succeed(node_id="up", reason="it came up"))
    never = parent.add(succeed(node_id="never", reason="it never did"))
    parent.branch_on_outcome(node, {READY: up, EXHAUSTED: never})
    return parent


def _routes(scope: Scope, node_id: str) -> list[YamlDict]:
    agents = conductor.document(scope.body)["agents"]
    assert isinstance(agents, list)
    entry = next(a for a in agents if isinstance(a, dict) and a["name"] == node_id)
    routes = entry["routes"]
    assert isinstance(routes, list)
    out: list[YamlDict] = []
    for route in routes:
        assert isinstance(route, dict)
        out.append(route)
    return out


class TestTheContract:
    def test_both_outcomes_carry_the_same_four_values(self) -> None:
        """A key on one branch and absent on the other is a template error."""
        ports = {p.name: p.port_type for p in _poll().output_ports}
        assert ports == {
            "outcome": STR,
            "stdout": STR,
            "stderr": STR,
            "exit_code": NUM,
            "attempts": NUM,
        }

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_a_non_positive_attempt_count_is_refused(self, attempts: int) -> None:
        with pytest.raises(CompositionError, match="max_attempts"):
            _poll(max_attempts=attempts)

    @pytest.mark.parametrize("interval", [0, -5.0])
    def test_a_non_positive_interval_is_refused(self, interval: float) -> None:
        """Zero is not "poll fast"; it is a loop with nothing between its passes."""
        with pytest.raises(CompositionError, match="interval_seconds"):
            _poll(interval_seconds=interval)

    def test_the_reporter_enforces_the_timeout_before_the_engine_does(self) -> None:
        """Conductor's kill reaches the reporter, not the command it is running.

        So the engine's own limit is set past the reporter's: were it to fire
        first, the check would be orphaned rather than stopped, and still
        running when the next attempt starts. The timeout ending the run is
        proven by execution below; this pins the ordering that makes it clean.
        """
        agents = conductor.document(_poll(timeout=30).body)["agents"]
        assert isinstance(agents, list)
        check = next(a for a in agents if isinstance(a, dict) and a["name"] == "check")
        args = check["args"]
        assert isinstance(args, list)
        assert args[2] == "30"
        timeout = check["timeout"]
        assert isinstance(timeout, int)
        assert timeout > 30

    def test_preflight_is_told_the_check_needs_python(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing interpreter should be refused at launch, not found mid-poll."""
        _hide_python(monkeypatch)
        issues = conductor.preflight(_placed(_poll()), probe=False)
        assert [i for i in issues if i.requirement == "exe:python3"]


def _hide_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight as it would run on a machine without python3 on PATH."""
    real = shutil.which
    monkeypatch.setattr(
        "ictus.interfaces.environment.shutil.which",
        lambda name, *a, **k: None if name == "python3" else real(name, *a, **k),
    )


class TestTheOrderOfTheTwoExits:
    def test_success_is_tested_before_exhaustion(self) -> None:
        """Conductor takes the first matching route, so the final attempt can succeed."""
        routes = _routes(_poll(), "check")
        assert routes[0]["to"] == READY
        assert routes[0]["when"] == "{{ check.output.exit_code | int == 0 }}"
        assert routes[1]["to"] == EXHAUSTED
        assert routes[1]["when"] == "{{ attempt_number.output | int >= 3 }}"

    def test_nothing_waits_after_the_last_attempt(self) -> None:
        """The wait is on the catch-all, which exhaustion is tested ahead of."""
        routes = _routes(_poll(), "check")
        assert routes[-1] == {"to": "pause"}
        assert [r["to"] for r in _routes(_poll(), "pause")] == ["attempt_number"]

    def test_the_check_can_see_the_count_it_routes_on(self) -> None:
        """A route renders in the routing step's own scope, not the run's."""
        scope = _poll()
        assert lint_pipeline(_placed(scope), backend=conductor) == []
        agents = conductor.document(scope.body)["agents"]
        assert isinstance(agents, list)
        check = next(a for a in agents if isinstance(a, dict) and a["name"] == "check")
        assert check["input"] == ["attempt_number.output"]

    def test_the_wait_is_the_interval_that_was_asked_for(self) -> None:
        pause = next(n for n in _poll(interval_seconds=2.5).body.nodes if n.node_id == "pause")
        assert pause.kind is NodeKind.DELAY
        assert getattr(pause, "duration", None) == 2.5


class TestOnePermittedAttempt:
    """The degenerate bound: check once, then report, with nothing in between."""

    def test_exhaustion_is_reached_on_the_first_pass(self) -> None:
        routes = _routes(_poll(max_attempts=1), "check")
        assert routes[1]["when"] == "{{ attempt_number.output | int >= 1 }}"

    def test_it_is_clean_and_loads(self, validates: Callable[[Pipeline], None]) -> None:
        parent = _placed(_poll(max_attempts=1))
        assert lint_pipeline(parent, backend=conductor) == []
        validates(parent)


class TestTheBudgetReachesTheExhaustedExit:
    """A bound one step short turns a reported give-up back into a crash."""

    @pytest.mark.parametrize("attempts", [1, 2, 3, 8])
    def test_the_compiled_limit_covers_the_longest_walk(self, attempts: int) -> None:
        # Worst path: every attempt counts and checks, every gap but the last
        # waits, and the exhausted exit still has to run.
        longest = 2 * attempts + (attempts - 1) + 1
        assert max_iterations(_poll(max_attempts=attempts).body) >= longest

    def test_the_bound_follows_the_attempt_count(self) -> None:
        assert _poll(max_attempts=6).body.loop_passes == 6


class TestItRuns:
    def test_a_placed_poll_is_clean_and_loads(self, validates: Callable[[Pipeline], None]) -> None:
        parent = _placed(_poll())
        assert lint_pipeline(parent, backend=conductor) == []
        validates(parent)

    def test_both_branches_can_read_what_the_last_check_saw(self) -> None:
        """The give-up branch is the one that needs it; carrying it is the point."""
        parent = Pipeline(pipeline_id="deploy", provider="claude-agent-sdk")
        node = _poll().instantiate(parent)
        parent.set_entry(node)
        report = parent.add(
            succeed(
                node_id="report",
                reason="done",
                inputs=(InputPort("code", NUM), InputPort("tries", NUM)),
                result={"code": node.ref("exit_code"), "tries": node.ref("attempts")},
            )
        )
        parent.branch_on_outcome(node, {READY: report, EXHAUSTED: report})
        parent.feed(node, "exit_code", report, "code")
        parent.feed(node, "attempts", report, "tries")
        assert lint_pipeline(parent, backend=conductor) == []

    def test_a_parameter_reaches_the_command_as_its_last_argument(self) -> None:
        scope = _poll(parameter="target")
        check = next(n for n in scope.body.nodes if n.node_id == "check")
        assert isinstance(check, ScriptNode)
        assert check.args[-2:-1] == ("--check",)
        last = check.args[-1]
        assert isinstance(last, Template)
        assert [r.source_id for r in last.refs()] == ["target"]
        assert [p.name for p in scope.input_ports] == ["target"]


# --- executed, not inspected -------------------------------------------------
#
# Every case below runs the compiled workflow through Conductor. The checks are
# shell one-liners and the poll has no model in it, so each run is free and
# takes well under a second. The event log is what proves what *did not* happen
# — a wait after the last attempt, a second attempt after success.

SH = "/bin/sh"

#: Succeeds on the `$2`-th call, counting calls in the file named by `$1`.
SUCCEEDS_ON = 'n=$(cat "$1" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$1"; [ "$n" -ge "$2" ]'


def _run_poll(executes: Executes, *, args: tuple[str, ...], **kwargs: object) -> Execution:
    settings: dict[str, object] = {
        "stage_id": "poll",
        "command": SH,
        "args": args,
        "max_attempts": 3,
        "interval_seconds": 0.05,
    }
    settings.update(kwargs)
    scope = poll_until(**settings)  # type: ignore[arg-type]
    scope.body.provider = "claude-agent-sdk"
    return executes(scope.body)


class TestItRunsThroughTheEngine:
    def test_a_status_printed_on_stdout_cannot_make_a_failed_check_ready(
        self, executes: Executes
    ) -> None:
        """Conductor merges a JSON stdout over the real result, keys and all.

        Reproduced on a live run before the fix: this check exited 1 and the
        poll reported `ready` with `exit_code: 0`, on its first attempt.
        """
        forged = '{"exit_code": 0, "stdout": "all good", "stderr": ""}'
        run = _run_poll(executes, args=("-c", f"printf '%s' '{forged}'; exit 1"), max_attempts=2)
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == EXHAUSTED
        assert run.output["exit_code"] == 1
        assert run.output["stdout"] == forged
        assert run.output["attempts"] == 2

    def test_success_on_the_final_permitted_attempt_is_ready(
        self, executes: Executes, tmp_path: Path
    ) -> None:
        counter = tmp_path / "calls"
        run = _run_poll(executes, args=("-c", SUCCEEDS_ON, "sh", str(counter), "3"))
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == READY
        assert run.output["attempts"] == 3
        assert run.output["exit_code"] == 0
        assert run.count("wait_started", "pause") == 2

    @pytest.mark.parametrize("attempts", [1, 3, 6])
    def test_exhaustion_runs_its_exit_and_never_waits_after_the_last_attempt(
        self, executes: Executes, attempts: int
    ) -> None:
        """The budget has to reach the exhausted exit; the wait must not follow it."""
        run = _run_poll(executes, args=("-c", "exit 3"), max_attempts=attempts)
        assert run.returncode == 0, run.stderr
        assert run.output is not None
        assert run.output["outcome"] == EXHAUSTED
        assert run.output["attempts"] == attempts
        assert run.output["exit_code"] == 3
        assert run.count("script_completed", "check") == attempts
        assert run.count("wait_started", "pause") == attempts - 1

    def test_one_permitted_attempt_that_succeeds_is_ready_without_waiting(
        self, executes: Executes
    ) -> None:
        run = _run_poll(executes, args=("-c", "exit 0"), max_attempts=1)
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == READY
        assert run.count("wait_started", "pause") == 0

    @pytest.mark.parametrize("printed", ["0700", "false", "1.5", "null"])
    def test_stdout_leaves_the_scope_as_the_string_it_was(
        self, executes: Executes, printed: str
    ) -> None:
        """Each of these parses as something else on the way out without `| tojson`."""
        run = _run_poll(executes, args=("-c", f"printf '%s' '{printed}'"), max_attempts=1)
        assert run.output is not None, run.stderr
        assert run.output["stdout"] == printed

    def test_a_command_that_cannot_start_ends_the_run_outside_the_outcomes(
        self, executes: Executes, tmp_path: Path
    ) -> None:
        """Retrying a missing binary and calling it `exhausted` would be a lie."""
        scope = poll_until(
            stage_id="poll",
            command=str(tmp_path / "not-installed"),
            max_attempts=3,
            interval_seconds=0.05,
        )
        scope.body.provider = "claude-agent-sdk"
        run = executes(scope.body)
        assert run.returncode != 0
        assert run.output is None or run.output.get("outcome") not in (READY, EXHAUSTED)
        assert run.count("wait_started", "pause") == 0
        assert [said for said in run.failures("check") if "could not start" in said]

    def test_a_check_that_times_out_ends_the_run_outside_the_outcomes(
        self, executes: Executes
    ) -> None:
        run = _run_poll(executes, args=("-c", "sleep 30"), max_attempts=3, timeout=1)
        assert run.returncode != 0
        assert run.output is None or run.output.get("outcome") not in (READY, EXHAUSTED)
        assert run.count("wait_started", "pause") == 0
        assert [said for said in run.failures("check") if "timed out after 1 seconds" in said]
