"""Running a whole stage once per item of a run-time array.

The engine could always do this — ``_execute_for_each_group`` builds a
sub-workflow's inputs from ``input_mapping`` rendered against a context the loop
variable has already been injected into (``engine/workflow.py``). What was
missing was the typed binding: a child's parameters are wired from graph edges,
and a loop item is not a node an edge can start from, so every iteration would
have received the parent's own inputs.

``bind`` is that binding. What these tests pin is that it is checked — every
name, every type, every required parameter — and that what reaches the child is
the item's value, at the item's type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ictus import (
    END,
    AgentNode,
    CompositionError,
    ComputeNode,
    EmitError,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    Ref,
    ScriptNode,
    Stage,
    ref_to,
    tpl,
)
from ictus.graph.mapping import Item
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline
from ictus.stdlib import approval_gate, ask_human_for, map_stage, succeed

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from conftest import Executes

    from ictus.graph.mapping import MapGroup
    from ictus.graph.node import Node, SubGraphNode
    from ictus.graph.values import YamlDict

STR, ARR, NUM, OBJ = PortType.STRING, PortType.ARRAY, PortType.NUMBER, PortType.OBJECT
PIECE = {"repo": STR, "task": STR, "priority": NUM}


def _review_stage(*, shared: bool = True) -> Stage:
    """A child taking one item's repo, plus a brief every iteration shares."""
    stage = Stage(stage_id="review")
    repo = stage.body.declare_input("repo", STR, description="Which repository")
    work = stage.body.add(
        AgentNode(
            node_id="work",
            inputs=(InputPort("repo", STR), *((InputPort("brief", STR),) if shared else ())),
            prompt=tpl("Review ", repo.ref()),
            declared_outputs=(OutputPort("report", STR, "What was found"),),
        )
    )
    stage.body.set_entry(work)
    stage.body.connect_input(repo, work, "repo")
    if shared:
        brief = stage.body.declare_input("brief", STR, description="What to look for")
        stage.body.connect_input(brief, work, "brief")
    stage.body.route(work, END)
    stage.body.expose_output("report", work, "report")
    return stage


def _parent(
    *,
    bind: Mapping[str, Ref] | None = None,
    item: Item | None = None,
    share: bool = True,
    stage: Stage | None = None,
) -> tuple[Pipeline, MapGroup, Item, SubGraphNode]:
    p = Pipeline(pipeline_id="fan", provider="claude-agent-sdk")
    brief = p.declare_input("brief", STR)
    split = p.add(
        AgentNode(
            node_id="split",
            inputs=(InputPort("brief", STR),),
            prompt=tpl("Split ", brief.ref()),
            declared_outputs=(OutputPort("pieces", ARR, "One per repo", element=PIECE),),
        )
    )
    p.set_entry(split)
    p.connect_input(brief, split, "brief")
    piece = item or Item(name="piece", fields=PIECE)
    host = (stage or _review_stage(shared=share)).instantiate(p, node_id="review")
    if share:
        p.connect_input(brief, host, "brief")
    group = p.map_over(
        "reviews",
        source=split.ref("pieces"),
        item=piece,
        body=host,
        expect_items=4,
        bind={"repo": piece.ref("repo")} if bind is None else bind,
    )
    p.route(split, group)
    done = p.add(
        succeed(
            node_id="done",
            reason="reviewed",
            inputs=(InputPort("reports", ARR),),
            result={"reports": tpl(group.ref("outputs"))},
        )
    )
    p.route(group, done)
    p.feed(group, "outputs", done, "reports")
    return p, group, piece, host


def _for_each(pipeline: Pipeline) -> YamlDict:
    groups = conductor.document(pipeline)["for_each"]
    assert isinstance(groups, list)
    entry = groups[0]
    assert isinstance(entry, dict)
    return entry


def _mapping(pipeline: Pipeline) -> YamlDict:
    agent = _for_each(pipeline)["agent"]
    assert isinstance(agent, dict)
    mapping = agent["input_mapping"]
    assert isinstance(mapping, dict)
    return mapping


class TestItIsWiredAndLoads:
    def test_a_bound_stage_is_clean_and_loads(self, validates: Callable[[Pipeline], None]) -> None:
        p, _, _, _ = _parent()
        assert lint_pipeline(p, backend=conductor) == []
        validates(p)

    def test_the_body_is_emitted_as_a_sub_workflow_call(self) -> None:
        agent = _for_each(_parent()[0])["agent"]
        assert isinstance(agent, dict)
        assert agent["type"] == "workflow"
        assert agent["workflow"] == "./review.yaml"

    def test_the_child_file_is_compiled_alongside_the_parent(self) -> None:
        assert [d.filename for d in conductor.compile(_parent()[0])] == [
            "fan.yaml",
            "review.yaml",
        ]

    def test_item_and_shared_values_arrive_in_one_mapping(self) -> None:
        """The two sources of a child's parameters end up in the same block."""
        assert _mapping(_parent()[0]) == {
            "brief": "{{ workflow.input.brief | tojson }}",
            "repo": "{{ piece.repo | tojson }}",
        }

    def test_the_shared_value_is_declared_as_an_input(self) -> None:
        """`input:` is what puts a prior step's value in scope for the mapping."""
        agent = _for_each(_parent()[0])["agent"]
        assert isinstance(agent, dict)
        assert agent["input"] == ["workflow.input.brief"]


