"""The stage stdlib.

Each stage is a whole sub-graph, so the thing worth asserting is that its body
is a valid workflow on its own and that its contract is the shape a parent can
wire to. Both stages and their bodies go through the real Conductor validator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ictus import AgentNode, OutputPort, Pipeline, PortType, equals
from ictus.errors import CompositionError
from ictus.graph.node import GateNode, NodeKind
from ictus.interfaces.conductor import ConductorBackend
from ictus.lint import lint_pipeline
from ictus.stdlib import (
    CONVERGED,
    EXHAUSTED,
    Attempt,
    ReviewOption,
    ScriptStep,
    briefing_gate,
    converge,
    script_sequence,
    succeed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ictus.graph.scope import Scope
    from ictus.graph.stage import Stage
    from ictus.graph.values import YamlDict

STR, OBJ = PortType.STRING, PortType.OBJECT


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
        assert template["text"] == "{{ draft.output.text }}"
        assert template["feedback"] == "{{ judge.output.notes }}"
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

    def test_it_loads_in_conductor(self, validates: Callable[[Pipeline], None]) -> None:
        for judged in ("model", "human"):
            # A provider that can resume a session: converge keeps each
            # attempt's, so pass two revises rather than starting again.
            parent = Pipeline(pipeline_id=f"c-{judged}", provider="claude-agent-sdk")
            brief = parent.declare_input("brief", STR)
            node = _converge(stage_id=f"loop-{judged}", judge=judged).instantiate(parent)
            parent.set_entry(node)
            parent.connect_input(brief, node, "brief")
            ok = parent.add(succeed(node_id="ok", reason="ok"))
            no = parent.add(succeed(node_id="no", reason="gave up"))
            parent.branch_on_outcome(node, {CONVERGED: ok, EXHAUSTED: no})
            assert lint_pipeline(parent) == []
            validates(parent)


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
        judge = _agent_in(self._scope().body, "judge")
        assert judge["input"] == ["test.output.report"]

    def test_it_is_lint_clean(self) -> None:
        assert lint_pipeline(self._scope().body) == []


def _agent_in(pipeline: Pipeline, name: str) -> YamlDict:
    agents = ConductorBackend().document(pipeline)["agents"]
    assert isinstance(agents, list)
    for candidate in agents:
        if isinstance(candidate, dict) and candidate.get("name") == name:
            return candidate
    raise AssertionError(name)
