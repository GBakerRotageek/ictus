"""Tests for the core node/stage/pipeline composition module."""

from __future__ import annotations

import pytest

from ictus import InputPort, NodeBuilder, OutputPort, Pipeline, PortType


class TestNode:
    """Tests for Node definitions."""

    def test_node_builder_creation(self) -> None:
        """Test creating a node with builder."""
        node = (
            NodeBuilder("Test Node", description="A test node")
            .with_id("test_node")
            .input("input", PortType.STRING)
            .output("output", PortType.STRING)
            .build()
        )

        assert node.name == "Test Node"
        assert node.node_id == "test_node"
        assert "input" in node.inputs
        assert "output" in node.outputs

    def test_node_get_ports(self) -> None:
        """Test accessing ports on a node."""
        node = NodeBuilder("Test").input("in", PortType.TEXT).output("out", PortType.TEXT).build()

        in_port = node.get_input("in")
        out_port = node.get_output("out")

        assert in_port.name == "in"
        assert out_port.name == "out"

    def test_node_get_missing_port_raises(self) -> None:
        """Test that accessing missing ports raises KeyError."""
        node = NodeBuilder("Test").build()

        with pytest.raises(KeyError):
            node.get_input("nonexistent")

        with pytest.raises(KeyError):
            node.get_output("nonexistent")


class TestPortTypes:
    """Tests for port types and matching."""

    def test_matching_ports(self) -> None:
        """Test that matching port types connect."""
        output = OutputPort("out", PortType.STRING)
        input_port = InputPort("in", PortType.STRING)

        assert output.matches(input_port)
        assert input_port.matches(output)

    def test_mismatched_ports_raise(self) -> None:
        """Test that mismatched port types raise errors."""
        output = OutputPort("out", PortType.STRING)
        input_port = InputPort("in", PortType.NUMBER)

        with pytest.raises(ValueError, match="Cannot connect"):
            from ictus import PortConnection

            connection = PortConnection(output, input_port)
            connection.validate()


class TestPipeline:
    """Tests for pipeline composition."""

    def test_pipeline_creation(self) -> None:
        """Test creating a basic pipeline."""
        pipeline = Pipeline(
            name="Test Pipeline",
            pipeline_id="test-pipeline",
            description="A test pipeline",
        )

        assert pipeline.name == "Test Pipeline"
        assert pipeline.pipeline_id == "test-pipeline"

    def test_add_nodes_to_pipeline(self) -> None:
        """Test adding nodes to a pipeline."""
        pipeline = Pipeline(name="Test")

        node1 = NodeBuilder("Node1").output("out", PortType.STRING).build()
        node2 = NodeBuilder("Node2").input("in", PortType.STRING).build()

        elem1 = pipeline.add_node(node1)
        pipeline.add_node(node2)

        assert len(pipeline.elements) == 2
        assert pipeline.start_element == elem1

    def test_connect_nodes_composition_time_validation(self) -> None:
        """Test that port type mismatches are caught at composition time."""
        pipeline = Pipeline(name="Test")

        node1 = NodeBuilder("Node1").output("out", PortType.STRING).build()
        node2 = NodeBuilder("Node2").input("in", PortType.NUMBER).build()

        elem1 = pipeline.add_node(node1)
        elem2 = pipeline.add_node(node2)

        with pytest.raises(ValueError, match="Cannot connect"):
            pipeline.connect(elem1, elem2, from_port="out", to_port="in")

    def test_connect_nodes_type_safe(self) -> None:
        """Test that matching port types connect successfully."""
        pipeline = Pipeline(name="Test")

        node1 = NodeBuilder("Node1").output("out", PortType.TEXT).build()
        node2 = NodeBuilder("Node2").input("in", PortType.TEXT).build()

        elem1 = pipeline.add_node(node1)
        elem2 = pipeline.add_node(node2)

        # Should not raise
        pipeline.connect(elem1, elem2, from_port="out", to_port="in")
        assert len(pipeline.edges) == 1

    def test_pipeline_to_dict(self) -> None:
        """Test serializing a pipeline to dict."""
        pipeline = Pipeline(name="Test", pipeline_id="test")

        node = (
            NodeBuilder("Node1").output("out", PortType.STRING).input("in", PortType.STRING).build()
        )

        pipeline.add_node(node)

        result = pipeline.to_dict()
        result_dict: dict[str, object] = dict(result)

        assert "workflow" in result_dict
        assert "agents" in result_dict
        workflow = result_dict["workflow"]
        assert isinstance(workflow, dict)
        assert workflow["name"] == "Test"
        assert workflow["entry_point"] == "node1"


class TestCompositionTimeValidation:
    """Tests for composition-time validation features."""

    def test_missing_output_port_raises(self) -> None:
        """Test that referencing missing output port raises at composition time."""
        pipeline = Pipeline(name="Test")

        node1 = NodeBuilder("Node1").output("out", PortType.STRING).build()
        node2 = NodeBuilder("Node2").input("in", PortType.STRING).build()

        elem1 = pipeline.add_node(node1)
        elem2 = pipeline.add_node(node2)

        with pytest.raises(KeyError, match="Output port"):
            pipeline.connect(elem1, elem2, from_port="nonexistent", to_port="in")

    def test_missing_input_port_raises(self) -> None:
        """Test that referencing missing input port raises at composition time."""
        pipeline = Pipeline(name="Test")

        node1 = NodeBuilder("Node1").output("out", PortType.STRING).build()
        node2 = NodeBuilder("Node2").input("in", PortType.STRING).build()

        elem1 = pipeline.add_node(node1)
        elem2 = pipeline.add_node(node2)

        with pytest.raises(KeyError, match="Input port"):
            pipeline.connect(elem1, elem2, from_port="out", to_port="nonexistent")