class TestTypesSurviveTheBoundary:
    """Conductor json-parses each rendered mapping, so a bare string is retyped."""

    def test_every_bound_value_is_serialised_including_strings(self) -> None:
        """`{{ piece.repo }}` holding "false" reaches the child as a boolean."""
        for expression in _mapping(_parent()[0]).values():
            assert isinstance(expression, str)
            assert expression.endswith(" | tojson }}"), expression

    def test_a_number_field_is_serialised_too(self) -> None:
        piece = Item(name="piece", fields=PIECE)
        stage = Stage(stage_id="review")
        rank = stage.body.declare_input("priority", NUM)
        work = stage.body.add(
            AgentNode(
                node_id="work",
                inputs=(InputPort("priority", NUM),),
                prompt="rank it",
                declared_outputs=(OutputPort("report", STR),),
            )
        )
        stage.body.set_entry(work)
        stage.body.connect_input(rank, work, "priority")
        stage.body.route(work, END)
        stage.body.expose_output("report", work, "report")
        p, _, _, _ = _parent(
            bind={"priority": piece.ref("priority")}, item=piece, share=False, stage=stage
        )
        assert _mapping(p) == {"priority": "{{ piece.priority | tojson }}"}

    def test_the_collected_array_carries_the_childs_output_shape(self) -> None:
        """Without it nothing downstream can fan out over what the children returned."""
        _, group, _, _ = _parent()
        outputs = group.get_output("outputs")
        assert outputs.port_type is ARR
        assert outputs.element == {"report": STR}


class TestAnItemIsOneObjectNotAName:
    def test_a_ref_from_a_different_item_of_the_same_name_is_refused(self) -> None:
        other = Item(name="piece", fields=PIECE)
        with pytest.raises(CompositionError, match="a different loop item"):
            _parent(bind={"repo": other.ref("repo")})

    def test_a_body_reading_a_different_item_of_the_same_name_is_reported(self) -> None:
        other = Item(name="piece", fields=PIECE)
        p = Pipeline(pipeline_id="fan")
        split = p.add(
            AgentNode(
                node_id="split",
                prompt="split",
                declared_outputs=(OutputPort("pieces", ARR, "each", element=PIECE),),
            )
        )
        piece = Item(name="piece", fields=PIECE)
        body = p.add(AgentNode(node_id="work", prompt=tpl("do ", other.ref("repo"))))
        p.set_entry(split)
        group = p.map_over(
            "reviews", source=split.ref("pieces"), item=piece, body=body, expect_items=3
        )
        p.route(split, group)
        p.route(group, END)
        assert [x for x in lint_pipeline(p) if "a different loop item" in x]

    def test_an_item_reference_outside_any_map_is_reported(self) -> None:
        """Nothing injects a loop variable there; the template fails at run time."""
        piece = Item(name="piece", fields=PIECE)
        p = Pipeline(pipeline_id="t")
        node = p.add(AgentNode(node_id="a", prompt=tpl("do ", piece.ref("repo"))))
        p.route(node, END)
        assert [x for x in lint_pipeline(p) if "not the body of a map group" in x]


