"""Distinct stage bodies must never share a compiled workflow by accident."""

from __future__ import annotations

import pytest

from ictus import END, AgentNode, CompositionError, EmitError, OutputPort, Pipeline, PortType, Stage
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline


def _stage(name: str, prompt: str = "work") -> Stage:
    stage = Stage(stage_id=name)
    node = stage.body.add(
        AgentNode(
            node_id="work", prompt=prompt, declared_outputs=(OutputPort("result", PortType.STRING),)
        )
    )
    stage.body.set_entry(node)
    stage.body.route(node, END)
    stage.body.expose_output("result", node, "result")
    return stage


def _host(stage: Stage, parent: Pipeline, name: str) -> None:
    node = stage.instantiate(parent, node_id=name)
    if len(parent.nodes) == 1:
        parent.set_entry(node)
    else:
        parent.route(parent.nodes[-2], node)


def test_distinct_stages_with_one_id_are_rejected_without_registering_the_second() -> None:
    parent = Pipeline(pipeline_id="outer")
    first = _stage("shared", "first").instantiate(parent, node_id="first")
    with pytest.raises(CompositionError, match=r"shared.*distinct"):
        _stage("shared", "second").instantiate(parent, node_id="second")
    assert parent.nodes == (first,)
    assert list(parent.children) == ["first"]


def test_stage_cannot_share_its_parent_id() -> None:
    parent = Pipeline(pipeline_id="shared")
    with pytest.raises(CompositionError, match=r"shared.*distinct"):
        _stage("shared").instantiate(parent)
    assert parent.nodes == ()


def test_collisions_between_nested_branches_are_rejected_when_joined() -> None:
    left, right = _stage("left"), _stage("right")
    _stage("shared", "first").instantiate(left.body)
    _stage("shared", "second").instantiate(right.body)
    parent = Pipeline(pipeline_id="outer")
    first = left.instantiate(parent)
    with pytest.raises(CompositionError, match=r"shared.*distinct"):
        right.instantiate(parent)
    assert parent.nodes == (first,)


def test_same_stage_can_be_instantiated_twice_and_emits_once() -> None:
    parent = Pipeline(pipeline_id="outer")
    stage = _stage("shared")
    _host(stage, parent, "first")
    _host(stage, parent, "second")
    parent.route(parent.nodes[-1], END)
    assert lint_pipeline(parent) == []
    docs = conductor.compile(parent)
    assert [doc.filename for doc in docs] == ["outer.yaml", "shared.yaml"]
    assert docs[0].content.count("workflow: ./shared.yaml") == 2


def test_lint_checks_both_bodies_after_a_late_nested_collision() -> None:
    parent = Pipeline(pipeline_id="outer")
    first, second = _stage("first"), _stage("second")
    _host(first, parent, "first")
    _host(second, parent, "second")
    parent.route(parent.nodes[-1], END)
    _stage("shared", "valid").instantiate(first.body)
    broken = _stage("shared", "broken")
    broken.body.add(AgentNode(node_id="orphan", prompt="unreachable"))
    broken.instantiate(second.body)
    problems = lint_pipeline(parent)
    assert any("shared" in problem and "distinct" in problem for problem in problems)
    assert any("shared" in problem and "'orphan' is unreachable" in problem for problem in problems)
    with pytest.raises(EmitError, match=r"shared\.yaml.*distinct"):
        conductor.compile(parent)


def test_compiler_refuses_a_stage_renamed_to_the_root_after_instantiation() -> None:
    parent = Pipeline(pipeline_id="outer")
    stage = _stage("inner")
    _host(stage, parent, "call")
    parent.route(parent.nodes[-1], END)
    stage.body.pipeline_id = "outer"
    with pytest.raises(EmitError, match=r"outer\.yaml.*distinct"):
        conductor.compile(parent)


def _shared_descendant(*, same_provider: bool, intermediate: bool = False) -> Pipeline:
    root = Pipeline(pipeline_id="outer")
    shared = _stage("shared")
    if intermediate:
        wrapper = Stage(stage_id="wrapper")
        host = shared.instantiate(wrapper.body)
        wrapper.body.route(host, END)
        wrapper.body.expose_output("result", host, "result")
        shared = wrapper
    for name, provider in (("left", "openai"), ("right", "openai" if same_provider else "claude")):
        branch = Stage(stage_id=name)
        branch.body.provider = provider
        host = shared.instantiate(branch.body)
        branch.body.route(host, END)
        branch.body.expose_output("result", host, "result")
        _host(branch, root, name)
    root.route(root.nodes[-1], END)
    return root


def test_shared_stage_under_matching_parent_settings_emits_once() -> None:
    root = _shared_descendant(same_provider=True)
    docs = conductor.compile(root)
    assert [doc.filename for doc in docs] == [
        "outer.yaml",
        "left.yaml",
        "shared.yaml",
        "right.yaml",
    ]
    shared = next(doc for doc in docs if doc.filename == "shared.yaml")
    assert "name: openai" in shared.content


def test_shared_stage_with_conflicting_inherited_settings_is_rejected() -> None:
    root = _shared_descendant(same_provider=False)
    with pytest.raises(EmitError, match=r"shared\.yaml.*inherited"):
        conductor.compile(root)


def test_inheritance_conflict_is_detected_below_identically_rendered_shared_parent() -> None:
    root = _shared_descendant(same_provider=True, intermediate=True)
    root.children["left"].system_prompt = "left baseline"
    root.children["right"].system_prompt = "right baseline"
    with pytest.raises(EmitError, match=r"shared\.yaml.*inherited"):
        conductor.compile(root)
