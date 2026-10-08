"""Composition-time rejections.

Each asserts that an invalid graph is refused at the call that introduces it.
"""

from __future__ import annotations

import pytest

from ictus import (
    AgentNode,
    CompositionError,
    ComputeNode,
    GateChoice,
    GateNode,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    PortTypeError,
    TerminateNode,
    UnknownPortError,
    WaitNode,
)
from ictus.stdlib import succeed

S, N = PortType.STRING, PortType.NUMBER


def _agent(node_id: str, *, out: PortType = S, in_: PortType | None = None) -> AgentNode:
    return AgentNode(
        node_id=node_id,
        prompt=f"do {node_id}",
        inputs=(InputPort("in", in_),) if in_ is not None else (),
        declared_outputs=(OutputPort("out", out),),
    )


class TestPortTyping:
    def test_mismatched_port_types_are_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        a, b = p.add(_agent("a", out=S)), p.add(_agent("b", in_=N))
        with pytest.raises(PortTypeError, match="port types differ"):
            p.connect(a, "out", b, "in")
        assert p.edges == (), "a rejected connect must not leave a partial edge"

    def test_matching_port_types_connect(self) -> None:
        p = Pipeline(pipeline_id="t")
        a, b = p.add(_agent("a", out=S)), p.add(_agent("b", in_=S))
        p.connect(a, "out", b, "in")
        assert len(p.edges) == 1
        assert len(p.data_deps) == 1

    def test_feed_type_checks_too(self) -> None:
        p = Pipeline(pipeline_id="t")
        a, b = p.add(_agent("a", out=S)), p.add(_agent("b", in_=N))
        with pytest.raises(PortTypeError):
            p.feed(a, "out", b, "in")

    def test_unknown_port_names_are_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        a, b = p.add(_agent("a")), p.add(_agent("b", in_=S))
        with pytest.raises(UnknownPortError, match="no output port 'nope'"):
            p.connect(a, "nope", b, "in")
        with pytest.raises(UnknownPortError, match="no input port 'nope'"):
            p.connect(a, "out", b, "nope")

    def test_workflow_input_type_is_checked(self) -> None:
        p = Pipeline(pipeline_id="t")
        b = p.add(_agent("b", in_=N))
        param = p.declare_input("x", S)
        with pytest.raises(PortTypeError, match="types differ"):
            p.connect_input(param, b, "in")


class TestMembership:
    def test_foreign_node_is_refused(self) -> None:
        """The README's promise: a target that is not in the graph is not expressible."""
        one, two = Pipeline(pipeline_id="one"), Pipeline(pipeline_id="two")
        a = one.add(_agent("a"))
        stranger = two.add(_agent("b", in_=S))
        with pytest.raises(CompositionError, match="is not part of pipeline 'one'"):
            one.connect(a, "out", stranger, "in")

    def test_same_node_added_twice_is_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        node = _agent("a")
        p.add(node)
        with pytest.raises(CompositionError, match="the same node twice"):
            p.add(node)

    def test_distinct_nodes_sharing_an_id_are_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        p.add(_agent("a"))
        with pytest.raises(CompositionError, match="two nodes"):
            p.add(_agent("a"))

    def test_structurally_identical_nodes_do_not_share_edges(self) -> None:
        """Nodes compare by identity, so equal-valued nodes route independently."""
        p = Pipeline(pipeline_id="t")
        a1 = p.add(AgentNode(node_id="a1", prompt="same", declared_outputs=(OutputPort("out", S),)))
        a2 = p.add(AgentNode(node_id="a2", prompt="same", declared_outputs=(OutputPort("out", S),)))
        sink = p.add(_agent("sink", in_=S))
        p.connect(a2, "out", sink, "in")
        assert p.outgoing(a1) == []
        assert len(p.outgoing(a2)) == 1


class TestRouting:
    def test_second_unconditional_route_is_refused(self) -> None:
        """Conductor takes the first match, so a second default would be dropped."""
        p = Pipeline(pipeline_id="t")
        a = p.add(_agent("a"))
        b, c = p.add(_agent("b", in_=S)), p.add(_agent("c", in_=S))
        p.connect(a, "out", b, "in")
        with pytest.raises(CompositionError, match="already has an unconditional route"):
            p.connect(a, "out", c, "in")

    def test_conditional_routes_may_share_a_source(self) -> None:
        p = Pipeline(pipeline_id="t")
        a = p.add(_agent("a"))
        b, c = p.add(_agent("b", in_=S)), p.add(_agent("c", in_=S))
        p.connect(a, "out", b, "in", when="{{ a.output.out == 'b' }}")
        p.connect(a, "out", c, "in")
        assert len(p.outgoing(a)) == 2

    def test_terminate_cannot_route_onward(self) -> None:
        p = Pipeline(pipeline_id="t")
        end = p.add(succeed(node_id="end", reason="done"))
        nxt = p.add(_agent("nxt", in_=S))
        with pytest.raises(CompositionError, match="terminate node"):
            p.route(end, nxt)

    def test_gate_cannot_be_a_connect_source(self) -> None:
        p = Pipeline(pipeline_id="t")
        gate = p.add(GateNode(node_id="g", prompt="?", choices=(GateChoice("a", "A"),)))
        sink = p.add(_agent("s", in_=S))
        with pytest.raises(CompositionError, match="routes through its options"):
            p.route(gate, sink)


