"""What a pipeline needs from its environment before it can run.

Declared where the pipeline is written, checked before it launches. The point is
that "this needs a GitHub token" stops being knowledge in someone's head and
becomes a thing the tooling can refuse on.

Secrets are deliberately *not* modelled as values. An ``EnvVar`` names a
variable that must be present in the environment at run time; ictus checks it is
set and never reads or emits it. A committed workflow file with a token in it is
a worse problem than an unconfigured one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from ictus.errors import CompositionError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["EnvVar", "McpServer", "McpTransport"]


class McpTransport(StrEnum):
    """How a client reaches an MCP server."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass(frozen=True, slots=True)
class EnvVar:
    """An environment variable that must be set before the pipeline runs.

    ``purpose`` is shown to whoever has to go and set it, so write it for them:
    "a GitHub token with repo:read" beats "the token".
    """

    name: str
    purpose: str = ""
    secret: bool = True


@dataclass(frozen=True, slots=True)
class McpServer:
    """An MCP server a pipeline needs access to.

    Transport-specific fields are checked here rather than at emission, so an
    ``http`` server with no URL is refused while the pipeline is being written.
    """

    name: str
    purpose: str
    transport: McpTransport = McpTransport.STDIO
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    env: tuple[EnvVar, ...] = ()
    tools: tuple[str, ...] = ()
    timeout_ms: int | None = None
    setup_hint: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise CompositionError("an MCP server needs a name")
        if not self.purpose:
            raise CompositionError(
                f"MCP server {self.name!r} needs a purpose; it is what the person "
                "being asked to configure it will read"
            )
        if self.transport is McpTransport.STDIO:
            if not self.command:
                raise CompositionError(
                    f"MCP server {self.name!r} uses stdio and needs a command to run"
                )
            if self.url is not None:
                raise CompositionError(
                    f"MCP server {self.name!r} uses stdio; a url belongs to http or sse"
                )
        else:
            if not self.url:
                raise CompositionError(
                    f"MCP server {self.name!r} uses {self.transport.value} and needs a url"
                )
            if self.command is not None:
                raise CompositionError(
                    f"MCP server {self.name!r} uses {self.transport.value}; "
                    "a command belongs to stdio"
                )

    @property
    def required_env(self) -> tuple[EnvVar, ...]:
        """Environment variables that must be set for this server to work."""
        return self.env
