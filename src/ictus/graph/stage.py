"""Stages — reusable collections of nodes.

A stage is a whole workflow in its own right: its own entry point, its own
graph, its own loops and gates. Placing one in a parent emits a second YAML file
plus a single ``type: workflow`` agent that references it. That is Conductor's
only nesting construct; there is no nested-step list inside ``AgentDef``, which
is why the previous ``tasks:`` key had no counterpart and no rename could fix
it.

The contract at the boundary is what makes a stage composable: the body's
``declare_input`` calls become the child's ``workflow.input``, and its
``expose_output`` calls become the child's top-level ``output:``. ictus keeps
the port types on both sides. Conductor cannot — its ``output:`` map is
``dict[str, str]`` — so a wrong stage wiring is caught here or nowhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.errors import CompositionError
from ictus.graph.node import SubGraphNode
from ictus.graph.pipeline import Pipeline

if TYPE_CHECKING:
    from ictus.graph.ports import InputPort, OutputPort


class Stage:
    """A named, reusable sub-graph that compiles to its own workflow file."""

    def __init__(
        self,
        *,
        stage_id: str,
        description: str = "",
        loop_passes: int | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.body = Pipeline(
            pipeline_id=stage_id,
            description=description,
            loop_passes=loop_passes,
            max_iterations=max_iterations,
        )

    @property
    def stage_id(self) -> str:
        """The stage's identifier, and the basename of its emitted file."""
        return self.body.pipeline_id

    @property
    def description(self) -> str:
        """Human description, forwarded to the emitted workflow."""
        return self.body.description

    @property
    def input_ports(self) -> tuple[InputPort, ...]:
        """The stage's parameters, as ports a parent can wire into."""
        return self.body.declared_input_ports

    @property
    def output_ports(self) -> tuple[OutputPort, ...]:
        """The stage's results, as ports a parent can wire from."""
        return self.body.exposed_output_ports

    def instantiate(
        self,
        parent: Pipeline,
        *,
        node_id: str | None = None,
        description: str = "",
        max_depth: int | None = None,
    ) -> SubGraphNode:
        """Place this stage into ``parent`` and return the node standing for it.

        The returned node carries the stage's contract as ordinary ports, so the
        parent wires it with the same ``connect`` / ``feed`` calls and the same
        type checking as any single node.
        """
        if not self.output_ports and not self.input_ports:
            raise CompositionError(
                f"stage {self.stage_id!r} exposes no inputs and no outputs; "
                "declare_input()/expose_output() on its body give it a contract, "
                "without which it cannot be wired to anything"
            )
        node = SubGraphNode(
            node_id=node_id or self.stage_id,
            description=description or self.description,
            inputs=self.input_ports,
            declared_outputs=self.output_ports,
            target=f"./{self.stage_id}.yaml",
            max_depth=max_depth,
        )
        parent.add_subworkflow(node, self.body)
        return node
