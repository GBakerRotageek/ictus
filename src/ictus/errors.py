"""Errors raised during pipeline composition and emission.

Every error names what was being attempted and where, so a failure at the
composition boundary is actionable without reading the traceback.
"""

from __future__ import annotations


class IctusError(Exception):
    """Base class for every error ictus raises."""


class CompositionError(IctusError):
    """Raised when a graph is assembled in a way Conductor cannot express.

    Raised at the point the invalid state enters the graph — ``connect``,
    ``branch``, ``add_node`` — never deferred to emission.
    """


class PortTypeError(CompositionError):
    """Raised when an output port is wired to an incompatible input port."""


class UnknownPortError(CompositionError):
    """Raised when a port name does not exist on the referenced node."""


class EmitError(IctusError):
    """Raised when a graph cannot be lowered to a Conductor workflow."""


class LintError(IctusError):
    """Raised when the composition lints reject a graph."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        body = "\n".join(f"  - {v}" for v in violations)
        super().__init__(f"{len(violations)} lint violation(s):\n{body}")
