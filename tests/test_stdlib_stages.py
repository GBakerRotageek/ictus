"""The stage stdlib.

Each stage is a whole sub-graph, so the thing worth asserting is that its body
is a valid workflow on its own and that its contract is the shape a parent can
wire to. Both stages and their bodies go through the real Conductor validator.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from ictus import AgentNode, OutputPort, Pipeline, PortType, equals
from ictus.errors import CompositionError
from ictus.graph.node import GateNode, NodeKind
from ictus.interfaces.conductor import ConductorBackend
from ictus.lint import lint_pipeline
from ictus.stdlib import (
    CONVERGED,
    DONE,
    EXHAUSTED,
    FAILED,
    OK,
    UNCLEAR,
    Attempt,
    Choice,
    ReviewOption,
    ScriptStep,
    Tier,
    briefing_gate,
    classify,
    converge,
    script_sequence,
    succeed,
    tiered,
    try_shell,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from conftest import Executes, Execution

    from ictus.graph.scope import Scope
    from ictus.graph.stage import Stage
    from ictus.graph.values import YamlDict

STR, OBJ, NUM = PortType.STRING, PortType.OBJECT, PortType.NUMBER


def _cases() -> dict[str, Stage]:
    return {
        "briefing_gate": briefing_gate(
            stage_id="briefing-gate", subject="change set", question="Ship it?"
        ),
        "script_sequence": script_sequence(
            stage_id="script-seq",
            steps=(
                ScriptStep("drop", "../scripts/db.sh", ("drop",), "dropped"),
                ScriptStep("seed", "../scripts/db.sh", ("seed",), "status"),
            ),
        ),
    }


@pytest.mark.parametrize("name", sorted(_cases()))
def test_stage_body_is_lint_clean(name: str) -> None:
    assert lint_pipeline(_cases()[name].body) == []


@pytest.mark.parametrize("name", sorted(_cases()))
def test_stage_body_loads_in_conductor(name: str, validates: Callable[[Pipeline], None]) -> None:
    validates(_cases()[name].body)


@pytest.mark.parametrize("name", sorted(_cases()))
def test_stage_declares_a_wireable_contract(name: str) -> None:
    """A stage with no ports cannot be connected to anything."""
    stage = _cases()[name]
    assert stage.input_ports, f"{name} exposes no inputs"
    assert stage.output_ports, f"{name} exposes no outputs"


DRAFT = Attempt(
    node_id="draft",
    prompt="Write it.",
    produces=(OutputPort("text", STR, "The draft"),),
)


SELF_CHECK = Attempt(
    node_id="check",
    prompt="Is it ready?",
    produces=(OutputPort("ok", PortType.BOOLEAN, "Ready"), OutputPort("status", STR, "Seen")),
)

FOUND = Attempt(
    node_id="search",
    prompt="Find the repositories.",
    produces=(
        OutputPort("repos", PortType.ARRAY, "One per repo", element={"name": STR, "url": STR}),
    ),
)


def _converge(**kwargs: object) -> Scope:
    settings: dict[str, object] = {
        "stage_id": "loop",
        "attempt": DRAFT,
        "judge": "model",
        "judge_prompt": "Good enough?",
        "passes": 3,
    }
    settings.update(kwargs)
    return converge(**settings)  # type: ignore[arg-type]


class TestConverge:
    def test_the_judge_reference_is_guarded_by_the_compiler(self) -> None:
        """The author writes a reference; the guard is the compiler's job.

        The first pass renders before the judge has run, and Conductor's strict
        undefined kills the step. Nothing in the stage source says "is defined".
        """
        scope = _converge()
        drafted = next(n for n in scope.body.nodes if n.node_id == "draft")
        assert isinstance(drafted, AgentNode)
        assert not isinstance(drafted.prompt, str), "the prompt should carry typed refs"

        rendered = ConductorBackend().document(scope.body)
        agents = rendered["agents"]
        assert isinstance(agents, list)
        emitted = next(a for a in agents if isinstance(a, dict) and a["name"] == "draft")
        prompt = emitted["prompt"]
        assert isinstance(prompt, str)
        assert "{% if judge is defined %}" in prompt

    def test_the_loop_bound_follows_the_pass_count(self) -> None:
        assert _converge(passes=2).body.loop_passes == 2
        _converge(passes=6).body.require_loop_bound()

    def test_rejection_notes_are_fed_back(self) -> None:
        deps = [(d.source.node_id, d.target.node_id) for d in _converge().body.data_deps]
        assert ("judge", "draft") in deps, "a revise loop without feedback is a retry loop"

    def test_the_retry_edge_re_enters_above_the_counter(self) -> None:
        """A retry that rejoins below the counter leaves it stuck on pass one.

        The exhausted exit is then unreachable and the loop dies on Conductor's
        iteration budget instead — which is the failure the construct exists to
        remove. Caught on a live run, not by the validator.
        """
        scope = _converge()
        judge = next(n for n in scope.body.nodes if n.node_id == "judge")
        fallthrough = [e for e in scope.body.outgoing(judge) if e.when is None]
        assert [e.describe_target for e in fallthrough] == ["pass_number"]

    def test_giving_up_is_an_outcome_rather_than_a_crash(self) -> None:
        scope = _converge()
        assert scope.outcomes == (CONVERGED, EXHAUSTED)
        exits = ConductorBackend().document(scope.body)["agents"]
        assert isinstance(exits, list)
        terminals = [a for a in exits if isinstance(a, dict) and a["type"] == "terminate"]
        assert {t["status"] for t in terminals} == {"success"}

    def test_the_exhausted_exit_carries_the_work_and_the_reason(self) -> None:
        """Salvage beats a bare failure: the caller gets the near-miss and the notes."""
        rendered = ConductorBackend().document(_converge().body)
        agents = rendered["agents"]
        assert isinstance(agents, list)
        gave_up = next(a for a in agents if isinstance(a, dict) and a["name"] == "exhausted")
        template = gave_up["output_template"]
        assert isinstance(template, dict)
        assert template["text"] == "{{ draft.output.text | tojson }}"
        assert template["feedback"] == "{{ judge.output.notes | tojson }}"
        assert template["passes"] == "{{ pass_number.output }}"

    def test_the_counter_reads_its_own_previous_value(self) -> None:
        """Verified live: the root guard is required and `.output` has no sub-key."""
        rendered = ConductorBackend().document(_converge().body)
        agents = rendered["agents"]
        assert isinstance(agents, list)
        counter = next(a for a in agents if isinstance(a, dict) and a["name"] == "pass_number")
        assert counter["value"] == (
            "{% if pass_number is defined %}"
            "{{ (pass_number.output | int) + 1 }}"
            "{% else %}1{% endif %}"
        )
        assert counter["input"] == ["pass_number.output?"]
        assert "output" not in counter, "a single-value set step must declare no output schema"

    def test_a_polling_shape_needs_no_judge(self) -> None:
        """One step that checks and reports — the poll_until shape."""
        scope = _converge(
            attempt=Attempt(
                node_id="check",
                prompt="Is it healthy?",
                produces=(
                    OutputPort("ready", PortType.BOOLEAN, "Healthy"),
                    OutputPort("status", STR, "What was seen"),
                ),
            ),
            judge="self",
            judge_prompt="",
            verdict_port="ready",
            pause_between=30.0,
        )
        pause = next(n for n in scope.body.nodes if n.node_id == "pause")
        assert pause.kind is NodeKind.DELAY
        assert "judge" not in {n.node_id for n in scope.body.nodes}

    def test_the_exit_condition_is_emitted_before_the_catch_all(self) -> None:
        """Conductor takes the first matching route; a catch-all first swallows the exit."""
        scope = _converge()
        routes = scope.body.outgoing(next(n for n in scope.body.nodes if n.node_id == "judge"))
        assert routes[0].when is not None
        assert routes[-1].when is None

    def test_a_self_judged_attempt_must_declare_the_verdict(self) -> None:
        with pytest.raises(CompositionError, match="declares no 'ready' output"):
            _converge(judge="self", judge_prompt="", verdict_port="ready")

    def test_a_judged_loop_needs_something_to_judge_by(self) -> None:
        with pytest.raises(CompositionError, match="needs a judge_prompt"):
            _converge(judge_prompt="")

    def test_a_human_judged_loop_still_gives_up(self) -> None:
        """A gate cannot test a counter, so the count is checked one step later."""
        scope = _converge(judge="human")
        rejected = next(n for n in scope.body.nodes if n.node_id == "rejected")
        targets = [e.describe_target for e in scope.body.outgoing(rejected)]
        assert targets == ["exhausted", "pass_number"]

    @staticmethod
    def _placed(judged: str, **kwargs: object) -> Pipeline:
        """The stage as a caller actually uses it: hosted, wired and branched on."""
        # A provider that can resume a session: converge keeps each attempt's,
        # so pass two revises rather than starting again.
        parent = Pipeline(pipeline_id=f"c-{judged}", provider="claude-agent-sdk")
        brief = parent.declare_input("brief", STR)
        node = _converge(stage_id=f"loop-{judged}", judge=judged, **kwargs).instantiate(parent)
        parent.set_entry(node)
        parent.connect_input(brief, node, "brief")
        ok = parent.add(succeed(node_id="ok", reason="ok"))
        no = parent.add(succeed(node_id="no", reason="gave up"))
        parent.branch_on_outcome(node, {CONVERGED: ok, EXHAUSTED: no})
        return parent

    @pytest.mark.parametrize("judged", ["model", "human", "self"])
    def test_the_step_that_routes_on_exhaustion_can_see_the_count(self, judged: str) -> None:
        """A route condition renders in the routing step's own scope, not the run's.

        Under `context.mode: explicit` — the default — a step sees only what its
        `input:` names, so routing on an unwired counter is
        `'pass_number' is undefined` on the first pass, after the attempt has
        been paid for. The human path had this right from the start only because
        a gate cannot test a counter at all; the other two routed on a value they
        never declared, and nothing placed one to notice.
        """
        extra = {"judge_prompt": "", "verdict_port": "ok", "attempt": SELF_CHECK}
        parent = self._placed(judged, **(extra if judged == "self" else {}))
        assert lint_pipeline(parent, backend=ConductorBackend()) == []

    def test_an_arrays_element_shape_survives_the_scope_boundary(self) -> None:
        """A carried array without its item schema cannot be fanned out over.

        The scope contract takes an `OutputPort` for exactly this reason;
        rebuilding it from the port *type* threw the shape away one line later.
        """
        scope = _converge(attempt=FOUND, judge="model", judge_prompt="Enough?")
        repos = next(p for p in scope.output_ports if p.name == "repos")
        assert repos.element == {"name": STR, "url": STR}

    def test_it_loads_in_conductor(self, validates: Callable[[Pipeline], None]) -> None:
        for judged in ("model", "human"):
            validates(self._placed(judged))


class TestBriefingGate:
    def test_it_reports_a_decision_rather_than_acting_on_one(self) -> None:
        """Every option exits the same way; the caller decides what it means."""
        stage = briefing_gate(stage_id="b", subject="x", question="ok?")
        gate = next(n for n in stage.body.nodes if n.node_id == "review")
        targets = {e.describe_target for e in stage.body.outgoing(gate)}
        assert targets == {"recorded"}
        # `notes` comes with the default reject option, which asks why.
        assert {p.name for p in stage.output_ports} == {"decision", "summary", "notes"}

    def test_data_type_is_part_of_the_contract(self) -> None:
        as_string = briefing_gate(stage_id="b", subject="x", question="?", data_type=STR)
        assert as_string.input_ports[0].port_type is STR
        assert (
            briefing_gate(stage_id="b", subject="x", question="?").input_ports[0].port_type is OBJ
        )

    def test_a_mistyped_parent_wiring_is_refused(self) -> None:
        from ictus import PortTypeError
        from ictus.graph.node import AgentNode
        from ictus.graph.ports import OutputPort

        parent = Pipeline(pipeline_id="p")
        src = parent.add(
            AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("v", STR),))
        )
        host = briefing_gate(stage_id="b", subject="x", question="?").instantiate(parent)
        with pytest.raises(PortTypeError):
            parent.connect(src, "v", host, "data")  # string into an object port


class TestScriptSequence:
    def test_steps_are_chained_in_order(self) -> None:
        stage = script_sequence(
            stage_id="s",
            steps=(
                ScriptStep("one", "/bin/true", (), "a"),
                ScriptStep("two", "/bin/true", (), "b"),
                ScriptStep("three", "/bin/true", (), "c"),
            ),
        )
        edges = [(e.source.node_id, e.describe_target) for e in stage.body.edges]
        assert edges == [("one", "two"), ("two", "three"), ("three", "END")]
        assert [p.name for p in stage.output_ports] == ["result"]

    def test_an_empty_sequence_is_refused(self) -> None:
        from ictus import CompositionError

        with pytest.raises(CompositionError, match="at least one step"):
            script_sequence(stage_id="s", steps=())

    def test_no_step_is_a_model_call(self) -> None:
        stage = script_sequence(stage_id="s", steps=(ScriptStep("one", "/bin/true", (), "a"),))
        assert {n.kind for n in stage.body.nodes} == {NodeKind.SUBPROCESS}


def test_two_stages_compose_into_one_parent(validates: Callable[[Pipeline], None]) -> None:
    """The demo's shape: stage -> stage, branched on the second one's decision."""
    parent = Pipeline(pipeline_id="composed")
    env = parent.declare_input("environment", STR)
    reset = script_sequence(
        stage_id="reset", steps=(ScriptStep("go", "/bin/true", (), "status"),)
    ).instantiate(parent, node_id="reset")
    review = briefing_gate(
        stage_id="review", subject="reset", question="Go live?", data_type=STR
    ).instantiate(parent, node_id="confirm")
    live = parent.add(succeed(node_id="live", reason="live"))
    stop = parent.add(succeed(node_id="stop", reason="stopped"))

    parent.connect_input(env, reset, "environment")
    parent.connect(reset, "result", review, "data")
    parent.route(review, live, when="{{ confirm.output.decision == 'approved' }}")
    parent.route(review, stop)

    assert lint_pipeline(parent) == []
    validates(parent)


class TestReviewOptions:
    """A reviewer needs to say *why*, and *where the work should go back to*.

    Approve/reject alone cannot express either: the next attempt is a guess, and
    the caller has one branch where it needs several.
    """

    @staticmethod
    def _stage() -> Stage:
        return briefing_gate(
            stage_id="rev",
            subject="change",
            question="Ship it?",
            data_type=STR,
            options=(
                ReviewOption("approved", "Approve"),
                ReviewOption("replan", "Send back to planning", ask_for_notes=True),
                ReviewOption("redo", "Re-run execution", ask_for_notes=True),
            ),
        )

    def test_each_option_becomes_a_gate_choice(self) -> None:
        gate = next(n for n in self._stage().body.nodes if n.node_id == "review")
        assert isinstance(gate, GateNode)
        assert [c.value for c in gate.choices] == ["approved", "replan", "redo"]

    def test_only_the_options_that_asked_for_notes_prompt_for_them(self) -> None:
        gate = next(n for n in self._stage().body.nodes if n.node_id == "review")
        assert isinstance(gate, GateNode)
        asked = {c.value: c.prompt_for for c in gate.choices}
        assert asked == {"approved": None, "replan": "notes", "redo": "notes"}

    def test_the_notes_are_part_of_the_stage_contract(self) -> None:
        assert "notes" in {p.name for p in self._stage().output_ports}

    def test_the_notes_output_is_defaulted(self) -> None:
        """Reading a gate's free text on a branch that never asked for it is a
        hard template error — verified against the engine, not assumed."""
        emitted = ConductorBackend().document(self._stage().body)["output"]
        assert isinstance(emitted, dict)
        # A string fallback is quoted so both branches parse back as a string:
        # unquoted, a default that looked numeric would come back a number.
        assert emitted["notes"] == (
            "{% if review is defined %}{{ review.output.additional_input.notes | tojson }}"
            '{% else %}""{% endif %}'
        )

    def test_a_caller_can_route_on_which_option_was_chosen(self) -> None:
        parent = Pipeline(pipeline_id="outer", loop_passes=2)
        first = parent.add(
            AgentNode(node_id="work", prompt="do it", declared_outputs=(OutputPort("out", STR),))
        )
        host = self._stage().instantiate(parent, node_id="review")
        done = parent.add(succeed(node_id="done", reason="d"))
        parent.set_entry(first)
        parent.connect(first, "out", host, "data")
        parent.route(host, done, when=equals(host.ref("decision"), "approved"))
        parent.route(host, first, when=equals(host.ref("decision"), "redo"))
        parent.route(host, done)
        assert lint_pipeline(parent) == []
        agents = ConductorBackend().document(parent)["agents"]
        assert isinstance(agents, list)
        entry = next(a for a in agents if isinstance(a, dict) and a["name"] == "review")
        assert entry["routes"] == [
            {"to": "done", "when": "{{ review.output.decision == 'approved' }}"},
            {"to": "work", "when": "{{ review.output.decision == 'redo' }}"},
            {"to": "done"},
        ]


class TestConvergeSequence:
    """A sequence of attempts is a chain, not two steps that happen to run in order."""

    @staticmethod
    def _scope() -> Scope:
        return converge(
            stage_id="seq",
            judge="model",
            judge_prompt="Good?",
            passes=2,
            attempt=(
                Attempt(node_id="write", prompt="Write it.", produces=(OutputPort("code", STR),)),
                Attempt(node_id="test", prompt="Test it.", produces=(OutputPort("report", STR),)),
            ),
        )

    def test_a_later_step_can_read_what_an_earlier_one_produced(self) -> None:
        """`route` is control-only, so without a data edge step two sees nothing."""
        emitted = _agent_in(self._scope().body, "test")
        assert emitted["input"] == ["workflow.input.brief", "write.output.code"]
        prompt = emitted["prompt"]
        assert isinstance(prompt, str)
        assert "{{ write.output.code }}" in prompt

    def test_the_loop_converges_on_the_last_step(self) -> None:
        """The judge reads the final step's output, and the count it routes on."""
        judge = _agent_in(self._scope().body, "judge")
        assert judge["input"] == ["test.output.report", "pass_number.output"]

    def test_it_is_lint_clean(self) -> None:
        assert lint_pipeline(self._scope().body) == []


