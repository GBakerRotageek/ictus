"""Typed references.

A reference used to be text inside a prompt, recoverable only by regular
expression. These tests pin what changed: the port is checked where the
reference is written, the type travels with it, and a reference into a loop is
resolved against the finished graph rather than trusted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ictus import (
    END,
    AgentNode,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    UnknownPortError,
    at_least,
    equals,
    every,
    not_every,
    optional,
    ref_to,
    tpl,
)
from ictus.errors import CompositionError
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline
from ictus.stdlib import approval_gate, succeed

if TYPE_CHECKING:
    from ictus.graph.values import YamlDict

STR, NUM, BOOL = PortType.STRING, PortType.NUMBER, PortType.BOOLEAN


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

    GATE_GUARD = (
        "review is defined"
        " and review.output.additional_input is defined"
        " and review.output.additional_input.notes is defined"
    )

    def test_a_deferred_reference_is_guarded(self) -> None:
        assert "{% if " + self.GATE_GUARD + " %}" in self._prompt(self._loop(), "draft")

    def test_a_gates_free_text_field_is_guarded_segment_by_segment(self) -> None:
        """Guarding only the node name is not enough, and it fails a round late.

        A gate's free-text field exists only on the branch that asked for one.
        Verified against the engine's own Jinja settings: with just
        ``review is defined``, the approve branch renders
        "'dict object' has no attribute 'notes'" — and `| default()` cannot
        rescue it, because strict undefined raises on the attribute access
        before the filter runs.
        """
        prompt = self._prompt(self._loop(), "draft")
        assert "review.output.additional_input is defined" in prompt
        assert "review.output.additional_input.notes is defined" in prompt

    def test_an_ordinary_output_is_guarded_only_at_the_root(self) -> None:
        """A step that ran has its declared outputs; the extra tests would be noise."""
        p = Pipeline(pipeline_id="t2", loop_passes=2)
        first = p.add(
            AgentNode(
                node_id="first",
                inputs=(InputPort("prev", STR, optional=True),),
                prompt=tpl("go", optional(" after ", ref_to("second", "text", STR))),
                declared_outputs=(OutputPort("text", STR),),
            )
        )
        second = p.add(
            AgentNode(node_id="second", prompt="again", declared_outputs=(OutputPort("text", STR),))
        )
        done = p.add(succeed(node_id="done", reason="d"))
        p.set_entry(first)
        p.route(first, second)
        p.route(second, done, when=tpl("{{ true }}"))
        p.route(second, first)
        p.feed(second, "text", first, "prev")
        assert "{% if second is defined %}" in self._prompt(p, "first")

    def test_the_gates_fixed_output_shape_is_resolved(self) -> None:
        """A forward reference still learns that a gate's text lives under additional_input."""
        assert "review.output.additional_input.notes" in self._prompt(self._loop(), "draft")

    def test_a_reference_that_always_precedes_is_not_guarded(self) -> None:
        assert "is defined" not in self._prompt(self._loop(), "review")

    def test_the_guard_is_not_emitted_twice(self) -> None:
        """The block guards it; the reference inside must not guard it again."""
        assert self._prompt(self._loop(), "draft").count("{% if " + self.GATE_GUARD + " %}") == 1
        assert self._prompt(self._loop(), "draft").count("{% if ") == 1

    def test_the_lint_no_longer_asks_the_author_for_a_guard(self) -> None:
        """With typed refs the old "guard this yourself" rule has nothing to say."""
        assert lint_pipeline(self._loop(), backend=conductor) == []


