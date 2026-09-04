"""Pipeline definitions - large-scale flows of stages and nodes.

Pipelines compose stages and occasional nodes into complete workflows.
Composition-time validation ensures inputs match outputs when connecting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ictus.node import Node  # noqa: TC001
from ictus.stage import Stage  # noqa: TC001
from ictus.types import InputPort, OutputPort, PortConnection, SerializedDict


@dataclass
class PipelineElement:
    """A union-like type for either a Node or Stage in a pipeline."""

    node: Node | None = None
    stage: Stage | None = None

    def name(self) -> str:
        if self.node:
            return self.node.name
        return self.stage.name if self.stage else "unknown"

    def id(self) -> str:
        if self.node:
            return self.node.node_id
        return self.stage.stage_id if self.stage else "unknown"

    def get_outputs(self) -> dict[str, OutputPort]:
        """Get all outputs from this element."""
        if self.node:
            return self.node.outputs
        return self.stage.outputs if self.stage else {}

    def get_inputs(self) -> dict[str, InputPort]:
        """Get all inputs from this element."""
        if self.node:
            return self.node.inputs
        return self.stage.inputs if self.stage else {}


@dataclass
class PipelineEdge:
    """Edge between two pipeline elements with port connections."""

    from_element: PipelineElement
    to_element: PipelineElement
    port_connection: PortConnection | None = None
    case: str | None = None  # For branching


@dataclass
class Pipeline:
    """A complete workflow composed of stages and nodes."""

    name: str
    pipeline_id: str = field(default="")
    description: str = ""
    elements: list[PipelineElement] = field(default_factory=list)
    edges: list[PipelineEdge] = field(default_factory=list)
    start_element: PipelineElement | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.pipeline_id:
            self.pipeline_id = self.name.lower().replace(" ", "_")

    def add_stage(self, stage: Stage) -> PipelineElement:
        """Add a stage to the pipeline."""
        element = PipelineElement(stage=stage)
        self.elements.append(element)
        if self.start_element is None:
            self.start_element = element
        return element

    def add_node(self, node: Node) -> PipelineElement:
        """Add a node to the pipeline."""
        element = PipelineElement(node=node)
        self.elements.append(element)
        if self.start_element is None:
            self.start_element = element
        return element

    def connect(
        self,
        from_element: PipelineElement,
        to_element: PipelineElement,
        from_port: str = "output",
        to_port: str = "input",
    ) -> PipelineEdge:
        """Connect two elements with composition-time validation.

        Args:
            from_element: Source element
            to_element: Target element
            from_port: Output port name (default "output")
            to_port: Input port name (default "input")

        Raises:
            KeyError: If port names don't exist
            ValueError: If port types don't match
        """
        from_outputs = from_element.get_outputs()
        to_inputs = to_element.get_inputs()

        if from_port not in from_outputs:
            raise KeyError(f"Output port '{from_port}' not found on {from_element.name()}")
        if to_port not in to_inputs:
            raise KeyError(f"Input port '{to_port}' not found on {to_element.name()}")

        output_port = from_outputs[from_port]
        input_port = to_inputs[to_port]

        # Composition-time validation
        connection = PortConnection(output_port, input_port)
        connection.validate()

        edge = PipelineEdge(from_element, to_element, connection)
        self.edges.append(edge)
        return edge

    def branch(
        self,
        from_element: PipelineElement,
        branches: dict[str, PipelineElement],
        default: PipelineElement | None = None,
    ) -> None:
        """Add conditional branching edges.

        Args:
            from_element: Source element
            branches: Dict of case -> target element
            default: Default target if no case matches
        """
        for case, to_element in branches.items():
            edge = PipelineEdge(from_element, to_element, case=case)
            self.edges.append(edge)
        if default:
            edge = PipelineEdge(from_element, default)
            self.edges.append(edge)

    def to_dict(self) -> SerializedDict:
        """Serialize pipeline to dict for YAML emission."""
        if not self.elements:
            raise ValueError("Pipeline must have at least one element")
        if self.start_element is None:
            raise ValueError("Pipeline must have a start element")

        # Build element defs
        element_defs: list[SerializedDict] = []
        for element in self.elements:
            if element.node:
                element_dict = element.node.to_dict()
            else:
                assert element.stage is not None
                element_dict = element.stage.to_dict()

            # Find outgoing edges
            outgoing = [e for e in self.edges if e.from_element == element]
            if outgoing:
                if len(outgoing) == 1 and outgoing[0].case is None:
                    # Simple linear edge
                    element_dict["next_task_id"] = outgoing[0].to_element.id()
                else:
                    # Branching
                    transitions = {}
                    default_transition = None
                    for edge in outgoing:
                        if edge.case:
                            transitions[edge.case] = edge.to_element.id()
                        else:
                            default_transition = edge.to_element.id()
                    element_dict["transitions"] = transitions
                    if default_transition:
                        element_dict["default_transition"] = default_transition

            element_defs.append(element_dict)

        workflow_def: SerializedDict = {
            "name": self.name,
            "description": self.description,
            "version": "1",
            "entry_point": self.start_element.id(),
        }

        return {
            "workflow": workflow_def,
            "agents": element_defs,
        }


class PipelineBuilder:
    """Builder for creating pipelines with fluent API."""

    def __init__(self, name: str, description: str = "") -> None:
        self.pipeline = Pipeline(name=name, description=description)

    def with_id(self, pipeline_id: str) -> PipelineBuilder:
        """Set the pipeline ID."""
        self.pipeline.pipeline_id = pipeline_id
        return self

    def add_stage(self, stage: Stage) -> PipelineBuilder:
        """Add a stage to the pipeline."""
        self.pipeline.add_stage(stage)
        return self

    def add_node(self, node: Node) -> PipelineBuilder:
        """Add a node to the pipeline."""
        self.pipeline.add_node(node)
        return self

    def connect(
        self,
        from_element: PipelineElement,
        to_element: PipelineElement,
        from_port: str = "output",
        to_port: str = "input",
    ) -> PipelineBuilder:
        """Connect two elements."""
        self.pipeline.connect(from_element, to_element, from_port, to_port)
        return self

    def branch(
        self,
        from_element: PipelineElement,
        branches: dict[str, PipelineElement],
        default: PipelineElement | None = None,
    ) -> PipelineBuilder:
        """Add branching."""
        self.pipeline.branch(from_element, branches, default)
        return self

    def build(self) -> Pipeline:
        """Build the pipeline."""
        return self.pipeline
