"""Shared fixtures.

The backend's own validator is the checker, not a schema model:
``WorkflowConfig.model_validate`` accepts a dangling ``options[].route``, which
is how every gate edge ictus emits is spelled.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from ictus.interfaces.conductor import ConductorBackend

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ictus.graph.pipeline import Pipeline


@pytest.fixture(scope="session")
def backend() -> ConductorBackend:
    """The backend under test. Fails hard rather than skipping when its CLI is absent."""
    if shutil.which("conductor") is None:
        pytest.fail("conductor is not on PATH; the conformance suite cannot verify output")
    return ConductorBackend()


@pytest.fixture
def validates(backend: ConductorBackend, tmp_path: Path) -> Callable[[Pipeline], None]:
    """Assert a pipeline and every stage it contains pass the backend's validator."""

    def _check(pipeline: Pipeline) -> None:
        for document in backend.compile(pipeline):
            (tmp_path / document.filename).write_text(document.content)
        entry = tmp_path / f"{pipeline.pipeline_id}.yaml"
        results = backend.validate([entry])
        bad = [r for r in results if not r.ok]
        assert not bad, (
            f"{backend.capabilities().name} rejected {pipeline.pipeline_id}:\n"
            f"{bad[0].detail}\n--- emitted ---\n{entry.read_text()}"
        )

    return _check
