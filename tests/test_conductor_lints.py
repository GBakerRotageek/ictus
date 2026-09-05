"""Lints that are true because of how Conductor runs.

Every graph below passes ``conductor validate``. That is the whole point: these
are the failures the engine's own validator cannot see. They are not reported
without a backend, because none of them is a claim about graphs in general.
"""

from __future__ import annotations

from ictus import END, AgentNode, InputPort, OutputPort, Pipeline, PortType
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline
from ictus.stdlib import approval_gate, succeed

S = PortType.STRING


def _problems(pipeline: Pipeline, fragment: str) -> list[str]:
    return [p for p in lint_pipeline(pipeline, backend=conductor) if fragment in p]


def test_a_backendless_lint_reports_none_of_these() -> None:
    """The boundary itself: engine rules arrive with an engine, not by default."""
    p = Pipeline(pipeline_id="t")
    node = p.add(AgentNode(node_id="a", prompt="{{ workflow.input.nope }}"))
    p.route(node, END)
    assert lint_pipeline(p) == []
    assert lint_pipeline(p, backend=conductor)


def test_unknown_output_field_in_a_template_is_reported() -> None:
    """Conductor checks the agent segment of a reference, never the field segment."""
    p = Pipeline(pipeline_id="t")
    src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("real", S),)))
    dst = p.add(
        AgentNode(node_id="dst", inputs=(InputPort("v", S),), prompt="{{ src.output.typo }}")
    )
    p.connect(src, "real", dst, "v")
    p.route(dst, END)
    assert _problems(p, "src.output.typo")


def test_known_output_field_passes() -> None:
    p = Pipeline(pipeline_id="t")
    src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("real", S),)))
    dst = p.add(
        AgentNode(node_id="dst", inputs=(InputPort("v", S),), prompt="{{ src.output.real }}")
    )
    p.connect(src, "real", dst, "v")
    p.route(dst, END)
    assert lint_pipeline(p, backend=conductor) == []


def test_undeclared_workflow_input_in_a_template_is_reported() -> None:
    p = Pipeline(pipeline_id="t")
    node = p.add(AgentNode(node_id="a", prompt="{{ workflow.input.nope }}"))
    p.route(node, END)
    assert _problems(p, "references workflow input 'nope'")


def test_reserved_output_name_is_reported() -> None:
    """A field named for Conductor's wrapper reads back empty at run time."""
    p = Pipeline(pipeline_id="t")
    node = p.add(AgentNode(node_id="a", prompt="x", declared_outputs=(OutputPort("outputs", S),)))
    p.route(node, END)
    assert _problems(p, "collides with Conductor's output wrapper")


class TestDeferredReferences:
    """A reference to a node that may not have run yet must be guarded.

    The ``?`` suffix makes the *dependency* optional. It does not make the Jinja
    variable defined, and Conductor renders with strict undefined — so the first
    pass through a loop dies on ``'<node>' is undefined``. This passes
    ``conductor validate``; it only shows up in a live run.
    """

    @staticmethod
    def _loop(prompt: str) -> Pipeline:
        p = Pipeline(pipeline_id="t", loop_passes=2)
        draft = p.add(
            AgentNode(
                node_id="draft",
                inputs=(InputPort("notes", S, optional=True),),
                prompt=prompt,
                declared_outputs=(OutputPort("text", S),),
            )
        )
        gate = p.add(approval_gate(node_id="review", prompt="ok?", inputs=(InputPort("text", S),)))
        done = p.add(succeed(node_id="done", reason="d"))
        p.set_entry(draft)
        p.connect(draft, "text", gate, "text")
        p.branch(gate, {"approved": done, "rejected": draft})
        p.feed(gate, "notes", draft, "notes")
        return p

    def test_unguarded_back_edge_reference_is_reported(self) -> None:
        p = self._loop("Revise using {{ review.output.additional_input.notes }}")
        assert _problems(p, "may not have run yet")

    def test_guarded_reference_is_accepted(self) -> None:
        p = self._loop(
            "Write it."
            "{% if review is defined %}"
            "Revise using {{ review.output.additional_input.notes }}"
            "{% endif %}"
        )
        assert lint_pipeline(p, backend=conductor) == []

    def test_the_gate_output_shape_is_understood(self) -> None:
        """``additional_input`` is fixed by Conductor, not declared by the author."""
        p = self._loop(
            "{% if review is defined %}{{ review.output.additional_input.notes }}{% endif %}"
        )
        assert lint_pipeline(p, backend=conductor) == []

    def test_a_reference_that_always_precedes_needs_no_guard(self) -> None:
        p = Pipeline(pipeline_id="t")
        src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("v", S),)))
        dst = p.add(
            AgentNode(node_id="dst", inputs=(InputPort("v", S),), prompt="{{ src.output.v }}")
        )
        p.connect(src, "v", dst, "v")
        p.route(dst, END)
        assert lint_pipeline(p, backend=conductor) == []


class TestUndeclaredReferences:
    """Under ``context.mode: explicit`` a node sees only what its ``input:`` names.

    This one cost a live run: a gate's terminal step referenced the gate without
    declaring it, and failed with ``'review' is undefined`` *after* the human had
    already answered. Conductor cannot catch it — the reference is well-formed
    and the agent exists.
    """

    @staticmethod
    def _referencing(*, declare: bool) -> Pipeline:
        p = Pipeline(pipeline_id="t")
        src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("v", S),)))
        end = p.add(
            succeed(
                node_id="fin",
                reason="got {{ src.output.v }}",
                inputs=(InputPort("v", S),) if declare else (),
            )
        )
        p.route(src, end)
        if declare:
            p.feed(src, "v", end, "v")
        return p

    def test_an_undeclared_reference_is_reported(self) -> None:
        assert _problems(self._referencing(declare=False), "does not declare it as an input")

    def test_declaring_it_clears_the_problem(self) -> None:
        assert lint_pipeline(self._referencing(declare=True), backend=conductor) == []

    def test_accumulate_mode_is_not_flagged(self) -> None:
        """The rule is a consequence of explicit scoping, not of graphs."""
        p = Pipeline(pipeline_id="t", context_mode="accumulate")
        src = p.add(AgentNode(node_id="src", prompt="x", declared_outputs=(OutputPort("v", S),)))
        end = p.add(succeed(node_id="fin", reason="got {{ src.output.v }}"))
        p.route(src, end)
        assert lint_pipeline(p, backend=conductor) == []

    def test_a_group_member_may_be_named_through_its_group(self) -> None:
        p = Pipeline(pipeline_id="t")
        a = p.add(AgentNode(node_id="a", prompt="x", declared_outputs=(OutputPort("ok", S),)))
        b = p.add(AgentNode(node_id="b", prompt="x", declared_outputs=(OutputPort("ok", S),)))
        group = p.parallel("both", [a, b])
        reader = p.add(
            AgentNode(
                node_id="reader",
                inputs=(InputPort("v", S),),
                prompt="{{ both.outputs.a.ok }}",
                declared_outputs=(OutputPort("seen", S),),
            )
        )
        done = p.add(succeed(node_id="done", reason="d"))
        p.set_entry(group)
        p.route(group, reader)
        p.feed(a, "ok", reader, "v")
        p.route(reader, done)
        assert lint_pipeline(p, backend=conductor) == []