class TestGates:
    def test_unrouted_choice_is_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        gate = p.add(
            GateNode(node_id="g", prompt="?", choices=(GateChoice("a", "A"), GateChoice("b", "B")))
        )
        target = p.add(succeed(node_id="done", reason="d"))
        with pytest.raises(CompositionError, match=r"unrouted choice\(s\) \['b'\]"):
            p.branch(gate, {"a": target})

    def test_unknown_choice_value_is_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        gate = p.add(GateNode(node_id="g", prompt="?", choices=(GateChoice("a", "A"),)))
        target = p.add(succeed(node_id="done", reason="d"))
        with pytest.raises(CompositionError, match=r"no choice\(s\) \['zzz'\]"):
            p.branch(gate, {"a": target, "zzz": target})

    def test_branching_twice_is_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        gate = p.add(GateNode(node_id="g", prompt="?", choices=(GateChoice("a", "A"),)))
        target = p.add(succeed(node_id="done", reason="d"))
        p.branch(gate, {"a": target})
        with pytest.raises(CompositionError, match="already been branched"):
            p.branch(gate, {"a": target})

    def test_duplicate_choice_values_are_refused(self) -> None:
        with pytest.raises(CompositionError, match="duplicate choice values"):
            GateNode(node_id="g", prompt="?", choices=(GateChoice("a", "A"), GateChoice("a", "B")))


class TestNodeInvariants:
    def test_agent_without_a_prompt_is_refused(self) -> None:
        with pytest.raises(CompositionError, match="non-empty prompt"):
            AgentNode(node_id="a", prompt="   ")

    def test_node_id_must_be_a_routing_identifier(self) -> None:
        with pytest.raises(CompositionError, match="not a routing identifier"):
            AgentNode(node_id="Review Gate", prompt="x")

    def test_duplicate_port_names_are_refused(self) -> None:
        with pytest.raises(CompositionError, match="more than once"):
            AgentNode(
                node_id="a",
                prompt="x",
                inputs=(InputPort("dup", S),),
                declared_outputs=(OutputPort("dup", S),),
            )

    def test_set_requires_exactly_one_of_value_or_values(self) -> None:
        with pytest.raises(CompositionError, match="exactly one"):
            ComputeNode(node_id="s")
        with pytest.raises(CompositionError, match="exactly one"):
            ComputeNode(node_id="s", value="a", values={"b": "c"})

    def test_wait_requires_a_positive_duration(self) -> None:
        with pytest.raises(CompositionError, match="positive duration"):
            WaitNode(node_id="w", duration=0)

    def test_terminate_requires_a_reason(self) -> None:
        with pytest.raises(CompositionError, match="non-empty reason"):
            TerminateNode(node_id="t", status="success", reason="  ")


class TestLoopBounds:
    def test_cycle_without_a_declared_bound_is_refused(self) -> None:
        """Conductor's default of 10 total steps would stop the run mid-loop."""
        p = Pipeline(pipeline_id="t")
        a = p.add(_agent("a", in_=S))
        b = p.add(_agent("b", in_=S))
        p.set_entry(a)
        p.connect(a, "out", b, "in")
        p.connect(b, "out", a, "in")
        with pytest.raises(CompositionError, match="loop_passes"):
            p.require_loop_bound()

    def test_a_declared_bound_satisfies_the_rule(self) -> None:
        """How the bound is spent is the backend's arithmetic, tested with it."""
        p = Pipeline(pipeline_id="t", loop_passes=3)
        a, b = p.add(_agent("a", in_=S)), p.add(_agent("b", in_=S))
        p.set_entry(a)
        p.connect(a, "out", b, "in")
        p.connect(b, "out", a, "in")
        p.require_loop_bound()
        assert p.longest_cycle_length() == 2

    def test_only_the_closing_edge_counts_as_a_back_edge(self) -> None:
        """In a two-node loop the naive test flags both edges; only one closes it."""
        p = Pipeline(pipeline_id="t", loop_passes=2)
        a, b = p.add(_agent("a", in_=S)), p.add(_agent("b", in_=S))
        p.set_entry(a)
        forward = p.connect(a, "out", b, "in")
        closing = p.connect(b, "out", a, "in")
        assert p.back_edges() == [closing]
        assert forward not in p.back_edges()


class TestEntryPoint:
    def test_ambiguous_entry_is_refused(self) -> None:
        p = Pipeline(pipeline_id="t")
        p.add(_agent("a"))
        p.add(_agent("b"))
        with pytest.raises(CompositionError, match="set_entry"):
            p.entry()

    def test_unique_root_is_used(self) -> None:
        p = Pipeline(pipeline_id="t")
        a, b = p.add(_agent("a")), p.add(_agent("b", in_=S))
        p.connect(a, "out", b, "in")
        assert p.entry() is a
