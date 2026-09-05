"""The stage stdlib.

Each stage is a whole sub-graph, so the thing worth asserting is that its body
is a valid workflow on its own and that its contract is the shape a parent can
wire to. Both stages and their bodies go through the real Conductor validator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ictus import AgentNode, OutputPort, Pipeline, PortType, equals
from ictus.graph.node import GateNode, NodeKind
from ictus.interfaces.conductor import ConductorBackend
from ictus.lint import lint_pipeline
from ictus.stdlib import (
    ReviewOption,
    ScriptStep,
    briefing_gate,
    poll_until,
    revise_loop,
    script_sequence,
    succeed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ictus.graph.stage import Stage

STR, OBJ = PortType.STRING, PortType.OBJECT


def _cases() -> dict[str, Stage]:
    return {
        "revise_loop": revise_loop(stage_id="revise-loop", task="Draft a note", passes=3),
        "briefing_gate": briefing_gate(
            stage_id="briefing-gate", subject="change set", question="Ship it?"
        ),
        "poll_until": poll_until(
            stage_id="poll-until",
            condition="the deploy is healthy",
            check_prompt="Is it healthy?",
            max_polls=4,
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


class TestReviseLoop:
    def test_the_gate_reference_is_guarded_by_the_compiler(self) -> None:
        """The author writes a reference; the guard is the compiler's job.

        The first pass renders before the gate has run, and Conductor's strict
        undefined kills the step. Nothing in the stage source says "is defined".
        """
        stage = revise_loop(stage_id="r", task="Write")
        produce = next(n for n in stage.body.nodes if n.node_id == "produce")
        assert isinstance(produce, AgentNode)
        assert not isinstance(produce.prompt, str), "the prompt should carry typed refs"

        rendered = ConductorBackend().document(stage.body)
        agents = rendered["agents"]
        assert isinstance(agents, list)
        emitted = next(a for a in agents if isinstance(a, dict) and a["name"] == "produce")
        prompt = emitted["prompt"]
        assert isinstance(prompt, str)
        assert "{% if review is defined %}" in prompt

    def test_the_loop_bound_follows_the_pass_count(self) -> None:
        assert revise_loop(stage_id="r", task="t", passes=2).body.loop_passes == 2
        revise_loop(stage_id="r", task="t", passes=6).body.require_loop_bound()

    def test_rejection_notes_are_fed_back(self) -> None:
        stage = revise_loop(stage_id="r", task="Write")
        deps = [(d.source.node_id, d.target.node_id) for d in stage.body.data_deps]
        assert ("review", "produce") in deps, "a revise loop without feedback is a retry loop"


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


class TestPollUntil:
    def test_the_exit_condition_is_emitted_before_the_catch_all(self) -> None:
        """Conductor takes the first matching route; a catch-all first swallows the exit."""
        stage = poll_until(stage_id="p", condition="done", check_prompt="done?")
        check = next(n for n in stage.body.nodes if n.node_id == "check")
        routes = stage.body.outgoing(check)
        assert routes[0].when is not None
        assert routes[-1].when is None

    def test_the_wait_is_not_a_model_call(self) -> None:
        stage = poll_until(stage_id="p", condition="done", check_prompt="done?")
        pause = next(n for n in stage.body.nodes if n.node_id == "pause")
        assert pause.kind is NodeKind.DELAY


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
        assert "default('')" in self._stage().body.exposed_outputs["notes"]

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
