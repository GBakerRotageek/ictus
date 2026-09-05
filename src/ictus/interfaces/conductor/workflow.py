"""Lowering a pipeline to Conductor's ``workflow:`` block.

Every default Conductor would otherwise apply silently is written out here:
the provider (which defaults to copilot), the iteration bound (which defaults to
10 total steps and would stop a loop midway), the context mode (which defaults
to ``accumulate``, under which the declared ``input:`` graph is parsed and never
consulted), and checkpointing for any graph containing a gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict

from ictus.interfaces.conductor.mcp import mcp_servers_block

__all__ = ["workflow_block"]

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
        return pipeline.max_iterations
    pipeline.require_loop_bound()
    base = len(pipeline.nodes)
    if not pipeline.has_cycle():
        return max(1, min(ITERATION_CEILING, base))
    extra = pipeline.longest_cycle_length() * ((pipeline.loop_passes or 1) - 1)
    return max(1, min(ITERATION_CEILING, base + extra))


def workflow_block(pipeline: Pipeline) -> YamlDict:
    block: YamlDict = {"name": pipeline.pipeline_id}
    if pipeline.description:
        block["description"] = pipeline.description
    block["version"] = pipeline.version
    block["entry_point"] = pipeline.entry().node_id

    runtime: YamlDict = {"provider": {"name": pipeline.provider or DEFAULT_PROVIDER}}
    if pipeline.default_model is not None:
        runtime["default_model"] = pipeline.default_model
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