class TestTheBindingIsChecked:
    def test_an_unknown_child_parameter_is_refused(self) -> None:
        piece = Item(name="piece", fields=PIECE)
        with pytest.raises(CompositionError, match="does not declare"):
            _parent(bind={"nope": piece.ref("repo")}, item=piece)

    def test_a_type_mismatch_is_refused(self) -> None:
        piece = Item(name="piece", fields=PIECE)
        with pytest.raises(CompositionError, match=r"but .* declares"):
            _parent(bind={"repo": piece.ref("priority")}, item=piece)

    def test_a_required_parameter_that_nothing_supplies_is_reported(self) -> None:
        """Bound, shared, or missing — the third is the one that runs and fails."""
        p, _, _, _ = _parent(bind={}, share=False)
        problems = lint_pipeline(p)
        assert [x for x in problems if "repo" in x and "nothing supplies" in x]

    def test_an_optional_parameter_may_be_left_unsupplied(self) -> None:
        stage = Stage(stage_id="review")
        repo = stage.body.declare_input("repo", STR)
        note = stage.body.declare_input("note", STR, required=False)
        work = stage.body.add(
            AgentNode(
                node_id="work",
                inputs=(InputPort("repo", STR), InputPort("note", STR, optional=True)),
                prompt=tpl("Review ", repo.ref(), " ", note.ref()),
                declared_outputs=(OutputPort("report", STR),),
            )
        )
        stage.body.set_entry(work)
        stage.body.connect_input(repo, work, "repo")
        stage.body.connect_input(note, work, "note")
        stage.body.route(work, END)
        stage.body.expose_output("report", work, "report")
        p, _, _, _ = _parent(share=False, stage=stage)
        assert lint_pipeline(p, backend=conductor) == []

    def test_binding_a_parameter_an_edge_already_supplies_is_refused(self) -> None:
        """Two writers for one value, and the mapping can only hold one of them."""
        piece = Item(name="piece", fields=PIECE)
        with pytest.raises(CompositionError, match="already supplies"):
            _parent(bind={"repo": piece.ref("repo"), "brief": piece.ref("task")}, item=piece)

    def test_wiring_an_edge_to_a_parameter_bind_already_supplies_is_reported(self) -> None:
        """The other order: the edge arrives after the group was declared."""
        p, _, _, host = _parent()
        p.connect_input(p.workflow_inputs[0], host, "repo")
        assert [x for x in lint_pipeline(p) if "supplied twice" in x]

    def test_binding_a_body_that_is_not_a_stage_is_refused(self) -> None:
        p = Pipeline(pipeline_id="fan")
        split = p.add(
            AgentNode(
                node_id="split",
                prompt="split",
                declared_outputs=(OutputPort("pieces", ARR, "each", element=PIECE),),
            )
        )
        piece = Item(name="piece", fields=PIECE)
        body = p.add(AgentNode(node_id="work", prompt=tpl("do ", piece.ref("repo"))))
        p.set_entry(split)
        with pytest.raises(CompositionError, match="only a stage"):
            p.map_over(
                "reviews",
                source=split.ref("pieces"),
                item=piece,
                body=body,
                expect_items=3,
                bind={"repo": piece.ref("repo")},
            )


