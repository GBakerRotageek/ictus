"""Typed references.

A reference used to be text inside a prompt, recoverable only by regular
expression. These tests pin what changed: the port is checked where the
reference is written, the type travels with it, and a reference into a loop is
resolved against the finished graph rather than trusted.
"""

from __future__ import annotations

import pytest

from ictus import (
    END,
    AgentNode,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    UnknownPortError,
    optional,
    ref_to,
    tpl,
)
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline
from ictus.stdlib import approval_gate, succeed

STR, NUM = PortType.STRING, PortType.NUMBER


def _producer(node_id: str = "src", port_type: PortType = STR) -> AgentNode:
    return AgentNode(node_id=node_id, prompt="x", declared_outputs=(OutputPort("v", port_type),))


class TestRefIsCheckedWhereWritten:
    def test_an_undeclared_port_raises_immediately(self) -> None:
        with pytest.raises(UnknownPortError, match="no output port 'nope'"):
            _producer().ref("nope")

    def test_the_port_type_travels_with_the_reference(self) -> None:
        assert _producer(port_type=NUM).ref("v").port_type is NUM

    def test_a_workflow_input_reference_is_typed_too(self) -> None:
        p = Pipeline(pipeline_id="t")
        param = p.declare_input("who", STR)
        ref = param.ref()
        assert ref.from_input
        assert ref.port_type is STR


class TestForwardReferences:
    """``ref_to`` names a node that does not exist yet. It is still checked."""

    @staticmethod
    def _with_prompt(prompt: object) -> Pipeline:
        p = Pipeline(pipeline_id="t")
        node = p.add(AgentNode(node_id="a", prompt=prompt))  # type: ignore[arg-type]
        p.route(node, END)
        return p

    def test_an_unknown_node_is_reported(self) -> None:
        p = self._with_prompt(tpl("see ", ref_to("ghost", "v", STR)))
        assert [x for x in lint_pipeline(p) if "references unknown node 'ghost'" in x]

    def test_an_undeclared_port_is_reported(self) -> None:
        p = Pipeline(pipeline_id="t")
        src = p.add(_producer())
        dst = p.add(AgentNode(node_id="dst", prompt=tpl("see ", ref_to("src", "typo", STR))))
        p.route(src, dst)
        p.route(dst, END)
        assert [x for x in lint_pipeline(p) if "src.typo" in x]

    def test_a_wrong_type_is_reported(self) -> None:
        p = Pipeline(pipeline_id="t")
        src = p.add(_producer(port_type=STR))
        dst = p.add(AgentNode(node_id="dst", prompt=tpl("see ", ref_to("src", "v", NUM))))
        p.route(src, dst)
        p.route(dst, END)
        assert [x for x in lint_pipeline(p) if "but it is declared string" in x]

    def test_a_correct_forward_reference_is_clean(self) -> None:
        p = Pipeline(pipeline_id="t")
        src = p.add(_producer())
        dst = p.add(AgentNode(node_id="dst", prompt=tpl("see ", ref_to("src", "v", STR))))
        p.route(src, dst)
        p.route(dst, END)
        assert lint_pipeline(p) == []


class TestGuardIsTheCompilersJob:
    """The author writes a reference; the guard is emitted for them."""

    @staticmethod
    def _loop() -> Pipeline:
        p = Pipeline(pipeline_id="t", loop_passes=2)
        draft = p.add(
            AgentNode(
                node_id="draft",
                inputs=(InputPort("notes", STR, optional=True),),
                prompt=tpl(
                    "Write it.",
                    optional("\nAddress: ", ref_to("review", "notes", STR)),
                ),
                declared_outputs=(OutputPort("text", STR),),
            )
        )
        gate = p.add(
            approval_gate(
                node_id="review",
                prompt=tpl("Accept?\n", draft.ref("text")),
                inputs=(InputPort("text", STR),),
            )
        )
        done = p.add(succeed(node_id="done", reason="d"))
        p.set_entry(draft)
        p.connect(draft, "text", gate, "text")
        p.branch(gate, {"approved": done, "rejected": draft})
        p.feed(gate, "notes", draft, "notes")
        return p

    @staticmethod
    def _prompt(pipeline: Pipeline, node_id: str) -> str:
        agents = conductor.document(pipeline)["agents"]
        assert isinstance(agents, list)
        entry = next(a for a in agents if isinstance(a, dict) and a["name"] == node_id)
        prompt = entry["prompt"]
        assert isinstance(prompt, str)
        return prompt

    def test_a_deferred_reference_is_guarded(self) -> None:
        assert "{% if review is defined %}" in self._prompt(self._loop(), "draft")

    def test_the_gates_fixed_output_shape_is_resolved(self) -> None:
        """A forward reference still learns that a gate's text lives under additional_input."""
        assert "review.output.additional_input.notes" in self._prompt(self._loop(), "draft")

    def test_a_reference_that_always_precedes_is_not_guarded(self) -> None:
        assert "is defined" not in self._prompt(self._loop(), "review")

    def test_the_guard_is_not_emitted_twice(self) -> None:
        """The block guards it; the reference inside must not guard it again."""
        assert self._prompt(self._loop(), "draft").count("{% if review is defined %}") == 1

    def test_the_lint_no_longer_asks_the_author_for_a_guard(self) -> None:
        """With typed refs the old "guard this yourself" rule has nothing to say."""
        assert lint_pipeline(self._loop(), backend=conductor) == []
