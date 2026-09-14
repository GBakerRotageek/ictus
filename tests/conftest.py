"""Shared fixtures.

The backend's own validator is the checker, not a schema model. For Conductor
specifically, ``WorkflowConfig.model_validate`` catches a dangling
``routes[].to`` but accepts a dangling ``options[].route`` — and every gate edge
ictus emits is an ``options[].route``, so the model alone would pass exactly the
graphs most worth checking.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

from ictus.graph.node import NodeKind
from ictus.interfaces.conductor import ConductorBackend
from ictus.runstate import STATE_ENV

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


@pytest.fixture(autouse=True)
def _isolated_run_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Every test's run state goes somewhere of its own, never the user's.

    Not hypothetical: before this existed, the first run of the run-state tests
    wrote seven pipelines' worth of runs into `~/.local/state/ictus`.
    """
    root = tmp_path_factory.mktemp("state")
    monkeypatch.setenv(STATE_ENV, str(root))
    return root


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


@dataclass(frozen=True, slots=True)
class Execution:
    """What one real run of a compiled pipeline did."""

    returncode: int
    output: dict[str, object] | None
    """The workflow's final output, or ``None`` when the run ended without one."""
    events: list[dict[str, object]]
    stderr: str

    def count(self, kind: str, step: str) -> int:
        """How many ``kind`` events the engine recorded for ``step``."""
        seen = 0
        for event in self.events:
            data = event.get("data")
            if (
                event.get("type") == kind
                and isinstance(data, dict)
                and data.get("agent_name") == step
            ):
                seen += 1
        return seen

    def routes(self) -> list[str]:
        """Every route the engine took, in order, by destination."""
        taken: list[str] = []
        for event in self.events:
            data = event.get("data")
            if event.get("type") == "route_taken" and isinstance(data, dict):
                taken.append(str(data.get("to_agent")))
        return taken

    def failures(self, step: str) -> list[str]:
        """Why the engine refused ``step``, each time it did.

        The engine's own message and whatever the step wrote to stderr, joined:
        a schema the step did not satisfy is named in the first, and a reason
        the step gave for itself — a reporter that could not start the command —
        only in the second.
        """
        said: list[str] = []
        for event in self.events:
            data = event.get("data")
            if (
                event.get("type") == "script_failed"
                and isinstance(data, dict)
                and data.get("agent_name") == step
            ):
                said.append(f"{data.get('message', '')}\n{data.get('stderr', '')}")
        return said


class Executes(Protocol):
    """The ``executes`` fixture: run a pipeline, with typed workflow inputs."""

    def __call__(self, pipeline: Pipeline, /, **inputs: object) -> Execution: ...


def _billable(pipeline: Pipeline) -> list[str]:
    """Every model call in a pipeline and the stages it hosts."""
    found = [
        f"{pipeline.pipeline_id}/{n.node_id}" for n in pipeline.nodes if n.kind is NodeKind.LLM_CALL
    ]
    for child in pipeline.children.values():
        found += _billable(child)
    return found


@pytest.fixture
def executes(backend: ConductorBackend, tmp_path: Path) -> Executes:
    """Run a pipeline through the engine for real, and read back what happened.

    Loader acceptance says a workflow *says* the right thing; only a run says it
    *does*. The false-readiness poll — a check that printed ``{"exit_code": 0}``
    and exited 1 — passed every structural test and ``conductor validate``, and
    came back ``ready`` the first time it was executed.

    Refuses a pipeline containing a model call. These runs are meant to be free
    and deterministic, and an agent node added to one by accident would be
    neither — a charge on every ``make soundcheck``.

    ``TMPDIR`` is pointed at the test's own directory, so the event log and
    checkpoints land somewhere nothing else writes.
    """

    def _run(pipeline: Pipeline, /, **inputs: object) -> Execution:
        billable = _billable(pipeline)
        assert not billable, f"an execution test must not call a model: {billable}"
        for document in backend.compile(pipeline):
            (tmp_path / document.filename).write_text(document.content)
        scratch = tmp_path / "engine"
        scratch.mkdir(exist_ok=True)
        binary = shutil.which("conductor")
        assert binary is not None  # the `backend` fixture already failed the run otherwise
        command = [
            binary,
            "--silent",
            "run",
            str(tmp_path / f"{pipeline.pipeline_id}.yaml"),
            "--no-interactive",
        ]
        for name, value in inputs.items():
            # Not `-i`: the CLI guesses types for those (cli/run.py, coerce_value),
            # so `-i brief=true` starts the run with a boolean and every test of a
            # later boundary would be measuring the launch instead.
            command += ["--input-json", f"{name}={json.dumps(value)}"]
        done = subprocess.run(
            command,
            cwd=tmp_path,
            env={**os.environ, "TMPDIR": str(scratch)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        try:
            parsed = json.loads(done.stdout)
        except json.JSONDecodeError:
            parsed = None
        events = [
            json.loads(line)
            for log in sorted((scratch / "conductor").glob("*.events.jsonl"))
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return Execution(
            returncode=done.returncode,
            output=parsed if isinstance(parsed, dict) else None,
            events=events,
            stderr=done.stderr,
        )

    return _run
