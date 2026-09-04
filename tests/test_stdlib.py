"""Tests for stdlib node factory functions."""

from __future__ import annotations

from ictus import (
    approval_gate,
    build_approval_gate_pair,
    build_summary_approval_gate,
    failure_node,
    filter_array,
    if_then_node,
    log_node,
    merge_data,
    multi_choice_gate,
    status_report,
    success_node,
    switch_node,
    text_summary,
    transform_data,
    wait_node,
)
from ictus.core import Node


class TestGateNodes:
    """Tests for gate node factories."""

    def test_approval_gate_creation(self) -> None:
        """Test creating an approval gate node."""
        gate = approval_gate()

        assert isinstance(gate, Node)
        assert gate.name == "Approval Gate"
        assert "summary" in gate.inputs
        assert "decision" in gate.outputs

    def test_approval_gate_custom_id(self) -> None:
        """Test approval gate with custom ID."""
        gate = approval_gate(node_id="my_gate")

        assert gate.node_id == "my_gate"

    def test_multi_choice_gate_creation(self) -> None:
        """Test creating a multi-choice gate node."""
        gate = multi_choice_gate()

        assert isinstance(gate, Node)
        assert "choices" in gate.inputs
        assert "selected_choice" in gate.outputs


class TestSummaryNodes:
    """Tests for summary node factories."""

    def test_text_summary_creation(self) -> None:
        """Test creating a text summary node."""
        node = text_summary()

        assert isinstance(node, Node)
        assert "data" in node.inputs
        assert "summary" in node.outputs

    def test_status_report_creation(self) -> None:
        """Test creating a status report node."""
        node = status_report()

        assert isinstance(node, Node)
        assert "results" in node.inputs
        assert "report" in node.outputs
        assert "status" in node.outputs


class TestTerminalNodes:
    """Tests for terminal node factories."""

    def test_success_node_creation(self) -> None:
        """Test creating a success terminal node."""
        node = success_node()

        assert isinstance(node, Node)
        assert "summary" in node.inputs
        assert "status" in node.outputs

    def test_failure_node_creation(self) -> None:
        """Test creating a failure terminal node."""
        node = failure_node()

        assert isinstance(node, Node)
        assert "error_message" in node.inputs
        assert "status" in node.outputs


class TestUtilityNodes:
    """Tests for utility node factories."""

    def test_log_node_creation(self) -> None:
        """Test creating a log node."""
        node = log_node()

        assert isinstance(node, Node)
        assert "data" in node.inputs
        assert "data" in node.outputs

    def test_merge_data_creation(self) -> None:
        """Test creating a merge data node."""
        node = merge_data()

        assert isinstance(node, Node)
        assert "primary" in node.inputs
        assert "secondary" in node.inputs
        assert "merged" in node.outputs

    def test_transform_data_creation(self) -> None:
        """Test creating a transform data node."""
        node = transform_data()

        assert isinstance(node, Node)
        assert "input" in node.inputs
        assert "transform_spec" in node.inputs
        assert "output" in node.outputs

    def test_filter_array_creation(self) -> None:
        """Test creating a filter array node."""
        node = filter_array()

        assert isinstance(node, Node)
        assert "array" in node.inputs
        assert "filtered" in node.outputs

    def test_wait_node_creation(self) -> None:
        """Test creating a wait node."""
        node = wait_node()

        assert isinstance(node, Node)
        assert "duration" in node.inputs
        assert "waited" in node.outputs


class TestConditionalNodes:
    """Tests for conditional branching node factories."""

    def test_if_then_node_creation(self) -> None:
        """Test creating an if/then node."""
        node = if_then_node()

        assert isinstance(node, Node)
        assert "condition" in node.inputs
        assert "branch" in node.outputs

    def test_switch_node_creation(self) -> None:
        """Test creating a switch node."""
        node = switch_node()

        assert isinstance(node, Node)
        assert "value" in node.inputs
        assert "case" in node.outputs


class TestNodeBuilders:
    """Tests for pre-built node pair builders."""

    def test_approval_gate_pair(self) -> None:
        """Test building an approval gate pair."""
        gate, success, fail = build_approval_gate_pair()

        assert isinstance(gate, Node)
        assert isinstance(success, Node)
        assert isinstance(fail, Node)
        assert gate.node_id == "approval"
        assert success.node_id == "approved"
        assert fail.node_id == "rejected"

    def test_summary_approval_gate(self) -> None:
        """Test building a summary + approval gate pair."""
        summary, gate = build_summary_approval_gate()

        assert isinstance(summary, Node)
        assert isinstance(gate, Node)
        assert summary.node_id == "summarize"
        assert gate.node_id == "review"