def _agent_in(pipeline: Pipeline, name: str) -> YamlDict:
    agents = ConductorBackend().document(pipeline)["agents"]
    assert isinstance(agents, list)
    for candidate in agents:
        if isinstance(candidate, dict) and candidate.get("name") == name:
            return candidate
    raise AssertionError(name)


class TestTryShell:
    """A command that fails has to arrive as a value, or the branch is decoration."""

    @staticmethod
    def _scope(**kwargs: object) -> Scope:
        settings: dict[str, object] = {"stage_id": "reset", "command": "/bin/false"}
        settings.update(kwargs)
        return try_shell(**settings)  # type: ignore[arg-type]

    def _script(self, scope: Scope) -> YamlDict:
        agents = ConductorBackend().document(scope.body)["agents"]
        assert isinstance(agents, list)
        found = [a for a in agents if isinstance(a, dict) and a.get("type") == "script"]
        assert len(found) == 1
        return found[0]

    def test_the_command_step_declares_only_what_the_reporter_always_prints(self) -> None:
        """A schema naming a field would raise on a failing command before routing.

        The reporter prints these three for every command that ran, whatever it
        exited with, so this schema can only refuse a command that never ran.
        """
        scope = self._scope(outputs=(OutputPort("revision", STR, "What it restored"),))
        run = _agent_in(scope.body, "run")
        output = run["output"]
        assert isinstance(output, dict)
        assert set(output) == {"stdout", "stderr", "exit_code"}

    def test_fields_are_parsed_only_after_the_status_said_zero(self) -> None:
        scope = self._scope(outputs=(OutputPort("revision", STR, "What it restored"),))
        assert _agent_in(scope.body, "run")["routes"] == [
            {"to": "run_fields", "when": "{{ run.output.exit_code | int == 0 }}"},
            {"to": "failed"},
        ]
        fields = _agent_in(scope.body, "run_fields")
        assert fields["routes"] == [{"to": "ok"}]
        output = fields["output"]
        assert isinstance(output, dict)
        assert set(output) == {"revision"}

    def test_a_command_with_no_fields_has_no_step_to_parse_them(self) -> None:
        assert "run_fields" not in {n.node_id for n in self._scope().body.nodes}

    def test_preflight_is_told_the_command_needs_python(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = shutil.which
        monkeypatch.setattr(
            "ictus.interfaces.environment.shutil.which",
            lambda name, *a, **k: None if name == "python3" else real(name, *a, **k),
        )
        issues = ConductorBackend().preflight(self._scope().body, probe=False)
        assert [i for i in issues if i.requirement == "exe:python3"]

    def test_the_ports_survive_for_composition_anyway(self) -> None:
        """Dropping the run-time contract must not drop the compile-time one."""
        scope = self._scope()
        run = next(n for n in scope.body.nodes if n.node_id == "run")
        assert [p.name for p in run.outputs] == ["stdout", "stderr", "exit_code"]
        with pytest.raises(Exception, match="typo"):
            run.ref("typo")

    def test_success_is_tested_and_failure_is_the_catch_all(self) -> None:
        """A signal-killed child exits -9. `exit_code >= 1` reads that as success,
        which is the exact bug the scope exists to remove."""
        routes = self._script(self._scope())["routes"]
        assert isinstance(routes, list)
        assert routes == [
            {"to": "ok", "when": "{{ run.output.exit_code | int == 0 }}"},
            {"to": "failed"},
        ]

    def test_both_outcomes_carry_the_baseline(self) -> None:
        """A key on one branch and absent on the other is a StrictUndefined error
        waiting for whichever branch nobody exercised."""
        scope = self._scope(outputs=(OutputPort("revision", STR, "What it restored"),))
        agents = ConductorBackend().document(scope.body)["agents"]
        assert isinstance(agents, list)
        exits = {
            a["name"]: a["output_template"]
            for a in agents
            if isinstance(a, dict) and a.get("type") == "terminate"
        }
        assert set(exits) == {OK, FAILED}
        for template in exits.values():
            assert isinstance(template, dict)
            assert set(template) == {"outcome", "stdout", "stderr", "exit_code", "revision"}
        broke, worked = exits[FAILED], exits[OK]
        assert isinstance(broke, dict) and isinstance(worked, dict)
        assert broke["revision"] == "", "the failed path must not read a field of the JSON"
        assert worked["revision"] == "{{ run_fields.output.revision | tojson }}"

    def test_a_declared_field_shadowing_the_baseline_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="already carries"):
            self._scope(outputs=(OutputPort("exit_code", PortType.NUMBER, "mine"),))

    def test_the_parameter_reaches_the_command_as_its_last_argument(self) -> None:
        script = self._script(self._scope(args=("--full",), parameter="backup"))
        args = script["args"]
        assert isinstance(args, list)
        assert args[-3:] == ["/bin/false", "--full", "{{ workflow.input.backup }}"]

    def test_the_caller_cannot_leave_the_failure_unrouted(self) -> None:
        """Refused where it is written, not reported as a dead end at lint time."""
        parent = Pipeline(pipeline_id="provision")
        reset = self._scope().instantiate(parent, node_id="reset")
        parent.set_entry(reset)
        done = parent.add(succeed(node_id="live", reason="live"))
        with pytest.raises(CompositionError, match=r"unrouted outcome\(s\) \['failed'\]"):
            parent.branch_on_outcome(reset, {OK: done})

    def test_the_body_is_lint_clean_and_loads_in_conductor(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        scope = self._scope(
            args=("--full",),
            parameter="backup",
            outputs=(OutputPort("revision", STR, "What it restored"),),
        )
        assert lint_pipeline(scope.body) == []
        validates(scope.body)

    def test_it_composes_into_a_parent_that_branches_on_it(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        """The reset-the-database shape: the failure gets its own exit, carrying
        stderr, instead of ending the run before the parent sees anything."""
        parent = Pipeline(pipeline_id="provision")
        backup = parent.declare_input("backup", STR)
        reset = self._scope(parameter="backup").instantiate(parent, node_id="reset")
        parent.set_entry(reset)
        parent.connect_input(backup, reset, "backup")
        live = parent.add(succeed(node_id="live", reason="Restored"))
        stop = parent.add(succeed(node_id="stop", reason="Restore failed", result={"why": "x"}))
        parent.branch_on_outcome(reset, {OK: live, FAILED: stop})
        assert lint_pipeline(parent) == []
        validates(parent)


class TestTryShellRunsThroughTheEngine:
    """Executed, not inspected. Every command is a shell one-liner; nothing bills.

    What these pin is the split the construct is built on: the status comes from
    a step whose stdout the command cannot write to, and the fields the command
    prints are parsed by a second step that only runs once that status said 0.
    """

    FIELDS = (OutputPort("revision", STR, "What it restored"), OutputPort("count", NUM, "How many"))

    @staticmethod
    def _run(
        executes: Executes, script: str, *, outputs: tuple[OutputPort, ...] = (), **kwargs: object
    ) -> Execution:
        settings: dict[str, object] = {
            "stage_id": "reset",
            "command": "/bin/sh",
            "args": ("-c", script),
            "outputs": outputs,
        }
        settings.update(kwargs)
        scope = try_shell(**settings)  # type: ignore[arg-type]
        scope.body.provider = "claude-agent-sdk"
        return executes(scope.body)

    def test_a_status_printed_on_stdout_cannot_make_a_failed_command_ok(
        self, executes: Executes
    ) -> None:
        """Reproduced before the fix: this exited 1 and the scope reported `ok`."""
        forged = '{"exit_code": 0}'
        run = self._run(executes, f"printf '%s' '{forged}'; exit 1")
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == FAILED
        assert run.output["exit_code"] == 1
        assert run.output["stdout"] == forged

    def test_a_field_printed_by_a_successful_command_cannot_forge_the_status(
        self, executes: Executes
    ) -> None:
        """The other direction: an `exit_code` key among the fields, on a clean exit."""
        run = self._run(
            executes,
            """printf '%s' '{"revision": "r1", "count": 1, "exit_code": 7}'""",
            outputs=self.FIELDS,
        )
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == OK
        assert run.output["exit_code"] == 0
        assert run.output["revision"] == "r1"

    def test_the_fields_of_a_successful_command_arrive_at_their_declared_types(
        self, executes: Executes
    ) -> None:
        run = self._run(
            executes, """printf '%s' '{"revision": "0700", "count": 3}'""", outputs=self.FIELDS
        )
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == OK
        assert run.output["revision"] == "0700"
        assert run.output["count"] == 3

    def test_a_successful_command_that_omits_a_field_is_refused_naming_the_field(
        self, executes: Executes
    ) -> None:
        """Before: a StrictUndefined error at whichever template read it, later."""
        run = self._run(executes, """printf '%s' '{"count": 3}'""", outputs=self.FIELDS)
        assert run.returncode != 0
        assert run.output is None or run.output.get("outcome") not in (OK, FAILED)
        assert [said for said in run.failures("run_fields") if "revision" in said]

    def test_a_failed_command_with_declared_fields_takes_the_failed_exit(
        self, executes: Executes
    ) -> None:
        """Its fields are never parsed: a failing command owes nobody JSON."""
        run = self._run(executes, "echo boom >&2; exit 2", outputs=self.FIELDS)
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == FAILED
        assert run.output["exit_code"] == 2
        assert run.output["stderr"] == "boom\n"
        assert run.output["revision"] == ""
        assert run.count("script_started", "run_fields") == 0

    def test_stdin_reaches_the_command(self, executes: Executes) -> None:
        run = self._run(executes, "cat", stdin="handed over")
        assert run.output is not None, run.stderr
        assert run.output["outcome"] == OK
        assert run.output["stdout"] == "handed over"

    def test_the_parameter_reaches_the_command_as_its_last_argument(
        self, executes: Executes
    ) -> None:
        scope = try_shell(
            stage_id="reset",
            command="/bin/sh",
            args=("-c", 'printf %s "$1"', "sh"),
            parameter="backup",
        )
        scope.body.provider = "claude-agent-sdk"
        run = executes(scope.body, backup="false")
        assert run.output is not None, run.stderr
        assert run.output["stdout"] == "false"

    def test_a_command_that_cannot_start_ends_the_run_outside_the_outcomes(
        self, executes: Executes, tmp_path: Path
    ) -> None:
        scope = try_shell(stage_id="reset", command=str(tmp_path / "not-installed"))
        scope.body.provider = "claude-agent-sdk"
        run = executes(scope.body)
        assert run.returncode != 0
        assert run.output is None or run.output.get("outcome") not in (OK, FAILED)
        assert [said for said in run.failures("run") if "could not start" in said]

    def test_a_command_that_times_out_ends_the_run_outside_the_outcomes(
        self, executes: Executes
    ) -> None:
        run = self._run(executes, "sleep 30", timeout=1)
        assert run.returncode != 0
        assert run.output is None or run.output.get("outcome") not in (OK, FAILED)
        assert [said for said in run.failures("run") if "timed out after 1 seconds" in said]


def _terminal_in(pipeline: Pipeline, name: str) -> YamlDict:
    found = _agent_in(pipeline, name)
    assert found["type"] == "terminate"
    return found


CHOICES = (
    Choice("simple", "mechanical, one file, nothing to decide"),
    Choice("hard", "spans modules or needs a design decision"),
)


class TestClassify:
    """An N-way model decision whose vocabulary is checked on both ends."""

    @staticmethod
    def _scope(**kwargs: object) -> Scope:
        settings: dict[str, object] = {
            "stage_id": "triage",
            "question": "How hard is this?",
            "choices": CHOICES,
        }
        settings.update(kwargs)
        return classify(**settings)  # type: ignore[arg-type]

    def test_every_choice_becomes_an_outcome_plus_unclear(self) -> None:
        assert self._scope().outcomes == ("simple", "hard", UNCLEAR)

    def test_an_answer_outside_the_vocabulary_is_its_own_exit(self) -> None:
        """Not the last choice in the list. "I could not classify this" and "I
        classified this as the last option" are different facts."""
        routes = _agent_in(self._scope().body, "decide")["routes"]
        assert isinstance(routes, list)
        assert routes == [
            {"to": "is_simple", "when": "{{ decide.output.choice == 'simple' }}"},
            {"to": "is_hard", "when": "{{ decide.output.choice == 'hard' }}"},
            {"to": UNCLEAR},
        ]

    def test_the_unclear_exit_carries_what_the_model_actually_said(self) -> None:
        """Without it, an invented category is indistinguishable from a hedge."""
        template = _terminal_in(self._scope().body, UNCLEAR)["output_template"]
        assert isinstance(template, dict)
        assert template["answer"] == "{{ decide.output.choice | tojson }}"

    def test_the_caller_must_route_every_choice(self) -> None:
        parent = Pipeline(pipeline_id="p")
        node = self._scope().instantiate(parent, node_id="triage")
        parent.set_entry(node)
        done = parent.add(succeed(node_id="done", reason="d"))
        with pytest.raises(CompositionError, match=r"unrouted outcome"):
            parent.branch_on_outcome(node, {"simple": done, "hard": done})

    def test_one_choice_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="at least two choices"):
            self._scope(choices=(CHOICES[0],))

    def test_a_repeated_choice_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="repeats the choice"):
            self._scope(choices=(*CHOICES, Choice("simple", "again")))

    def test_a_choice_named_unclear_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="already goes"):
            self._scope(choices=(*CHOICES, Choice(UNCLEAR, "no idea")))

    def test_a_choice_without_a_meaning_is_refused(self) -> None:
        """A vocabulary of bare words is a classifier that guesses."""
        with pytest.raises(CompositionError, match="needs a meaning"):
            Choice("simple", "")

    @pytest.mark.parametrize("value", ["needs work", "high-risk", "Simple", "a/b"])
    def test_a_choice_that_cannot_name_a_route_is_refused_where_it_is_written(
        self, value: str
    ) -> None:
        """The value becomes an outcome *and* an exit node id, so it has to be one.

        It was caught before this, but two layers down and under a name nobody
        wrote: `Choice("needs work", ...)` failed with "node_id 'is_needs work'
        is not a routing identifier", about an id the caller never typed and
        after half a scope had been built. Fail where the bad value enters.
        """
        with pytest.raises(CompositionError, match="routing identifier"):
            Choice(value, "a meaning")

    def test_tools_without_a_turn_budget_are_refused(self) -> None:
        with pytest.raises(CompositionError, match="max_turns"):
            self._scope(tools=None)

    def test_it_denies_tools_by_default(self) -> None:
        """A classifier reading what it was handed should not open files."""
        assert _agent_in(self._scope().body, "decide")["tools"] == []

    def test_the_body_is_lint_clean_and_loads_in_conductor(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        assert lint_pipeline(self._scope().body) == []
        validates(self._scope().body)


TIERS = (
    Tier("quick", "a mechanical change", "Make the change.", model="cheap-model"),
    Tier("deep", "needs a design decision", "Work it out first.", tools=None, max_turns=200),
)
RESULT = (OutputPort("result", STR, "What was done"),)


class TestTiered:
    """Effort is not a value a step can produce, so the tiers are structure."""

    @staticmethod
    def _scope(**kwargs: object) -> Scope:
        settings: dict[str, object] = {
            "stage_id": "work",
            "question": "How much effort?",
            "tiers": TIERS,
            "produces": RESULT,
        }
        settings.update(kwargs)
        return tiered(**settings)  # type: ignore[arg-type]

    def test_the_outcomes_are_done_and_unclear_not_one_per_tier(self) -> None:
        """A caller almost never branches on which tier ran; it reads `tier`."""
        assert self._scope().outcomes == (DONE, UNCLEAR)

    def test_each_tier_carries_its_own_ports_through_its_own_exit(self) -> None:
        """A shared exit would read every tier's ports, and the ones that did
        not run render empty rather than failing — the ambiguity this removes."""
        scope = self._scope()
        for tier in TIERS:
            template = _terminal_in(scope.body, f"{DONE}_{tier.name}")["output_template"]
            assert isinstance(template, dict)
            assert template["outcome"] == DONE
            assert template["tier"] == tier.name
            assert template["result"] == f"{{{{ {tier.name}.output.result | tojson }}}}"

    def test_the_unclear_exit_still_carries_every_declared_key(self) -> None:
        template = _terminal_in(self._scope().body, UNCLEAR)["output_template"]
        assert isinstance(template, dict)
        assert set(template) == {"outcome", "tier", "rationale", "result"}
        assert template["result"] == ""

    def test_a_tier_reaches_the_provider_as_a_declared_model(self) -> None:
        """The whole point: `model` is read raw, so it cannot come from a step."""
        assert _agent_in(self._scope().body, "quick")["model"] == "cheap-model"

    def test_a_tier_given_tools_keeps_them_and_its_turn_budget(self) -> None:
        deep = _agent_in(self._scope().body, "deep")
        assert "tools" not in deep, "None means the workflow default, not denied"
        assert deep["max_agent_iterations"] == 200

    def test_triage_never_gets_tools(self) -> None:
        """A router that opens files has become the work it was meant to route."""
        assert _agent_in(self._scope().body, "triage")["tools"] == []

    def test_a_tier_with_tools_and_no_budget_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="max_turns"):
            Tier("x", "m", "p", tools=None)

    @pytest.mark.parametrize("name", ["quick pass", "deep-dive", "Quick", "a/b"])
    def test_a_tier_that_cannot_name_a_route_is_refused_where_it_is_written(
        self, name: str
    ) -> None:
        """Same boundary as `Choice`: the name is the node the branch routes to."""
        with pytest.raises(CompositionError, match="routing identifier"):
            Tier(name, "m", "p")

    def test_a_tier_named_after_an_outcome_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="already uses"):
            self._scope(tiers=(*TIERS, Tier(DONE, "m", "p")))

    def test_produces_must_not_shadow_the_carry(self) -> None:
        with pytest.raises(CompositionError, match="already carries"):
            self._scope(produces=(OutputPort("tier", STR, "mine"),))

    def test_no_produces_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="no produces"):
            self._scope(produces=())

    def test_the_body_is_lint_clean_and_loads_in_conductor(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        assert lint_pipeline(self._scope().body) == []
        validates(self._scope().body)

    def test_it_composes_into_a_parent_that_reads_one_shape(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        parent = Pipeline(pipeline_id="p")
        work = parent.declare_input("brief", STR)
        node = self._scope().instantiate(parent, node_id="work")
        parent.set_entry(node)
        parent.connect_input(work, node, "brief")
        shipped = parent.add(succeed(node_id="shipped", reason="Done"))
        stuck = parent.add(succeed(node_id="stuck", reason="Nobody could place it"))
        parent.branch_on_outcome(node, {DONE: shipped, UNCLEAR: stuck})
        assert lint_pipeline(parent) == []
        validates(parent)


def test_the_two_scopes_share_one_unclear() -> None:
    """A flat namespace would export whichever module imported last, silently.

    `import_module` because the package binds each module's name to the
    constructor it exports, so `stages.classify` is the function, not the file.
    """
    import importlib

    a = importlib.import_module("ictus.stdlib.stages.classify")
    b = importlib.import_module("ictus.stdlib.stages.tiered")
    assert a.UNCLEAR is b.UNCLEAR is UNCLEAR