class TestTypedConditions:
    """Route conditions built from references rather than written as strings.

    A hand-written conjunction stops matching the thing it was derived from the
    moment that thing changes — which is exactly when nobody re-reads it.
    """

    @staticmethod
    def _pipeline() -> Pipeline:
        p = Pipeline(pipeline_id="cond", loop_passes=2)
        count = p.add(AgentNode(node_id="n", prompt="x", declared_outputs=(OutputPort("c", NUM),)))
        a = p.add(AgentNode(node_id="a", prompt="x", declared_outputs=(OutputPort("ok", BOOL),)))
        b = p.add(AgentNode(node_id="b", prompt="x", declared_outputs=(OutputPort("ok", BOOL),)))
        group = p.parallel("panel", [a, b])
        after = p.add(
            AgentNode(
                node_id="after",
                inputs=(
                    InputPort("c", NUM),
                    InputPort("a_ok", BOOL),
                    InputPort("b_ok", BOOL),
                ),
                prompt="decide",
                declared_outputs=(OutputPort("t", STR),),
            )
        )
        done = p.add(succeed(node_id="done", reason="d"))
        p.set_entry(count)
        p.route(count, group)
        p.route(group, after)
        p.feed(count, "c", after, "c")
        p.feed(a, "ok", after, "a_ok")
        p.feed(b, "ok", after, "b_ok")
        p.route(after, done, when=every(a.ref("ok"), b.ref("ok")))
        p.route(after, done, when=at_least(count.ref("c"), 3))
        p.route(after, count, when=not_every(a.ref("ok"), b.ref("ok")))
        p.route(after, done)
        return p

    @staticmethod
    def _routes(pipeline: Pipeline) -> list[str]:
        agents = conductor.document(pipeline)["agents"]
        assert isinstance(agents, list)
        entry = next(a for a in agents if isinstance(a, dict) and a["name"] == "after")
        routes = entry["routes"]
        assert isinstance(routes, list)
        return [str(r["when"]) for r in routes if isinstance(r, dict) and "when" in r]

    def test_a_conjunction_resolves_members_through_their_group(self) -> None:
        """The member's own name is not bound in context; the direct form reads empty."""
        assert self._routes(self._pipeline())[0] == (
            "{{ panel.outputs.a.ok and panel.outputs.b.ok }}"
        )

    def test_a_threshold_coerces_before_comparing(self) -> None:
        """A rendered value arrives as whatever JSON made of it; str >= int is a TypeError."""
        assert self._routes(self._pipeline())[1] == "{{ n.output.c | int >= 3 }}"

    def test_a_negated_conjunction_negates_once(self) -> None:
        assert self._routes(self._pipeline())[2] == (
            "{{ not (panel.outputs.a.ok and panel.outputs.b.ok) }}"
        )

    def test_the_references_inside_are_visible_to_the_lints(self) -> None:
        """Invisible to `refs()`, a condition's reads would never be checked or wired."""
        assert lint_pipeline(self._pipeline(), backend=conductor) == []

    def test_a_threshold_on_something_that_is_not_a_number_is_refused(self) -> None:
        p = Pipeline(pipeline_id="c2")
        n = p.add(AgentNode(node_id="n", prompt="x", declared_outputs=(OutputPort("s", STR),)))
        with pytest.raises(CompositionError, match="compares numbers"):
            at_least(n.ref("s"), 2)

    def test_an_empty_conjunction_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="at least one reference"):
            every()


