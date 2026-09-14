"""Where a run keeps what it needs to be resumed — somewhere that survives.

Left to itself the engine checkpoints into ``$TMPDIR``. On this machine that is
a tmpfs, so the interruption most worth surviving, a shutdown, deleted every
resume point. And it finds a checkpoint by workflow name across that whole
directory, so a resume could quietly pick up a run abandoned days earlier
instead of the one just interrupted.

So each ``ictus run`` gets a directory of its own under a persistent root, and
the engine is handed a subdirectory of it for its state. Beside that sits a
manifest of what was launched: which files ``build/`` held, where the work
happened. Resuming reads the manifest back, and refuses rather than continues
when what is on disk is no longer what was running.

Engine-neutral. What the engine keeps inside its directory, and whether a
checkpoint in it can be resumed, is the backend's to say.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ictus.errors import IctusError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ictus.runspec import PipelineFolder

__all__ = [
    "KEEP_FINISHED_RUNS",
    "STATE_ENV",
    "RunManifest",
    "RunRecord",
    "RunStateError",
    "changed_files",
    "fingerprint",
    "prune_runs",
    "runs_of",
    "start_run",
    "state_root",
]

STATE_ENV = "ICTUS_STATE_DIR"
"""Put run state here instead of ``$XDG_STATE_HOME/ictus``."""

MANIFEST = "run.json"
ENGINE_DIR = "engine"

#: How many runs that can no longer be resumed are kept per pipeline, for their
#: event logs. A run that can still be resumed is never counted or removed.
KEEP_FINISHED_RUNS = 20


class RunStateError(IctusError):
    """A run's recorded state is missing, malformed, or no longer matches."""


def state_root() -> Path:
    """The persistent directory every pipeline's runs live under."""
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "ictus"


def fingerprint(build: Path) -> dict[str, str]:
    """A digest of every emitted document, by filename."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(build.glob("*.yaml"))
    }


def changed_files(recorded: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """What differs between two fingerprints, each file named with how."""
    changes = [f"{name} (removed)" for name in recorded if name not in current]
    changes += [f"{name} (added)" for name in current if name not in recorded]
    changes += [
        f"{name} (changed)"
        for name in recorded
        if name in current and recorded[name] != current[name]
    ]
    return sorted(changes)


@dataclass(frozen=True, slots=True)
class RunManifest:
    """What was launched, recorded before the engine starts."""

    pipeline_id: str
    folder: Path
    workflow: Path
    """The root document the engine was given."""
    working_dir: Path
    started_at: str
    fingerprint: Mapping[str, str]
    """Every document in ``build/`` at launch. Resuming against anything else
    continues a different workflow from a checkpoint taken in this one."""

    def to_json(self) -> dict[str, object]:
        return {
            "pipeline_id": self.pipeline_id,
            "folder": str(self.folder),
            "workflow": str(self.workflow),
            "working_dir": str(self.working_dir),
            "started_at": self.started_at,
            "fingerprint": dict(self.fingerprint),
        }

    @classmethod
    def from_json(cls, data: object, *, source: str) -> RunManifest:
        """Parse a manifest, naming the file and the field that is wrong."""
        if not isinstance(data, dict):
            raise RunStateError(f"{source}: expected an object, got {type(data).__name__}")
        text: dict[str, str] = {}
        for key in ("pipeline_id", "folder", "workflow", "working_dir", "started_at"):
            value = data.get(key)
            if not isinstance(value, str) or not value:
                raise RunStateError(f"{source}: {key!r} must be a non-empty string")
            text[key] = value
        digests = data.get("fingerprint")
        if not isinstance(digests, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in digests.items()
        ):
            raise RunStateError(f"{source}: 'fingerprint' must map filenames to digests")
        return cls(
            pipeline_id=text["pipeline_id"],
            folder=Path(text["folder"]),
            workflow=Path(text["workflow"]),
            working_dir=Path(text["working_dir"]),
            started_at=text["started_at"],
            fingerprint=dict(digests),
        )


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One launched run: its directory and what it recorded."""

    directory: Path
    manifest: RunManifest

    @property
    def name(self) -> str:
        """How a person picks this run out: its start stamp."""
        return self.directory.name

    @property
    def engine_dir(self) -> Path:
        """What the engine is given to keep its own state in."""
        return self.directory / ENGINE_DIR


def _pipeline_dir(folder: PipelineFolder) -> Path:
    """One pipeline's runs. The path's digest keeps same-named folders apart."""
    digest = hashlib.sha256(str(folder.root.resolve()).encode()).hexdigest()[:8]
    return state_root() / f"{folder.name}-{digest}"


def start_run(
    folder: PipelineFolder, *, pipeline_id: str, workflow: Path, working_dir: Path
) -> RunRecord:
    """Record a run about to launch, and create the directory its engine will use."""
    now = datetime.now(UTC)
    # Sortable by name, so "newest" needs no clock beyond this one; the suffix
    # keeps two launches in one second apart.
    stamp = f"{now:%Y%m%d-%H%M%S}-{now:%f}-{secrets.token_hex(2)}"
    directory = _pipeline_dir(folder) / stamp
    manifest = RunManifest(
        pipeline_id=pipeline_id,
        folder=folder.root.resolve(),
        workflow=workflow.resolve(),
        working_dir=working_dir.resolve(),
        started_at=now.isoformat(),
        fingerprint=fingerprint(folder.build),
    )
    (directory / ENGINE_DIR).mkdir(parents=True)
    (directory / MANIFEST).write_text(json.dumps(manifest.to_json(), indent=2), encoding="utf-8")
    return RunRecord(directory=directory, manifest=manifest)


def runs_of(folder: PipelineFolder) -> list[RunRecord]:
    """Every recorded run of this folder, newest first."""
    root = _pipeline_dir(folder)
    if not root.is_dir():
        return []
    records: list[RunRecord] = []
    for directory in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        manifest = directory / MANIFEST
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RunStateError(f"{manifest}: not valid JSON ({exc})") from exc
        records.append(
            RunRecord(
                directory=directory, manifest=RunManifest.from_json(data, source=str(manifest))
            )
        )
    return records


def prune_runs(
    folder: PipelineFolder,
    *,
    keep: int = KEEP_FINISHED_RUNS,
    resumable: Callable[[RunRecord], bool],
) -> list[RunRecord]:
    """Remove the oldest runs that cannot be resumed, beyond ``keep`` of them.

    ``resumable`` is asked of the backend, which is the only thing that knows
    what its own directory holds. A run it says can still be resumed is never
    removed and never counted: deleting a resume point to save disk is exactly
    the loss this module exists to prevent.
    """
    finished = [run for run in runs_of(folder) if not resumable(run)]
    removed = finished[keep:]
    for run in removed:
        shutil.rmtree(run.directory)
    return removed
