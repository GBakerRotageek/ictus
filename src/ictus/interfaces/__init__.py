"""The boundary between an ictus graph and whatever executes it.

A ``Backend`` is the only thing permitted to know an engine's spelling: its
field names, its template dialect, its iteration accounting, its CLI. Everything
above this line describes *what* a pipeline is; everything below describes how
one engine wants to hear it.

The point is not that a second backend is planned. It is that the boundary makes
the coupling countable: if a Conductor field name appears above this line, that
is a defect with a name, rather than a slow drift nobody can see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ictus.graph.signals import RunSignal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ictus.graph.node import NodeKind
    from ictus.graph.pipeline import Pipeline

__all__ = [
    "ENDED",
    "Backend",
    "Capabilities",
    "Document",
    "PreflightIssue",
    "SignalEvent",
    "UnsupportedFeatureError",
    "ValidationResult",
]


#: After one of these the run is over, and whatever is attached must let go.
ENDED = frozenset({RunSignal.RUN_FINISHED, RunSignal.RUN_FAILED})


@dataclass(frozen=True, slots=True)
class SignalEvent:
    """One reportable moment, with the run it happened in.

    Lives on the boundary rather than inside a backend because both sides need
    it and neither owns it: an engine produces these, and whatever reports them
    onward consumes them without knowing which engine ran.
    """

    signal: RunSignal
    run_id: str
    workflow: str
    at: float
    event_type: str
    """The engine's own name for it, kept so a report can say what it saw."""

    data: dict[str, object] = field(default_factory=dict)

    @property
    def ends_the_run(self) -> bool:
        """Whether nothing further will arrive for this run."""
        return self.signal in ENDED


@dataclass(frozen=True, slots=True)
class Document:
    """One rendered file, ready to be written.

    Rendered text rather than a data structure: serialization format is the
    engine's business, and a backend that emitted JSON or a Python call graph
    should not have to pretend it produces a mapping.
    """

    filename: str
    content: str


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What an engine can actually express.

    Declared rather than assumed, so a graph using a feature the target cannot
    run is refused while it is being composed — not discovered on a live run.
    """

    name: str
    kinds: frozenset[NodeKind]
    providers: frozenset[str] = frozenset()
    """Who can answer a model call. Empty means the engine does not constrain it.

    Declared here so a misspelled provider is refused where it is written rather
    than by the engine's own loader, which only sees it once the whole pipeline
    has been compiled.
    """

    remembering_providers: frozenset[str] = frozenset()
    """Providers whose steps can resume a session rather than starting cold.

    A step that starts cold has read nothing, whatever it read last time round
    the loop. Not every provider can carry a conversation forward, and one that
    cannot rejects the request rather than quietly ignoring it, so which ones
    can is worth knowing before the run.
    """

    tool_allowlists: bool = False
    """Whether a step can name *which* tools it may use.

    Three states, and only the middle one is universal: omitting a list means
    "whatever the engine gives a step by default", an empty list means "none",
    and a non-empty list means "exactly these". An engine that cannot translate
    the third has to say so, because the alternative is a run that dies partway
    through on a list ictus was happy to emit.
    """

    conditional_routes: bool = True
    cycles: bool = True
    sub_graphs: bool = True

    signals: frozenset[RunSignal] = frozenset()
    """Which moments of a run this engine can actually report.

    A pipeline subscribing to one that is absent is refused while it is being
    written, for the same reason an unsupported ``NodeKind`` is: the alternative
    is a notifier that is configured, passes preflight, and silently never fires
    — which looks exactly like a quiet run.

    Empty means the engine reports nothing, so any subscription is refused.
    """

    notes: str = ""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The engine's own opinion of a compiled document."""

    path: Path
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    """Something the environment must provide before a pipeline can run.

    ``remedy`` is the point: an issue nobody can act on is just a failure. It
    should say what to type or where to click, not restate the problem.
    """

    requirement: str
    problem: str
    remedy: str
    blocking: bool = True


class UnsupportedFeatureError(Exception):
    """Raised when a graph needs something the chosen backend cannot express."""

    def __init__(self, backend: str, missing: Sequence[str]) -> None:
        self.backend = backend
        self.missing = list(missing)
        body = "\n".join(f"  - {m}" for m in missing)
        super().__init__(f"backend {backend!r} cannot express this pipeline:\n{body}")


@runtime_checkable
class Backend(Protocol):
    """An execution target for an ictus pipeline."""

    def capabilities(self) -> Capabilities:
        """What this backend can express."""
        ...

    def compile(self, pipeline: Pipeline) -> list[Document]:
        """Render ``pipeline`` and every stage it contains."""
        ...

    def lint(self, pipeline: Pipeline) -> list[str]:
        """Engine-specific problems the generic composition rules cannot know.

        Separate from ``ictus.lint`` because rules like "a route list with no
        catch-all raises at run time" are assertions about one engine's runtime,
        not about graphs in general.
        """
        ...

    def validate(self, paths: Sequence[Path]) -> list[ValidationResult]:
        """Ask the engine itself whether the compiled documents load."""
        ...

    def preflight(self, pipeline: Pipeline, *, probe: bool) -> list[PreflightIssue]:
        """Check the environment can satisfy what the pipeline declares.

        Separate from ``validate``: a workflow can be perfectly well-formed and
        still be unrunnable here because a token is missing or a server is not
        installed. ``probe`` additionally opens each declared connection, which
        catches a wrong credential that an offline check cannot.
        """
        ...

    def run(
        self,
        path: Path,
        *,
        inputs: Mapping[str, str],
        dashboard: bool,
        workspace_instructions: bool = True,
        working_dir: Path | None = None,
    ) -> int:
        """Execute a compiled document in ``working_dir``. Returns the exit code.

        ``workspace_instructions`` asks the engine to read the target project's
        own instruction files. What an engine discovers, and whether it can at
        all, is its business; that a step should arrive knowing what the project
        says about itself is not.
        """
        ...
