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
    ComputeNode,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    UnknownPortError,
    at_least,
    equals,
    every,
    not_equals,
    not_every,
    optional,
    ref_to,
    tpl,
)
from ictus.errors import CompositionError
from ictus.graph.mapping import Item
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline
from ictus.stdlib import approval_gate, ask_human_for, succeed

if TYPE_CHECKING:
    from collections.abc import Callable

    from conftest import Executes

    from ictus.graph.ref import Ref, Template
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

    @pytest.mark.parametrize("port_type", [STR, NUM, PortType.ARRAY, PortType.OBJECT])
    def test_a_conjunction_over_something_that_is_not_a_boolean_is_refused(
        self, port_type: PortType
    ) -> None:
        """Jinja calls a non-empty string true, so the condition holds on every run.

        That is the failure a route exists to prevent, arriving as a branch
        nobody took — the same reason `equals` refuses a mistyped literal.
        """
        ref = _producer(port_type=port_type).ref("v")
        with pytest.raises(CompositionError, match=r"every\(\) tests booleans"):
            every(ref)
        with pytest.raises(CompositionError, match=r"not_every\(\) tests booleans"):
            not_every(ref)

    def test_a_conjunction_names_the_member_that_is_not_a_boolean(self) -> None:
        """A council conjunction is built from a list; the message has to say which."""
        refs = (_producer("a", BOOL).ref("v"), _producer("b", STR).ref("v"))
        with pytest.raises(CompositionError, match=r"b\.v is string"):
            every(*refs)