class TestConditionalFieldsThatAlwaysRan:
    """A field can be absent even when the step that owns it certainly ran.

    The earlier guard asked only "might this step not have run yet?". A gate
    every path crosses is never deferred, so its free-text answer was emitted
    bare — and the branch where nobody typed anything died *after* the human had
    already answered. Both halves matter: the template guard, and the `?` on the
    `input:` entry, because a required entry raises at context build before any
    template runs.
    """

    @staticmethod
    def _pipeline() -> Pipeline:
        p = Pipeline(pipeline_id="always")
        start = p.add(
            AgentNode(node_id="draft", prompt="write", declared_outputs=(OutputPort("text", STR),))
        )
        gate = p.add(
            approval_gate(
                node_id="review",
                prompt=tpl("Accept?\n", start.ref("text")),
                inputs=(InputPort("text", STR),),
            )
        )
        # Both branches converge on one reader, so `review` always precedes it.
        ship = p.add(
            AgentNode(
                node_id="ship",
                inputs=(InputPort("notes", STR),),
                prompt=tpl("Ship it. Reviewer said: ", gate.ref("notes")),
                declared_outputs=(OutputPort("done", STR),),
            )
        )
        done = p.add(succeed(node_id="done", reason="d"))
        p.set_entry(start)
        p.connect(start, "text", gate, "text")
        p.branch(gate, {"approved": ship, "rejected": ship})
        p.feed(gate, "notes", ship, "notes")
        p.route(ship, done)
        return p

    @staticmethod
    def _agent(pipeline: Pipeline, name: str) -> YamlDict:
        agents = conductor.document(pipeline)["agents"]
        assert isinstance(agents, list)
        for candidate in agents:
            if isinstance(candidate, dict) and candidate.get("name") == name:
                return candidate
        raise AssertionError(name)

    def test_the_template_guards_it_even_though_the_gate_always_ran(self) -> None:
        prompt = self._agent(self._pipeline(), "ship")["prompt"]
        assert isinstance(prompt, str)
        assert "{% if review is defined" in prompt
        assert "review.output.additional_input.notes is defined" in prompt

    def test_the_input_entry_is_optional_so_context_build_does_not_raise(self) -> None:
        """A required entry raises KeyError before a template guard can help."""
        assert self._agent(self._pipeline(), "ship")["input"] == [
            "review.output.additional_input.notes?"
        ]

    def test_an_ordinary_output_of_a_step_that_always_ran_stays_unguarded(self) -> None:
        prompt = self._agent(self._pipeline(), "review")["prompt"]
        assert isinstance(prompt, str)
        assert "is defined" not in prompt


class TestConditionsAreFalseNotFatal:
    """A route condition reading a branch this run skipped must not kill the run.

    Conditions took the reference path directly and never asked whether the step
    behind it had run, so `equals(skipped.ref("x"), "y")` rendered bare and died
    under StrictUndefined. The only sensible reading of "that value is not there"
    is that the condition does not hold.
    """

    @staticmethod
    def _pipeline() -> Pipeline:
        p = Pipeline(pipeline_id="cond")
        triage = p.add(
            AgentNode(node_id="triage", prompt="x", declared_outputs=(OutputPort("big", BOOL),))
        )
        deep = p.add(
            AgentNode(node_id="deep", prompt="x", declared_outputs=(OutputPort("verdict", STR),))
        )
        judge = p.add(
            AgentNode(
                node_id="judge",
                inputs=(InputPort("v", STR, optional=True),),
                prompt="y",
                declared_outputs=(OutputPort("t", STR),),
            )
        )
        ok = p.add(succeed(node_id="ok", reason="d"))
        no = p.add(succeed(node_id="no", reason="d"))
        p.set_entry(triage)
        p.route(triage, deep, when=tpl(triage.ref("big")))
        p.route(triage, judge)  # the branch that skips `deep`
        p.route(deep, judge)
        p.feed(deep, "verdict", judge, "v")
        p.route(judge, ok, when=equals(deep.ref("verdict"), "ship"))
        p.route(judge, no)
        return p

    @staticmethod
    def _routes(pipeline: Pipeline, name: str) -> list[str]:
        agents = conductor.document(pipeline)["agents"]
        assert isinstance(agents, list)
        entry = next(a for a in agents if isinstance(a, dict) and a["name"] == name)
        routes = entry["routes"]
        assert isinstance(routes, list)
        return [str(r["when"]) for r in routes if isinstance(r, dict) and "when" in r]

    def test_a_condition_on_a_skipped_branch_short_circuits(self) -> None:
        assert self._routes(self._pipeline(), "judge") == [
            "{{ deep is defined and (deep.output.verdict == 'ship') }}"
        ]

    def test_a_condition_on_a_step_that_always_ran_is_left_alone(self) -> None:
        assert self._routes(self._pipeline(), "triage") == ["{{ triage.output.big }}"]

    def test_the_guard_goes_inside_the_braces_not_around_them(self) -> None:
        """`{% if %}` would render the empty string — falsy by accident, not design."""
        rendered = self._routes(self._pipeline(), "judge")[0]
        assert "{% if" not in rendered
        assert rendered.startswith("{{ ") and rendered.endswith(" }}")

    def test_it_is_lint_clean(self) -> None:
        assert lint_pipeline(self._pipeline(), backend=conductor) == []