class TestAnEmptySourceArray:
    """Conductor returns an empty aggregate and skips the body entirely."""

    def test_the_aggregate_is_still_typed_and_read_as_json(self) -> None:
        """`[]` bare would reach the consumer as the string "[]"."""
        p, _, _, _ = _parent()
        agents = conductor.document(p)["agents"]
        assert isinstance(agents, list)
        done = next(a for a in agents if isinstance(a, dict) and a["name"] == "done")
        template = done["output_template"]
        assert isinstance(template, dict)
        assert template["reports"] == "{{ reviews.outputs | tojson }}"

    def test_the_child_is_compiled_even_though_it_may_never_run(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        p, _, _, _ = _parent()
        validates(p)


class TestTheConstructor:
    """`map_stage` places the stage and maps over it, and nothing else."""

    @staticmethod
    def _built() -> tuple[Pipeline, MapGroup]:
        p = Pipeline(pipeline_id="fan", provider="claude-agent-sdk")
        brief = p.declare_input("brief", STR)
        split = p.add(
            AgentNode(
                node_id="split",
                inputs=(InputPort("brief", STR),),
                prompt=tpl("Split ", brief.ref()),
                declared_outputs=(OutputPort("pieces", ARR, "one per repo", element=PIECE),),
            )
        )
        p.set_entry(split)
        p.connect_input(brief, split, "brief")
        piece = Item(name="piece", fields=PIECE)
        group = map_stage(
            p,
            group_id="reviews",
            stage=_review_stage(),
            source=split.ref("pieces"),
            item=piece,
            bind={"repo": piece.ref("repo")},
            expect_items=4,
        )
        # The shared half is wired on the placed stage, like any other placement.
        p.connect_input(brief, group.body, "brief")
        p.route(split, group)
        done = p.add(
            succeed(
                node_id="done",
                reason="reviewed",
                inputs=(InputPort("reports", ARR),),
                result={"reports": tpl(group.ref("outputs"))},
            )
        )
        p.route(group, done)
        p.feed(group, "outputs", done, "reports")
        return p, group

    def test_it_builds_the_same_graph_as_the_calls_it_makes(
        self, validates: Callable[[Pipeline], None]
    ) -> None:
        p, _ = self._built()
        assert lint_pipeline(p, backend=conductor) == []
        assert _mapping(p) == {
            "brief": "{{ workflow.input.brief | tojson }}",
            "repo": "{{ piece.repo | tojson }}",
        }
        validates(p)

    def test_the_group_hands_back_the_placed_stage_to_wire_shared_values_to(self) -> None:
        _, group = self._built()
        assert group.body.node_id == "review"


class TestAChainedMap:
    """A second fan-out over what the first collected.

    Two separate facts, and both are pinned. The graph must carry the collected
    element shape through a *reference*, not only on the port, or the second
    map cannot name a field. And Conductor cannot run the chain directly: its
    schema requires a for_each source of at least three dotted parts
    (``config/schema.py``, "minimum 3 parts"), and a map group's aggregate is
    addressed in two — ``reviews.outputs``. Verified by loading such a workflow,
    not inferred from the check.
    """

    @staticmethod
    def _chained() -> tuple[Pipeline, MapGroup]:
        p, first, _, _ = _parent()
        entry = Item(name="entry", fields={"report": STR})
        reader = p.add(
            AgentNode(
                node_id="reader",
                prompt=tpl("Summarise ", entry.ref("report")),
                declared_outputs=(OutputPort("summary", STR),),
            )
        )
        second = p.map_over(
            "summaries", source=first.ref("outputs"), item=entry, body=reader, expect_items=4
        )
        return p, second

    def test_a_reference_to_the_collected_array_carries_its_element_shape(self) -> None:
        _, first, _, _ = _parent()
        assert first.ref("outputs").element == {"report": STR}

    def test_a_second_map_can_read_a_field_of_what_the_first_collected(self) -> None:
        _, second = self._chained()
        assert second.item.fields == {"report": STR}

    def test_conductor_refuses_to_run_it_directly_and_says_so_before_emitting(self) -> None:
        p, _ = self._chained()
        assert any("three" in problem and "summaries" in problem for problem in conductor.lint(p))
        with pytest.raises(EmitError, match="summaries"):
            conductor.compile(p)


class TestTheSourceIsResolvedLikeAnyReference:
    """A map's source is a reference, and it resolves to one array — the right one.

    It escaped every check: `reference_problems` walks what a *node* reads, and a
    map group is not a node. So a foreign `split.pieces` declaring `repo`, placed
    beside a local `split.pieces` declaring something else, linted clean and
    compiled — against the local array, whose items have no `repo`.
    """

    @staticmethod
    def _split(p: Pipeline, element: Mapping[str, PortType]) -> AgentNode:
        return p.add(
            AgentNode(
                node_id="split",
                prompt="split",
                declared_outputs=(OutputPort("pieces", ARR, "each", element=element),),
            )
        )

    @staticmethod
    def _mapped(p: Pipeline, source: Ref, item: Item) -> MapGroup:
        reads = tuple(item.ref(name) for name in item.fields)
        body = p.add(
            AgentNode(
                node_id="work",
                prompt=tpl("do ", *reads),
                declared_outputs=(OutputPort("summary", STR),),
            )
        )
        return p.map_over("fan", source=source, item=item, body=body, expect_items=3)

    def _foreign(self) -> AgentNode:
        return self._split(Pipeline(pipeline_id="elsewhere"), {"repo": STR})

    @staticmethod
    def _finish(p: Pipeline, group: MapGroup) -> Pipeline:
        split = next(n for n in p.nodes if n.node_id == "split")
        p.set_entry(split)
        p.route(split, group)
        p.route(group, END)
        return p

    def test_a_foreign_source_shadowed_by_a_local_step_is_refused_where_written(self) -> None:
        p = Pipeline(pipeline_id="t")
        self._split(p, {"unrelated": STR})
        piece = Item("piece", {"repo": STR})
        with pytest.raises(CompositionError, match="a different node"):
            self._mapped(p, self._foreign().ref("pieces"), piece)

    def test_the_same_shadowing_is_reported_when_the_local_step_arrives_later(self) -> None:
        p = Pipeline(pipeline_id="t")
        piece = Item("piece", {"repo": STR})
        group = self._mapped(p, self._foreign().ref("pieces"), piece)
        self._split(p, {"unrelated": STR})
        problems = lint_pipeline(self._finish(p, group))
        assert [x for x in problems if "map group 'fan'" in x and "a different node" in x]

    def test_a_source_naming_no_step_is_reported(self) -> None:
        p = Pipeline(pipeline_id="t")
        self._split(p, {"repo": STR})
        group = self._mapped(p, ref_to("ghost", "pieces", ARR), Item("piece"))
        problems = lint_pipeline(self._finish(p, group))
        assert [x for x in problems if "map group 'fan'" in x and "unknown node 'ghost'" in x]

    def test_a_source_port_the_step_does_not_declare_is_reported(self) -> None:
        p = Pipeline(pipeline_id="t")
        self._split(p, {"repo": STR})
        group = self._mapped(p, ref_to("split", "chunks", ARR), Item("piece"))
        problems = lint_pipeline(self._finish(p, group))
        assert [x for x in problems if "map group 'fan'" in x and "split.chunks" in x]

    def test_a_source_the_step_declares_at_another_type_is_reported(self) -> None:
        p = Pipeline(pipeline_id="t")
        split = p.add(
            AgentNode(node_id="split", prompt="s", declared_outputs=(OutputPort("pieces", STR),))
        )
        group = self._mapped(p, ref_to("split", "pieces", ARR), Item("piece"))
        p.set_entry(split)
        p.route(split, group)
        p.route(group, END)
        assert [x for x in lint_pipeline(p) if "map group 'fan'" in x and "declared string" in x]

    def test_an_element_shape_the_step_does_not_produce_is_reported(self) -> None:
        """A hand-built reference can claim any shape; the item fields trust it."""
        p = Pipeline(pipeline_id="t")
        self._split(p, {"unrelated": STR})
        claimed = Ref(source_id="split", port="pieces", port_type=ARR, element={"repo": STR})
        group = self._mapped(p, claimed, Item("piece", {"repo": STR}))
        problems = lint_pipeline(self._finish(p, group))
        assert [x for x in problems if "map group 'fan'" in x and "element shape" in x]

    def test_a_foreign_workflow_input_as_source_is_refused_where_written(self) -> None:
        theirs = Pipeline(pipeline_id="outer").declare_input("pieces", ARR)
        p = Pipeline(pipeline_id="t")
        p.declare_input("pieces", ARR)
        body = p.add(AgentNode(node_id="work", prompt="do it"))
        with pytest.raises(CompositionError, match="a different pipeline input"):
            p.map_over("fan", source=theirs.ref(), item=Item("piece"), body=body, expect_items=3)

    def test_a_foreign_workflow_input_declared_here_later_is_reported(self) -> None:
        theirs = Pipeline(pipeline_id="outer").declare_input("pieces", ARR)
        p = Pipeline(pipeline_id="t")
        body = p.add(AgentNode(node_id="work", prompt="do it"))
        group = p.map_over(
            "fan", source=theirs.ref(), item=Item("piece"), body=body, expect_items=3
        )
        p.declare_input("pieces", ARR)
        p.set_entry(group)
        p.route(group, END)
        problems = lint_pipeline(p)
        assert [x for x in problems if "map group 'fan'" in x and "different pipeline input" in x]

    def test_a_field_of_an_item_cannot_be_a_source(self) -> None:
        """Nothing injects an item at the level a group is scheduled."""
        piece = Item("piece", {"tags": ARR})
        p = Pipeline(pipeline_id="t")
        body = p.add(AgentNode(node_id="work", prompt="do it"))
        with pytest.raises(CompositionError, match="loop item"):
            p.map_over("fan", source=piece.ref("tags"), item=Item("tag"), body=body, expect_items=3)

    def test_the_local_source_is_still_clean(self) -> None:
        p = Pipeline(pipeline_id="t")
        split = self._split(p, {"repo": STR})
        piece = Item("piece", {"repo": STR})
        group = self._mapped(p, split.ref("pieces"), piece)
        assert lint_pipeline(self._finish(p, group), backend=conductor) == []


# --- executed, not inspected -------------------------------------------------


def _echo_stage() -> Stage:
    """A child that reports exactly what it was handed, as one string.

    A `set` step, so the run calls no model. Both parameters go into one value
    because a `values:` block YAML-loads each binding — "false" would come back a
    boolean there for reasons that have nothing to do with the boundary under
    test — and a single `value:` with `output_type: string` does not.
    """
    stage = Stage(stage_id="echo")
    repo = stage.body.declare_input("repo", STR)
    brief = stage.body.declare_input("brief", STR)
    said = stage.body.add(
        ComputeNode(
            node_id="said",
            inputs=(InputPort("repo", STR), InputPort("brief", STR)),
            value="{{ workflow.input.brief }}:{{ workflow.input.repo }}",
            value_type=STR,
            declared_outputs=(OutputPort("value", STR),),
        )
    )
    stage.body.set_entry(said)
    stage.body.connect_input(repo, said, "repo")
    stage.body.connect_input(brief, said, "brief")
    stage.body.route(said, END)
    stage.body.expose_output("echoed", said, "value")
    return stage


def _echoing(pieces: str) -> Pipeline:
    p = Pipeline(pipeline_id="fan", provider="claude-agent-sdk")
    brief = p.declare_input("brief", STR)
    split = p.add(
        ScriptNode(
            node_id="split",
            command="/bin/echo",
            args=('{"pieces": ' + pieces + "}",),
            declared_outputs=(OutputPort("pieces", ARR, "each", element={"repo": STR}),),
        )
    )
    p.set_entry(split)
    piece = Item(name="piece", fields={"repo": STR})
    group = map_stage(
        p,
        group_id="echoes",
        stage=_echo_stage(),
        source=split.ref("pieces"),
        item=piece,
        bind={"repo": piece.ref("repo")},
        expect_items=4,
    )
    p.connect_input(brief, group.body, "brief")
    p.route(split, group)
    done = p.add(
        succeed(
            node_id="done",
            reason="echoed",
            inputs=(InputPort("echoes", ARR), InputPort("count", NUM)),
            result={"echoes": tpl(group.ref("outputs")), "count": tpl(group.ref("count"))},
        )
    )
    p.route(group, done)
    p.feed(group, "outputs", done, "echoes")
    p.feed(group, "count", done, "count")
    return p


class TestItRunsThroughTheEngine:
    def test_item_values_reach_each_child_as_the_strings_they_were(
        self, executes: Executes
    ) -> None:
        """Bare, "false" and "null" arrive as a boolean and None: `False`, `None`."""
        p = _echoing('[{"repo": "false"}, {"repo": "null"}, {"repo": "0700"}]')
        assert lint_pipeline(p, backend=conductor) == []
        run = executes(p, brief="shared")
        assert run.output is not None, run.stderr
        assert run.output["echoes"] == [
            {"echoed": "shared:false"},
            {"echoed": "shared:null"},
            {"echoed": "shared:0700"},
        ]
        assert run.output["count"] == 3

    def test_a_shared_value_reaches_every_iteration_beside_its_item(
        self, executes: Executes
    ) -> None:
        run = executes(_echoing('[{"repo": "a"}, {"repo": "b"}]'), brief="true")
        assert run.output is not None, run.stderr
        assert run.output["echoes"] == [{"echoed": "true:a"}, {"echoed": "true:b"}]

    def test_an_empty_source_array_runs_no_child_and_collects_nothing(
        self, executes: Executes
    ) -> None:
        run = executes(_echoing("[]"), brief="unused")
        assert run.output is not None, run.stderr
        assert run.output["echoes"] == []
        assert run.output["count"] == 0
        assert not [e for e in run.events if e.get("type") == "subworkflow_started"]


class TestTheCollectedShapeIsTheBodysResult:
    """The element a map group advertises is what each item's result actually is.

    Derived from the body's *ports* it was wrong for the one kind whose port is
    not a key: a single-value `set` step stores the bare value, so a group of
    them collects `[1, 2, 3]`, not `[{"value": 1}, ...]`. Advertised as the
    latter, a map downstream — through a stage, where nothing else looks —
    passed lint and Conductor's validation and then failed on its first item
    with "'int object' has no attribute 'value'".
    """

    @staticmethod
    def _counting(body: Node, *, pieces: str = "[1, 2, 3]") -> tuple[Pipeline, MapGroup]:
        p = Pipeline(pipeline_id="tally", provider="claude-agent-sdk")
        split = p.add(
            ScriptNode(
                node_id="split",
                command="/bin/echo",
                args=('{"pieces": ' + pieces + "}",),
                declared_outputs=(OutputPort("pieces", ARR, "numbers"),),
            )
        )
        p.set_entry(split)
        p.add(body)
        group = p.map_over(
            "counted", source=split.ref("pieces"), item=Item("piece"), body=body, expect_items=3
        )
        p.route(split, group)
        done = p.add(
            succeed(
                node_id="done",
                reason="counted",
                inputs=(InputPort("collected", ARR),),
                result={"collected": tpl(group.ref("outputs"))},
            )
        )
        p.route(group, done)
        p.feed(group, "outputs", done, "collected")
        return p, group

    @staticmethod
    def _bare() -> ComputeNode:
        return ComputeNode(
            node_id="each",
            value="{{ piece }}",
            value_type=NUM,
            declared_outputs=(OutputPort("value", NUM),),
        )

    @staticmethod
    def _keyed() -> ComputeNode:
        return ComputeNode(
            node_id="each",
            values={"doubled": "{{ piece * 2 }}"},
            declared_outputs=(OutputPort("doubled", NUM),),
        )

    def test_a_single_value_body_collects_bare_values_with_no_fields(self) -> None:
        _, group = self._counting(self._bare())
        assert group.get_output("outputs").element is None
        assert group.ref("outputs").element is None

    def test_a_keyed_body_collects_objects_with_its_keys(self) -> None:
        _, group = self._counting(self._keyed())
        assert group.get_output("outputs").element == {"doubled": NUM}

    def test_a_port_the_result_nests_is_not_advertised_as_a_top_level_field(self) -> None:
        """A gate keeps free text under `additional_input`, not beside `selected`."""
        p = Pipeline(pipeline_id="t")
        src = p.add(AgentNode(node_id="s", prompt="x", declared_outputs=(OutputPort("v", ARR),)))
        gate = p.add(approval_gate(node_id="review", prompt="ok?"))
        group = p.map_over("g", source=src.ref("v"), item=Item("piece"), body=gate, expect_items=2)
        element = group.get_output("outputs").element
        assert element is not None
        assert "selected" in element
        assert "notes" not in element

    def test_the_engine_collects_exactly_those_shapes(self, executes: Executes) -> None:
        """What the two tests above claim, checked against a run of each."""
        bare = executes(self._counting(self._bare())[0])
        assert bare.output is not None, bare.stderr
        assert bare.output["collected"] == [1, 2, 3]
        keyed = executes(self._counting(self._keyed())[0])
        assert keyed.output is not None, keyed.stderr
        assert keyed.output["collected"] == [{"doubled": 2}, {"doubled": 4}, {"doubled": 6}]

    def test_a_map_reading_fields_off_bare_values_through_a_stage_is_refused(self) -> None:
        """The reported failure: it used to compose, lint, validate — and die per item."""
        stage = Stage(stage_id="inner")
        split = stage.body.add(
            ScriptNode(
                node_id="split",
                command="/bin/echo",
                args=('{"pieces": [1, 2, 3]}',),
                declared_outputs=(OutputPort("pieces", ARR, "numbers"),),
            )
        )
        stage.body.set_entry(split)
        each = stage.body.add(self._bare())
        counted = stage.body.map_over(
            "counted", source=split.ref("pieces"), item=Item("piece"), body=each, expect_items=3
        )
        stage.body.route(split, counted)
        stage.body.route(counted, END)
        stage.body.expose_output("collected", counted, "outputs")

        parent = Pipeline(pipeline_id="outer")
        host = stage.instantiate(parent)
        parent.set_entry(host)
        entry = Item("entry", {"value": NUM})
        reader = parent.add(AgentNode(node_id="reader", prompt=tpl("read ", entry.ref("value"))))
        with pytest.raises(CompositionError, match="declares no element shape"):
            parent.map_over(
                "reads", source=host.ref("collected"), item=entry, body=reader, expect_items=3
            )


class TestAnInputAndAMapMayShareAName:
    """`workflow.input.items` and a group called `items` are two addresses, not one.

    The resolver looked names up in the map table before asking what *kind* of
    reference it held, so an input named like a group resolved to the group and
    failed its identity check — a false error on a workflow that validates and
    runs.
    """

    @staticmethod
    def _shared_name() -> Pipeline:
        p = Pipeline(pipeline_id="named", provider="claude-agent-sdk")
        items = p.declare_input("items", ARR)
        each = p.add(
            ComputeNode(
                node_id="each",
                value="{{ piece }}",
                value_type=STR,
                declared_outputs=(OutputPort("value", STR),),
            )
        )
        group = p.map_over(
            "items", source=items.ref(), item=Item("piece"), body=each, expect_items=3
        )
        p.set_entry(group)
        done = p.add(
            succeed(
                node_id="done",
                reason="read",
                inputs=(InputPort("seen", ARR), InputPort("given", ARR)),
                result={"seen": tpl(group.ref("outputs")), "given": tpl(items.ref())},
            )
        )
        p.route(group, done)
        p.feed(group, "outputs", done, "seen")
        p.connect_input(items, done, "given")
        return p

    def test_the_source_resolves_to_the_input_not_the_group(self) -> None:
        assert lint_pipeline(self._shared_name(), backend=conductor) == []

    def test_it_loads_and_runs(
        self, validates: Callable[[Pipeline], None], executes: Executes
    ) -> None:
        p = self._shared_name()
        validates(p)
        run = executes(p, items=["a", "b"])
        assert run.output is not None, run.stderr
        assert run.output["seen"] == ["a", "b"]
        assert run.output["given"] == ["a", "b"]


class TestAMapBodyHasNoOutputOfItsOwn:
    """A map body runs once per item and only its group's aggregate is stored.

    `feed` and `connect` already refused to read one. A *reference* was never
    checked, so a second map over `each.items` — `each` being the first map's
    body — linted clean, passed Conductor's validation, and died on the run
    with "Agent 'each' output not found for source 'each.output.items'".
    """

    @staticmethod
    def _each(p: Pipeline) -> ComputeNode:
        return p.add(
            ComputeNode(
                node_id="each",
                values={"items": "[{{ piece }}]"},
                declared_outputs=(OutputPort("items", ARR),),
            )
        )

    @staticmethod
    def _split(p: Pipeline) -> ScriptNode:
        return p.add(
            ScriptNode(
                node_id="split",
                command="/bin/echo",
                args=('{"pieces": [1, 2]}',),
                declared_outputs=(OutputPort("pieces", ARR),),
            )
        )

    @staticmethod
    def _again(p: Pipeline) -> ComputeNode:
        return p.add(
            ComputeNode(
                node_id="again",
                value="{{ x }}",
                value_type=NUM,
                declared_outputs=(OutputPort("value", NUM),),
            )
        )

    def test_mapping_over_a_body_that_is_already_one_is_refused_where_written(self) -> None:
        p = Pipeline(pipeline_id="t")
        split, each = self._split(p), self._each(p)
        p.map_over(
            "first", source=split.ref("pieces"), item=Item("piece"), body=each, expect_items=2
        )
        with pytest.raises(CompositionError, match="body of map group 'first'"):
            p.map_over(
                "second",
                source=each.ref("items"),
                item=Item("x"),
                body=self._again(p),
                expect_items=2,
            )

    def test_making_a_mapped_source_into_a_body_is_refused_where_written(self) -> None:
        """The other order: the second map existed first."""
        p = Pipeline(pipeline_id="t")
        split, each = self._split(p), self._each(p)
        p.map_over(
            "second", source=each.ref("items"), item=Item("x"), body=self._again(p), expect_items=2
        )
        with pytest.raises(CompositionError, match="map group 'second' iterates"):
            p.map_over(
                "first", source=split.ref("pieces"), item=Item("piece"), body=each, expect_items=2
            )

    def _forward(self) -> Pipeline:
        """Named by `ref_to`, so nothing is decidable until the graph is finished."""
        p = Pipeline(pipeline_id="bod", provider="claude-agent-sdk")
        split = self._split(p)
        second = p.map_over(
            "second",
            source=ref_to("each", "items", ARR),
            item=Item("x"),
            body=self._again(p),
            expect_items=2,
        )
        first = p.map_over(
            "first",
            source=split.ref("pieces"),
            item=Item("piece"),
            body=self._each(p),
            expect_items=2,
        )
        p.set_entry(split)
        p.route(split, first)
        p.route(first, second)
        p.route(second, END)
        return p

    def test_a_forward_reference_to_a_body_as_a_source_is_reported(self) -> None:
        problems = lint_pipeline(self._forward())
        assert [x for x in problems if "map group 'second'" in x and "body of map group" in x]

    def test_a_prompt_reading_a_body_is_reported(self) -> None:
        p = Pipeline(pipeline_id="t")
        split, each = self._split(p), self._each(p)
        first = p.map_over(
            "first", source=split.ref("pieces"), item=Item("piece"), body=each, expect_items=2
        )
        reader = p.add(AgentNode(node_id="reader", prompt=tpl("see ", each.ref("items"))))
        p.set_entry(split)
        p.route(split, first)
        p.route(first, reader)
        p.route(reader, END)
        problems = lint_pipeline(p)
        assert [x for x in problems if "agent 'reader'" in x and "body of map group 'first'" in x]

    def test_the_engine_has_nothing_stored_under_the_bodys_name(self, executes: Executes) -> None:
        """What the lint prevents, run: the source resolves to nothing."""
        run = executes(self._forward())
        assert run.returncode != 0
        assert any(
            "'each' output not found" in str(e.get("data"))
            for e in run.events
            if "fail" in str(e.get("type"))
        )


class TestNoOtherReaderReachesABody:
    """The same hole in the two other places a graph names a value to read."""

    @staticmethod
    def _mapped() -> tuple[Pipeline, ComputeNode, MapGroup]:
        p = Pipeline(pipeline_id="t", provider="claude-agent-sdk")
        split = p.add(
            ScriptNode(
                node_id="split",
                command="/bin/echo",
                args=('{"pieces": [1]}',),
                declared_outputs=(OutputPort("pieces", ARR),),
            )
        )
        each = p.add(
            ComputeNode(
                node_id="each",
                values={"items": "[1]"},
                declared_outputs=(OutputPort("items", ARR),),
            )
        )
        first = p.map_over(
            "first", source=split.ref("pieces"), item=Item("piece"), body=each, expect_items=1
        )
        p.set_entry(split)
        p.route(split, first)
        return p, each, first

    def test_exposing_a_body_as_a_pipeline_output_is_refused(self) -> None:
        """The workflow's `output:` would render `each.output.items`, which is never bound."""
        p, each, _ = self._mapped()
        with pytest.raises(CompositionError, match="body of map group 'first'"):
            p.expose_output("leak", each, "items")

    def test_making_an_exposed_node_into_a_body_is_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        split = p.add(
            ScriptNode(
                node_id="split",
                command="/bin/echo",
                declared_outputs=(OutputPort("pieces", ARR),),
            )
        )
        each = p.add(
            ComputeNode(
                node_id="each",
                values={"items": "[1]"},
                declared_outputs=(OutputPort("items", ARR),),
            )
        )
        p.expose_output("leak", each, "items")
        with pytest.raises(CompositionError, match="exposed as pipeline output 'leak'"):
            p.map_over(
                "first", source=split.ref("pieces"), item=Item("piece"), body=each, expect_items=1
            )

    def test_a_questions_step_asking_what_a_body_produced_is_reported(self) -> None:
        p, each, first = self._mapped()
        ask = p.add(ask_human_for(node_id="ask", source=each.ref("items")))
        p.route(first, ask)
        p.route(ask, END)
        problems = lint_pipeline(p)
        assert [x for x in problems if "questions 'ask'" in x and "body of map group" in x]
