"""Where a run keeps what it needs to be resumed, and how that is read back.

The engine's own default is `$TMPDIR`, which on this machine is a tmpfs: a
shutdown — the interruption most worth surviving — deleted every checkpoint.
And checkpoints were found by workflow name across the whole of that directory,
so a resume could pick up a run abandoned days earlier. Each run now gets its
own persistent directory, and these tests pin what that directory promises.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ictus.errors import IctusError
from ictus.runspec import PipelineFolder
from ictus.runstate import (
    STATE_ENV,
    RunManifest,
    RunRecord,
    changed_files,
    fingerprint,
    prune_runs,
    runs_of,
    start_run,
    state_root,
)

if TYPE_CHECKING:
    from pathlib import Path


def _folder(tmp_path: Path, name: str = "demo") -> PipelineFolder:
    root = tmp_path / "pipelines" / name
    (root / "build").mkdir(parents=True)
    (root / "pipeline.py").write_text("")
    (root / "build" / f"{name}.yaml").write_text("workflow: {}\n")
    return PipelineFolder(root)


def _start(folder: PipelineFolder, tmp_path: Path) -> RunRecord:
    return start_run(
        folder,
        pipeline_id=folder.name,
        workflow=folder.build / f"{folder.name}.yaml",
        working_dir=tmp_path,
    )


class TestWhereStateLives:
    def test_the_suite_never_writes_to_the_real_state_root(self, tmp_path: Path) -> None:
        """The autouse fixture is what keeps a test run out of the user's home."""
        assert str(state_root()).startswith(str(tmp_path.parent.parent))

    def test_the_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STATE_ENV, str(tmp_path / "state"))
        assert state_root() == tmp_path / "state"

    def test_xdg_state_home_is_the_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persistent by definition, unlike the temp directory the engine chose."""
        monkeypatch.delenv(STATE_ENV, raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        assert state_root() == tmp_path / "xdg" / "ictus"

    def test_two_folders_with_one_name_do_not_share_runs(self, tmp_path: Path) -> None:
        first = _folder(tmp_path / "a")
        second = _folder(tmp_path / "b")
        _start(first, tmp_path)
        assert runs_of(second) == []
        assert len(runs_of(first)) == 1


class TestARunRecord:
    def test_the_manifest_round_trips(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path)
        started = _start(folder, tmp_path)
        (found,) = runs_of(folder)
        assert found.manifest == started.manifest
        assert found.manifest.working_dir == tmp_path
        assert found.manifest.fingerprint == fingerprint(folder.build)

    def test_the_newest_run_comes_first(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path)
        older = _start(folder, tmp_path)
        newer = _start(folder, tmp_path)
        assert [r.directory for r in runs_of(folder)] == [
            newer.directory,
            older.directory,
        ]

    def test_the_engine_gets_a_directory_of_its_own(self, tmp_path: Path) -> None:
        run = _start(_folder(tmp_path), tmp_path)
        assert run.engine_dir.is_dir()
        assert run.engine_dir.parent == run.directory

    def test_a_malformed_manifest_names_the_file_and_the_field(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path)
        run = _start(folder, tmp_path)
        manifest = run.directory / "run.json"
        data = json.loads(manifest.read_text())
        del data["working_dir"]
        manifest.write_text(json.dumps(data))
        with pytest.raises(IctusError, match=r"run\.json.*working_dir"):
            runs_of(folder)

    def test_a_manifest_parses_at_the_edge(self) -> None:
        with pytest.raises(IctusError, match="fingerprint"):
            RunManifest.from_json(
                {
                    "pipeline_id": "demo",
                    "folder": "/x",
                    "workflow": "/x/build/demo.yaml",
                    "working_dir": "/x",
                    "started_at": "2026-09-14T00:00:00+00:00",
                    "fingerprint": ["not", "a", "mapping"],
                },
                source="run.json",
            )


class TestWhatChangedSinceLaunch:
    def test_an_unchanged_build_reports_nothing(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path)
        assert changed_files(fingerprint(folder.build), fingerprint(folder.build)) == []

    def test_an_edited_added_or_removed_file_is_named(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path)
        before = fingerprint(folder.build)
        (folder.build / "demo.yaml").write_text("workflow: {changed: true}\n")
        (folder.build / "child.yaml").write_text("workflow: {}\n")
        after = fingerprint(folder.build)
        assert changed_files(before, after) == ["child.yaml (added)", "demo.yaml (changed)"]
        (folder.build / "demo.yaml").unlink()
        assert "demo.yaml (removed)" in changed_files(before, fingerprint(folder.build))


class TestPruning:
    def test_finished_runs_beyond_the_limit_are_removed_oldest_first(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path)
        runs = [_start(folder, tmp_path) for _ in range(4)]
        removed = prune_runs(folder, keep=2, resumable=lambda _run: False)
        assert sorted(r.directory for r in removed) == sorted(r.directory for r in runs[:2])
        assert len(runs_of(folder)) == 2

    def test_a_run_that_can_still_be_resumed_is_never_removed(self, tmp_path: Path) -> None:
        """Deleting a resume point to save disk is the loss this exists to prevent."""
        folder = _folder(tmp_path)
        runs = [_start(folder, tmp_path) for _ in range(4)]
        keep_me = runs[0].directory
        prune_runs(folder, keep=1, resumable=lambda run: run.directory == keep_me)
        assert keep_me in {r.directory for r in runs_of(folder)}
