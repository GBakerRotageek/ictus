"""Generic composition lints — one negative test per rule.

These hold for any graph, whatever runs it, so none of them passes a backend.
Rules that are true because of how one engine runs are tested next to that
engine, in ``test_conductor_lints.py``.
"""

from __future__ import annotations

import pytest

from ictus import END, AgentNode, InputPort, LintError, OutputPort, Pipeline, PortType, Stage
from ictus.lint import check, lint_pipeline
from ictus.stdlib import succeed

S, N = PortType.STRING, PortType.NUMBER


def _matching(pipeline: Pipeline, fragment: str) -> list[str]:
    return [p for p in lint_pipeline(pipeline) if fragment in p]


def test_unreachable_agent_is_reported() -> None:
    p = Pipeline(pipeline_id="t")
    start = p.add(AgentNode(node_id="start", prompt="go"))
    p.add(AgentNode(node_id="orphan", prompt="never"))
    p.set_entry(start)
    p.route(start, END)
    assert _matching(p, "'orphan' is unreachable")


def test_conditional_routes_without_a_catch_all_are_reported() -> None:
    """Falling off a route list raises at run time and passes validation."""
    p = Pipeline(pipeline_id="t")
    src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("v", S),)))
    only = p.add(succeed(node_id="only", reason="o"))
    p.route(src, only, when="{{ src.output.v == 'yes' }}")
    assert _matching(p, "only conditional routes")


def test_adding_the_catch_all_clears_it() -> None:
    p = Pipeline(pipeline_id="t")
    src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("v", S),)))
    only = p.add(succeed(node_id="only", reason="o"))
    other = p.add(succeed(node_id="other", reason="x"))
    p.route(src, only, when="{{ src.output.v == 'yes' }}")
    p.route(src, other)
    assert lint_pipeline(p) == []


def test_implicit_end_is_reported() -> None:
    """A missing route and an intended finish are indistinguishable to Conductor."""
    p = Pipeline(pipeline_id="t")
    p.add(AgentNode(node_id="dangling", prompt="x"))
    assert _matching(p, "implicitly ends the run")


def test_unwired_required_input_is_reported() -> None:
    p = Pipeline(pipeline_id="t")
    node = p.add(AgentNode(node_id="needs", inputs=(InputPort("thing", S),), prompt="x"))
    p.route(node, END)
    assert _matching(p, "required input 'thing'")


def test_optional_input_may_be_unwired() -> None:
    p = Pipeline(pipeline_id="t")
    node = p.add(
        AgentNode(node_id="needs", inputs=(InputPort("thing", S, optional=True),), prompt="x")
    )
    p.route(node, END)
    assert lint_pipeline(p) == []


class TestStageContract:
    """Conductor never compares a stage's input_mapping against the child's inputs."""

    @staticmethod
    def _stage(input_type: PortType = S) -> Stage:
        stage = Stage(stage_id="inner")
        param = stage.body.declare_input("x", input_type)
        step = stage.body.add(
            AgentNode(
                node_id="w",
                inputs=(InputPort("x", input_type),),
                prompt="w",
                declared_outputs=(OutputPort("y", S),),
            )
        )
        stage.body.connect_input(param, step, "x")
        stage.body.route(step, END)
        stage.body.expose_output("y", step, "y")
        return stage

    def test_unwired_stage_input_is_reported(self) -> None:
        parent = Pipeline(pipeline_id="outer")
        host = self._stage().instantiate(parent)
        done = parent.add(succeed(node_id="done", reason="d", inputs=(InputPort("y", S),)))
        parent.connect(host, "y", done, "y")
        assert _matching(parent, "required input 'x'")

    def test_contract_drift_after_instantiation_is_reported(self) -> None:
        """The reachable version of the gap Conductor leaves open.

        ``instantiate`` copies the child's contract onto the node, so a mismatch
        cannot be written directly. It can still drift: declaring a new required
        input on the body afterwards leaves the placement stale, and Conductor
        compares the two sides never.
        """
        stage = self._stage()
        parent = Pipeline(pipeline_id="outer")
        param = parent.declare_input("x", S)
        host = stage.instantiate(parent)
        done = parent.add(succeed(node_id="done", reason="d", inputs=(InputPort("y", S),)))
        parent.connect_input(param, host, "x")
        parent.connect(host, "y", done, "y")
        assert lint_pipeline(parent) == []

        stage.body.declare_input("added_later", N)
        assert _matching(parent, "leaves required input 'added_later'")

    def test_wired_stage_is_clean(self) -> None:
        parent = Pipeline(pipeline_id="outer")
        param = parent.declare_input("x", S)
        host = self._stage().instantiate(parent)
        done = parent.add(succeed(node_id="done", reason="d", inputs=(InputPort("y", S),)))
        parent.connect_input(param, host, "x")
        parent.connect(host, "y", done, "y")
        assert lint_pipeline(parent) == []

    def test_child_problems_surface_through_the_parent(self) -> None:
        stage = self._stage()
        stage.body.add(AgentNode(node_id="stray", prompt="never runs"))
        parent = Pipeline(pipeline_id="outer")
        param = parent.declare_input("x", S)
        host = stage.instantiate(parent)
        done = parent.add(succeed(node_id="done", reason="d", inputs=(InputPort("y", S),)))
        parent.connect_input(param, host, "x")
        parent.connect(host, "y", done, "y")
        assert _matching(parent, "inner: agent 'stray' is unreachable")


def test_check_raises_with_every_violation_listed() -> None:
    p = Pipeline(pipeline_id="t")
    p.add(AgentNode(node_id="a", inputs=(InputPort("missing", S),), prompt="x"))
    with pytest.raises(LintError) as excinfo:
        check(p)
    assert len(excinfo.value.violations) >= 1
    assert "required input 'missing'" in str(excinfo.value)
