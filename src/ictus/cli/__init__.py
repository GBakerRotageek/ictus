"""The ``ictus`` command line.

    building    emit, lint, preflight, validate, init — before anything runs
    running     run — compile, check, then launch what was compiled
    watching    trace, watch — what a run did, and what one is doing
    catalogue   stdlib — what is already built, touching no pipeline

``app.py`` holds the Typer object and the helpers all three use. A command
module registers itself by being imported here.
"""

from __future__ import annotations

from ictus.cli.app import app

# Imported for the side effect of registering their commands on `app`.
from ictus.cli import building, catalogue, running, watching  # noqa: F401  # isort: skip

__all__ = ["app"]
