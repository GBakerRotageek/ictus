"""Lowering a pipeline to Conductor's ``workflow:`` block.

Every default Conductor would otherwise apply silently is written out here:
the provider (which defaults to copilot), the iteration bound (which defaults to
10 total steps and would stop a loop midway), the context mode (which defaults
to ``accumulate``, under which the declared ``input:`` graph is parsed and never
consulted), and checkpointing for any graph containing a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict

from ictus.errors import EmitError
from ictus.interfaces.conductor.mcp import mcp_servers_block

__all__ = ["NOTHING_INHERITED", "Inherited", "workflow_block"]

# Conductor's own default when the workflow omits a provider.
DEFAULT_PROVIDER = "copilot"

# Conductor counts every step execution against one global budget, and caps it.
ITERATION_CEILING = 500


def max_iterations(pipeline: Pipeline) -> int:
    """Price the graph in the unit Conductor charges: total step executions.

    A loop costs its body length on every pass, so the bound has to be derived
    from the shape of the graph. Conductor's default is 10, which stops a
    six-node pipeline with one review loop midway through its second pass — at a
    number nobody chose.
    """
    if pipeline.max_iterations is not None:
        return _within_ceiling(pipeline, pipeline.max_iterations)
    pipeline.require_loop_bound()
    # A map body is one node in the graph and up to `max_items` executions in
    # the engine: Conductor records the group's cost as the number of items it
    # actually spawned.
    fanout = sum(group.expect_items - 1 for group in pipeline.maps)
    base = len(pipeline.nodes) + fanout
    if not pipeline.has_cycle():
        return _within_ceiling(pipeline, max(1, base))
    return _within_ceiling(pipeline, max(1, base + pipeline.loop_cost(pipeline.loop_passes or 1)))


def _within_ceiling(pipeline: Pipeline, wanted: int) -> int:
    """Refuse a budget Conductor cannot hold, rather than quietly truncating it.

    Clamping looks harmless and is not: the group cost is charged after the work
    runs, so a truncated budget fails on the *next* node, having already paid for
    everything before it.
    """
    if wanted > ITERATION_CEILING:
        raise EmitError(
            f"pipeline {pipeline.pipeline_id!r} needs {wanted} steps but Conductor caps "
            f"max_iterations at {ITERATION_CEILING}. Reduce loop_passes, split the graph "
            "into stages (a stage costs its caller one step), or set max_iterations "
            "explicitly and accept the truncation."
        )
    return wanted


@dataclass(frozen=True, slots=True)
class Inherited:
    """Runtime settings a nested workflow takes from the pipeline that hosts it.

    Conductor loads each ``type: workflow`` file on its own, so a child that
    names no provider gets Conductor's default rather than its parent's. That is
    how four of six emitted files ended up on ``copilot`` while their parents ran
    on ``claude-agent-sdk`` — a setting the author made once, silently ignored by
    everything nested inside it.

    Resolved when the file is written rather than when the stage is placed,
    because a parent's provider is often chosen after the stage is instantiated.
    """

    provider: str | None = None
    default_model: str | None = None

    def under(self, parent: Pipeline) -> Inherited:
        """What a child of ``parent`` should inherit, parent's own choice first."""
        return Inherited(
            provider=parent.provider or self.provider,
            default_model=parent.default_model or self.default_model,
        )


NOTHING_INHERITED = Inherited()


def workflow_block(pipeline: Pipeline, inherited: Inherited = NOTHING_INHERITED) -> YamlDict:
    block: YamlDict = {"name": pipeline.pipeline_id}
    if pipeline.description:
        block["description"] = pipeline.description
    block["version"] = pipeline.version
    block["entry_point"] = pipeline.entry().node_id

    runtime: YamlDict = {
        "provider": {"name": pipeline.provider or inherited.provider or DEFAULT_PROVIDER}
    }
    model = pipeline.default_model or inherited.default_model
    if model is not None:
        runtime["default_model"] = model
    servers = mcp_servers_block(pipeline)
    if servers:
        runtime["mcp_servers"] = servers
    if pipeline.has_gate():
        # A gated run is long-lived by construction: the human may be hours
        # away. Checkpointing only on failure would discard that wait.
        runtime["checkpoint"] = {"every_agent": True}
    block["runtime"] = runtime

    if pipeline.workflow_inputs:
        params: YamlDict = {}
        for param in pipeline.workflow_inputs:
            entry: YamlDict = {"type": param.port_type.value, "required": param.required}
            if param.default is not None:
                entry["default"] = param.default
            if param.description:
                entry["description"] = param.description
            params[param.name] = entry
        block["input"] = params

    block["context"] = {"mode": pipeline.context_mode}

    limits: YamlDict = {"max_iterations": max_iterations(pipeline)}
    if pipeline.budget_usd is not None:
        limits["budget_usd"] = pipeline.budget_usd
        limits["budget_mode"] = pipeline.budget_mode
    block["limits"] = limits

    if pipeline.metadata:
        block["metadata"] = dict(pipeline.metadata)
    return block