class TestComparingAgainstNonStrings:
    """`equals` quotes its value, and a quote against anything but a string is
    silently false.

    A route condition is evaluated against the value the engine stored, not its
    rendered text, so the type is real on that side. The mismatch is well-formed,
    never true, and sends every run down the catch-all — a branch nobody took
    rather than an error anybody saw. A script step's `exit_code` is where it
    bites hardest: the one number a graph routinely routes on.
    """

    @staticmethod
    def _ref(port_type: PortType) -> Ref:
        p = Pipeline(pipeline_id="n")
        node = p.add(
            AgentNode(node_id="n", prompt="x", declared_outputs=(OutputPort("v", port_type),))
        )
        return node.ref("v")

    @staticmethod
    def _render(port_type: PortType, build: Callable[[Ref], object]) -> str:
        """The condition has to be built from *this* graph's node, or the
        compiler reads it as a forward reference and wraps it in a guard."""
        p = Pipeline(pipeline_id="r")
        node = p.add(
            AgentNode(node_id="n", prompt="x", declared_outputs=(OutputPort("v", port_type),))
        )
        done = p.add(succeed(node_id="done", reason="d"))
        other = p.add(succeed(node_id="other", reason="o"))
        p.set_entry(node)
        p.route(node, done, when=build(node.ref("v")))  # type: ignore[arg-type]
        p.route(node, other)
        agents = conductor.document(p)["agents"]
        assert isinstance(agents, list)
        entry = next(a for a in agents if isinstance(a, dict) and a["name"] == "n")
        routes = entry["routes"]
        assert isinstance(routes, list)
        first = routes[0]
        assert isinstance(first, dict)
        return str(first["when"])

    def test_an_int_coerces_before_comparing(self) -> None:
        assert self._render(NUM, lambda r: equals(r, 0)) == "{{ n.output.v | int == 0 }}"

    def test_a_negated_int_comparison_uses_the_same_coercion(self) -> None:
        assert self._render(NUM, lambda r: not_equals(r, 0)) == "{{ n.output.v | int != 0 }}"

    def test_a_bool_renders_jinja_s_bare_literal(self) -> None:
        """Quoted, it would compare against the string 'False' and never match."""
        assert self._render(BOOL, lambda r: equals(r, False)) == "{{ n.output.v == false }}"

    def test_a_negated_bool_comparison_uses_the_same_literal(self) -> None:
        assert self._render(BOOL, lambda r: not_equals(r, True)) == "{{ n.output.v != true }}"

    def test_a_string_still_renders_quoted(self) -> None:
        assert self._render(STR, lambda r: equals(r, "ship")) == "{{ n.output.v == 'ship' }}"

    @pytest.mark.parametrize(
        ("port_type", "value"),
        [
            (NUM, "0"),
            (STR, 0),
            (BOOL, "true"),
            (BOOL, "True"),
            (BOOL, 1),
            (NUM, True),
            (STR, True),
        ],
    )
    def test_a_value_of_the_wrong_type_is_refused(self, port_type: PortType, value: object) -> None:
        """`bool` is a subclass of `int`, so `True` against a number port is the
        one a naive isinstance check lets through."""
        with pytest.raises(CompositionError, match="never true"):
            equals(self._ref(port_type), value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("port_type", [PortType.ARRAY, PortType.OBJECT])
    def test_a_container_cannot_be_compared_at_all(self, port_type: PortType) -> None:
        """A list never equals a scalar literal; the test is false on every run."""
        with pytest.raises(CompositionError, match="cannot compare"):
            equals(self._ref(port_type), "x")

    def test_the_error_shows_what_the_condition_would_have_become(self) -> None:
        """An error a reader can check beats one they have to trust."""
        with pytest.raises(CompositionError, match=r"renders `== 'true'`"):
            equals(self._ref(BOOL), "true")


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


class TestSettledStructures:
    """A value Conductor reads back with ``json.loads`` has to be rendered as JSON.

    ``| tojson`` is what makes that round trip lossless. A fallback used to opt
    the reference out of it, so the branch that had a value emitted a Python
    repr — single quotes, parse fails, and what survives is a string that looks
    like data.
    """

    @staticmethod
    def _pipeline(*, fallback: bool) -> Pipeline:
        p = Pipeline(pipeline_id="carry")
        gate = p.add(approval_gate(node_id="ask", prompt="Search?"))
        search = p.add(
            AgentNode(
                node_id="search",
                prompt="find things",
                declared_outputs=(OutputPort("hits", PortType.ARRAY),),
            )
        )
        hits = search.ref("hits")
        done = p.add(
            succeed(
                node_id="done",
                reason="done",
                inputs=(InputPort("hits", PortType.ARRAY, optional=True),),
                result={"hits": tpl(hits.or_else("[]") if fallback else hits)},
            )
        )
        p.set_entry(gate)
        p.branch(gate, {"approved": search, "rejected": done})
        p.route(search, done)
        p.feed(search, "hits", done, "hits")
        return p

    def _template(self, *, fallback: bool) -> YamlDict:
        agents = conductor.document(self._pipeline(fallback=fallback))["agents"]
        assert isinstance(agents, list)
        done = next(a for a in agents if isinstance(a, dict) and a["name"] == "done")
        template = done["output_template"]
        assert isinstance(template, dict)
        return template

    def test_a_lone_structured_reference_round_trips(self) -> None:
        assert self._template(fallback=False)["hits"] == (
            "{% if search is defined %}{{ search.output.hits | tojson }}{% endif %}"
        )

    def test_a_fallback_does_not_opt_the_value_out_of_json(self) -> None:
        """The guard, not `| default()`: the attribute chain raises before a filter runs."""
        assert self._template(fallback=True)["hits"] == (
            "{% if search is defined %}{{ search.output.hits | tojson }}{% else %}[]{% endif %}"
        )

    def test_it_is_clean_and_loads(self, validates: Callable[[Pipeline], None]) -> None:
        pipeline = self._pipeline(fallback=True)
        assert lint_pipeline(pipeline, backend=conductor) == []
        validates(pipeline)


class TestTerminalResults:
    """``result`` typed the reference out: only a hand-written "{{ ... }}" fitted.

    That is the one spelling no reference lint can see — ``settled_refs`` walks
    ``Template``s — so the demos taught the form the README's opening claim
    forbids, because it was the only one that type-checked.
    """

    @staticmethod
    def _pipeline(value: Ref) -> Pipeline:
        p = Pipeline(pipeline_id="t")
        work = p.add(_producer("work"))
        done = p.add(
            succeed(
                node_id="done",
                reason="ok",
                inputs=(InputPort("v", STR),),
                result={"v": value},
            )
        )
        p.set_entry(work)
        p.route(work, done)
        p.feed(work, "v", done, "v")
        return p

    def test_a_result_takes_a_reference(self) -> None:
        p = self._pipeline(_producer("work").ref("v"))
        agents = conductor.document(p)["agents"]
        assert isinstance(agents, list)
        done = next(a for a in agents if isinstance(a, dict) and a["name"] == "done")
        template = done["output_template"]
        assert isinstance(template, dict)
        assert template["v"] == "{{ work.output.v | tojson }}"

    def test_a_reference_in_a_result_is_checked_like_any_other(self) -> None:
        p = self._pipeline(ref_to("ghost", "v", STR))
        assert any("unknown node 'ghost'" in problem for problem in lint_pipeline(p))


class TestAReferenceResolvesToWhatItWasBuiltFrom:
    """A reference carries its source. Sharing a name is not being the same thing.

    ``Ref.source`` was always kept "so a lint can check the referenced node is
    actually in the pipeline". Nothing checked it: resolution was by ``node_id``
    alone, so a reference built from another graph's node landed on whichever
    local node happened to share the name — and the emitted template then read
    that one. Every layer agreed, and the value was wrong.
    """

    @staticmethod
    def _local() -> tuple[Pipeline, AgentNode]:
        p = Pipeline(pipeline_id="t")
        work = p.add(_producer("work"))
        p.set_entry(work)
        return p, work

    @staticmethod
    def _read(p: Pipeline, local: AgentNode, value: Ref) -> Pipeline:
        reader = p.add(
            AgentNode(
                node_id="reader",
                inputs=(InputPort("v", STR),),
                prompt=tpl("see ", value),
                declared_outputs=(OutputPort("summary", STR),),
            )
        )
        p.connect(local, "v", reader, "v")
        p.route(reader, END)
        return p

    @staticmethod
    def _foreign_node() -> AgentNode:
        other = Pipeline(pipeline_id="other")
        node = other.add(_producer("work"))
        other.route(node, END)
        return node

    def test_a_foreign_node_shadowed_by_a_local_name_is_reported(self) -> None:
        p, local = self._local()
        self._read(p, local, self._foreign_node().ref("v"))
        assert [x for x in lint_pipeline(p, backend=conductor) if "a different node" in x]

    def test_the_local_node_of_that_name_still_reads_clean(self) -> None:
        """The check has to distinguish the two, not refuse the name."""
        p, local = self._local()
        self._read(p, local, local.ref("v"))
        assert lint_pipeline(p, backend=conductor) == []

    def test_a_forward_reference_is_still_resolved_by_name(self) -> None:
        """`ref_to` has no source to compare; naming the node is all it can do."""
        p, local = self._local()
        self._read(p, local, ref_to("work", "v", STR))
        assert lint_pipeline(p, backend=conductor) == []

    def test_a_foreign_workflow_input_shadowed_by_a_local_one_is_reported(self) -> None:
        """Every stage declares `brief`, so this is the collision most likely to happen."""
        outer = Pipeline(pipeline_id="outer")
        theirs = outer.declare_input("brief", STR)
        inner = Pipeline(pipeline_id="inner")
        inner.declare_input("brief", STR)
        node = inner.add(AgentNode(node_id="a", prompt=tpl("see ", theirs.ref())))
        inner.route(node, END)
        assert [x for x in lint_pipeline(inner) if "a different pipeline input" in x]

    def test_a_foreign_map_group_shadowed_by_a_local_one_is_reported(self) -> None:
        """A group resolves by name too, and its aggregate ports are identical."""
        theirs = _fanout("first").maps[0]
        mine = _fanout("second")
        peek = mine.add(
            AgentNode(
                node_id="peek",
                inputs=(InputPort("n", NUM),),
                prompt=tpl("count ", theirs.ref("count")),
            )
        )
        mine.route(mine.maps[0], peek)
        mine.route(peek, END)
        assert [x for x in lint_pipeline(mine) if "a different map group" in x]


def _fanout(pipeline_id: str) -> Pipeline:
    """A pipeline whose one map group is always called `workers`."""
    shape = {"repo": STR}
    p = Pipeline(pipeline_id=pipeline_id)
    split = p.add(
        AgentNode(
            node_id="split",
            prompt="split it",
            declared_outputs=(OutputPort("pieces", PortType.ARRAY, "each", element=shape),),
        )
    )
    piece = Item(name="piece", fields=shape)
    body = p.add(
        AgentNode(
            node_id="work",
            prompt=tpl("do ", piece.ref("repo")),
            declared_outputs=(OutputPort("summary", STR),),
        )
    )
    p.set_entry(split)
    group = p.map_over("workers", source=split.ref("pieces"), item=piece, body=body, expect_items=4)
    p.route(split, group)
    return p


class TestAGroupsConditionsResolveLikeAStepsConditions:
    """A group routes like a step, so what its conditions read is checked like one.

    Only nodes' routes were resolved: `reference_problems` walks a node's
    outgoing edges, and a parallel or map group is not a node. So a group
    condition built from another graph's `flag`, beside a local `flag`, linted
    clean — and on a live run the group took the branch the *local* flag chose.
    """

    @staticmethod
    def _flag(p: Pipeline, value: str) -> ComputeNode:
        return p.add(
            ComputeNode(
                node_id="flag",
                value=value,
                value_type=BOOL,
                declared_outputs=(OutputPort("value", BOOL),),
            )
        )

    def _grouped(self, condition: Callable[[ComputeNode], Template]) -> Pipeline:
        """Local flag → a parallel group → a branch chosen by `condition`."""
        p = Pipeline(pipeline_id="grp", provider="claude-agent-sdk")
        flag = self._flag(p, "false")
        members = [
            p.add(
                ComputeNode(
                    node_id=name,
                    value="1",
                    value_type=NUM,
                    declared_outputs=(OutputPort("value", NUM),),
                )
            )
            for name in ("a", "b")
        ]
        group = p.parallel("both", members)
        p.set_entry(flag)
        p.route(flag, group)
        yes = p.add(succeed(node_id="yes", reason="the condition held"))
        no = p.add(succeed(node_id="no", reason="it did not"))
        p.route(group, yes, when=condition(flag))
        p.route(group, no)
        return p

    def test_a_foreign_node_in_a_parallel_groups_condition_is_reported(self) -> None:
        foreign = self._flag(Pipeline(pipeline_id="elsewhere"), "true")
        p = self._grouped(lambda _local: tpl(foreign.ref("value")))
        assert [x for x in lint_pipeline(p) if "group 'both'" in x and "a different node" in x]

    def test_a_foreign_node_in_a_map_groups_condition_is_reported(self) -> None:
        foreign = self._flag(Pipeline(pipeline_id="elsewhere"), "true")
        p = _fanout("t")
        done = p.add(succeed(node_id="done", reason="ok"))
        p.route(p.maps[0], done, when=tpl(foreign.ref("value")))
        p.route(p.maps[0], END)
        self._flag(p, "false")
        assert [x for x in lint_pipeline(p) if "group 'workers'" in x and "a different node" in x]

    def test_an_unknown_node_in_a_groups_condition_is_reported(self) -> None:
        p = self._grouped(lambda _local: tpl(ref_to("ghost", "value", BOOL)))
        assert [x for x in lint_pipeline(p) if "group 'both'" in x and "unknown node 'ghost'" in x]

    def test_a_loop_item_in_a_groups_condition_is_reported(self) -> None:
        """A group routes once, after every item; there is no item there to read."""
        piece = Item(name="piece", fields={"ready": BOOL})
        p = self._grouped(lambda _local: tpl(piece.ref("ready")))
        assert [x for x in lint_pipeline(p) if "group 'both'" in x and "loop item" in x]

    def test_the_local_node_in_a_groups_condition_is_clean(self) -> None:
        p = self._grouped(lambda local: tpl(local.ref("value")))
        assert lint_pipeline(p, backend=conductor) == []

    def test_the_engine_reads_whichever_node_the_name_lands_on(self, executes: Executes) -> None:
        """Why the lint is the only guard: nothing downstream can tell the two apart.

        The condition was built from a flag that says true. The emitted template
        names `flag`, the engine resolves that to the local flag, which says
        false, and the group takes the other branch without any error.
        """
        foreign = self._flag(Pipeline(pipeline_id="elsewhere"), "true")
        run = executes(self._grouped(lambda _local: tpl(foreign.ref("value"))))
        assert run.returncode == 0, run.stderr
        assert run.routes()[-1] == "no"


class TestAQuestionsSourceIsResolved:
    """`ask_human_for(source=...)` names a value like any reference and was never resolved.

    It is a dotted path the engine reads itself rather than a template, so it was
    in neither `prompt_refs` nor `settled_refs` — and nothing walked it.
    """

    @staticmethod
    def _asking(source: Ref) -> Pipeline:
        """A local `find` step, then a questions step reading ``source``."""
        p = Pipeline(pipeline_id="t")
        found = p.add(
            AgentNode(
                node_id="find",
                prompt="x",
                declared_outputs=(OutputPort("questions", PortType.ARRAY),),
            )
        )
        p.set_entry(found)
        ask = p.add(ask_human_for(node_id="ask", source=source))
        p.route(found, ask)
        p.route(ask, END)
        return p

    def test_a_foreign_source_shadowed_by_a_local_step_is_reported(self) -> None:
        other = Pipeline(pipeline_id="elsewhere")
        foreign = other.add(
            AgentNode(
                node_id="find",
                prompt="y",
                declared_outputs=(OutputPort("questions", PortType.ARRAY),),
            )
        )
        problems = lint_pipeline(self._asking(foreign.ref("questions")))
        assert [x for x in problems if "questions 'ask'" in x and "a different node" in x]

    def test_an_unknown_source_is_reported(self) -> None:
        problems = lint_pipeline(self._asking(ref_to("ghost", "questions", PortType.ARRAY)))
        assert [x for x in problems if "questions 'ask'" in x and "unknown node 'ghost'" in x]

    def test_a_local_source_is_clean(self) -> None:
        p = self._asking(ref_to("find", "questions", PortType.ARRAY))
        assert not [x for x in lint_pipeline(p) if "questions 'ask'" in x]
