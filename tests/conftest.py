"""Shared fixtures.

The backend's own validator is the checker, not a schema model. For Conductor
specifically, ``WorkflowConfig.model_validate`` catches a dangling
``routes[].to`` but accepts a dangling ``options[].route`` — and every gate edge
ictus emits is an ``options[].route``, so the model alone would pass exactly the
graphs most worth checking.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ictus.interfaces.conductor import ConductorBackend

if TYPE_CHECKING:
    from collections.abc import Callable

    from ictus.graph.pipeline import Pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent

# Two roots hold folder-shaped pipelines, and the difference between them is
# whether a clone has them. `tests/fixtures/pipelines/` is committed and is the
# gate; `demo_work/pipelines/` is gitignored local work, checked as a bonus
# when it happens to be there. Keeping the gate in the first is what stopped a
# clone from running 36 fewer tests than the machine they were written on and
# calling that green.
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "pipelines"
LOCAL = REPO_ROOT / "demo_work" / "pipelines"


def pipeline_roots() -> list[Path]:
    """Every root holding `<name>/pipeline.py` folders, committed ones first."""
    assert FIXTURES.is_dir(), f"{FIXTURES} is the gate's own corpus and must be committed"
    return [FIXTURES, *(r for r in (LOCAL,) if r.is_dir())]


@pytest.fixture(scope="session")
def backend() -> ConductorBackend:
    """The backend under test.

    A hard failure rather than a skip when its CLI is missing: a run that cannot
    check its own output has not verified anything.
    """
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
