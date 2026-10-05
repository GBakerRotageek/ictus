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

    from ictus.graph.signals import RunSignal

__all__ = ["EnvVar", "Executable", "McpServer", "McpTransport", "Notifier", "NotifierKind"]


class NotifierKind(StrEnum):
    """What shape a report takes when it arrives.

    Named here beside ``McpTransport`` for the same reason: it is a property of
    what the pipeline asked for, not of whatever happens to deliver it. The
    difference between these two is the body of one HTTP POST — Slack's incoming
    webhooks are ordinary HTTPS, never a socket — so this picks a payload, not a
    protocol.
    """

    WEBHOOK = "webhook"
    """A JSON POST carrying the signal, its run, and the engine's own payload."""

    SLACK = "slack"
    """An incoming-webhook message, written to be read by a person in a channel."""


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
class Executable:
    """A command that must be on ``PATH`` before the pipeline runs.

    Declared for the same reason an ``EnvVar`` is: so "this pipeline reads
    Conductor's own source" stops being knowledge in the author's head and
    becomes something preflight can refuse on.

    The failure this exists to prevent is not a crash. A step told to go and
    check something against a tool that is not reachable does not fail — it
    reports that the thing it was checking does not exist, which is a confident
    wrong answer that costs a whole run to produce and looks exactly like a
    considered one. Better to refuse at the launch, for free.

    ``probe`` are arguments that prove the command actually answers, run only
    when preflight is probing. ``--version`` is the usual one. Left empty, the
    check is presence on ``PATH`` and nothing more.
    """

    name: str
    purpose: str
    probe: tuple[str, ...] = ()
    setup_hint: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise CompositionError("an executable requirement needs a command name")
        if not self.purpose:
            raise CompositionError(
                f"executable {self.name!r} needs a purpose; it is what the person "
                "being asked to install it will read"
            )


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


@dataclass(frozen=True, slots=True)
class Notifier:
    """Somewhere a run's progress is reported to, and which signals it wants.

    Declared alongside the other requirements, and for the same reason: a
    notification path that is not configured fails at the moment it matters —
    when a gate opens and nobody is told — and a run that discovers that has
    already parked and is waiting for a person who does not know. Preflight
    refuses it instead.

    ``env`` is how the endpoint is named. A webhook URL is a credential: it is
    the whole authorisation to post as whatever it points at, so it is declared
    as a variable to read at run time and never written into the pipeline.
    ``required_env`` is the same property ``McpServer`` exposes, so preflight
    treats both the same way without knowing which it is holding.
    """

    name: str
    purpose: str
    signals: tuple[RunSignal, ...]
    kind: NotifierKind = NotifierKind.WEBHOOK
    env: tuple[EnvVar, ...] = ()
    setup_hint: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise CompositionError("a notifier needs a name")
        if not self.purpose:
            raise CompositionError(
                f"notifier {self.name!r} needs a purpose; it is what the person "
                "being asked to configure it will read"
            )
        if not self.signals:
            raise CompositionError(
                f"notifier {self.name!r} subscribes to nothing, so it would never fire. "
                "Name the signals it should report, or drop the declaration."
            )
        duplicated = sorted({s for s in self.signals if self.signals.count(s) > 1})
        if duplicated:
            raise CompositionError(
                f"notifier {self.name!r} names {[s.value for s in duplicated]} more than "
                "once; a signal is reported once or not at all"
            )

    @property
    def required_env(self) -> tuple[EnvVar, ...]:
        """Environment variables that must be set for this notifier to work."""
        return self.env

    def wants(self, signal: RunSignal) -> bool:
        """Whether this notifier asked to hear about ``signal``."""
        return signal in self.signals
