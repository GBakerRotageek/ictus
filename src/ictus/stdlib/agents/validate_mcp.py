"""A visible, in-workflow check that one MCP server is actually connected.

``ictus preflight`` answers a different question, more cheaply: is the command
installed, is the token set, does the endpoint answer. All of that can be true
while the server never reaches the model — the provider may not forward it, or
may forward it under a different name.

This node closes that gap by asking the model to *use* the server. It is the
only kind of check that proves the connection end to end, and because it is a
node it shows up in the dashboard: whoever is watching sees which server is
being checked and what came back.

An LLM call is the cost. It is also the only node type Conductor permits inside
a parallel group — scripts, waits and gates are all rejected as members — so a
fan-out of these is the only way to check several servers at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ictus.graph.node import AgentNode, slugify
from ictus.graph.ports import OutputPort, PortType

if TYPE_CHECKING:
    from ictus.graph.requirements import McpServer

__all__ = ["validate_mcp"]


def validate_mcp(server: McpServer, *, node_id: str | None = None) -> AgentNode:
    """Check that ``server`` is connected, by calling one read-only tool from it.

    Outputs ``available`` (boolean) so a route condition can branch on it, and
    ``detail`` (string) so a human reading the dashboard learns what was tried.
    """
    return AgentNode(
        node_id=node_id or f"check_{slugify(server.name)}",
        description=f"Check the {server.name} MCP server responds",
        prompt=(
            f"An MCP server named '{server.name}' should be available to you.\n"
            f"It is needed so that: {server.purpose}\n\n"
            f"Call exactly one inexpensive, read-only tool from '{server.name}' to "
            "confirm it is connected and answering. Do not call tools from any other "
            "server, and do not create, modify or delete anything.\n\n"
            "Report available=true only if a tool from that server actually returned a "
            "result. If no tool from it is offered to you, or the call failed, report "
            "available=false and say in `detail` exactly which tool you tried and what "
            "happened. Do not guess, and do not report success because the server is "
            "mentioned in this prompt."
        ),
        declared_outputs=(
            OutputPort("available", PortType.BOOLEAN, f"Whether {server.name} answered"),
            OutputPort("detail", PortType.STRING, "Which tool was tried, and the result"),
        ),
    )
