"""Interrupt a real run, then resume it through `ictus resume`.

Every run here is model-free and driven through the real CLI in a subprocess,
with stdin closed so the engine never reaches for a terminal. The interruption
is deterministic rather than timed: a step runs a command that does not exist
yet, which ends the run with a checkpoint at that step. Creating the command is
the "fix", and resuming must then finish without repeating what already ran —
or, inside a stage, must say beforehand exactly what will repeat.

Each step appends its name to a log, which is how "ran once" and "ran twice"
are told apart.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

PIPELINE = """
from ictus import END, InputPort, OutputPort, Pipeline, PortType, ScriptNode, Stage
from ictus.stdlib import succeed

LOG, TOOL = {log!r}, {tool!r}

def mark(p, node_id, label, output=None):
    script = f"echo {{label}} >> {{LOG}}" + (f"; printf '{{output}}'" if output else "")
    outs = (OutputPort("n", PortType.NUMBER),) if output else ()
    return p.add(ScriptNode(node_id=node_id, command="/bin/sh", args=("-c", script),
                            declared_outputs=outs))

def fixable(p):
    # Fails to start until TOOL exists: the deterministic interruption.
    return p.add(ScriptNode(node_id="fixable", command=TOOL, trusted_status=True))

stage = Stage(stage_id="inner")
a = mark(stage.body, "child_a", "child-a", '{{"n": 7}}')
b = mark(stage.body, "child_b", "child-b")
stage.body.set_entry(a)
if {in_stage!r}:
    f = fixable(stage.body)
    stage.body.route(a, f)
    stage.body.route(f, b)
else:
    stage.body.route(a, b)
stage.body.route(b, END)
stage.body.expose_output("n", a, "n")

longrun = Pipeline(pipeline_id="longrun")
first = mark(longrun, "first", "first", '{{"n": 1}}')
inner = stage.instantiate(longrun)
last = mark(longrun, "last", "last")
done = longrun.add(succeed(node_id="done", reason="finished",
                           inputs=(InputPort("n", PortType.NUMBER),),
                           result={{"first_n": first.ref("n")}}))
longrun.set_entry(first)
if {in_stage!r}:
    longrun.route(first, inner)
else:
    f = fixable(longrun)
    longrun.route(first, f)
    longrun.route(f, inner)
longrun.route(inner, last)
longrun.route(last, done)
longrun.feed(first, "n", done, "n")
"""

CONFIG = "provider: claude-agent-sdk\nstart_gate: false\ndashboard: false\n"


class Folder:
    """One pipeline folder, the log its steps write, and the command they wait on."""

    def __init__(self, tmp_path: Path, *, in_stage: bool) -> None:
        self.root = tmp_path / "longrun"
        self.root.mkdir()
        self.log = tmp_path / "steps.log"
        self.tool = tmp_path / "tool.sh"
        self.work = tmp_path / "work"
        self.work.mkdir()
        source = PIPELINE.format(log=str(self.log), tool=str(self.tool), in_stage=in_stage)
        (self.root / "pipeline.py").write_text(source)
        (self.root / "config.yaml").write_text(CONFIG)

    def ictus(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "ictus.cli", *args],
            cwd=self.work,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    def run(self) -> subprocess.CompletedProcess[str]:
        return self.ictus("run", str(self.root), "--skip-preflight", "--foreground", "--no-web")

    def resume(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.ictus("resume", str(self.root), "--foreground", "--no-web", *extra)

    def fix(self) -> None:
        self.tool.write_text("#!/bin/sh\nexit 0\n")
        self.tool.chmod(0o755)

    def steps(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []


def _result(done: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """The workflow's final JSON output, which the engine prints last."""
    start = done.stdout.rfind("{\n")
    assert start != -1, done.stdout + done.stderr
    parsed = json.loads(done.stdout[start:])
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture
def interrupted_at_root(tmp_path: Path) -> Folder:
    folder = Folder(tmp_path, in_stage=False)
    first = folder.run()
    assert first.returncode != 0, first.stdout + first.stderr
    assert folder.steps() == ["first"]
    return folder


@pytest.fixture
def interrupted_in_stage(tmp_path: Path) -> Folder:
    folder = Folder(tmp_path, in_stage=True)
    first = folder.run()
    assert first.returncode != 0, first.stdout + first.stderr
    assert folder.steps() == ["first", "child-a"]
    return folder


class TestResumingARun:
    def test_what_already_ran_does_not_run_again(self, interrupted_at_root: Folder) -> None:
        folder = interrupted_at_root
        folder.fix()
        resumed = folder.resume("--yes")
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert folder.steps() == ["first", "child-a", "child-b", "last"]
        assert _result(resumed) == {"first_n": 1}, "the first step's output survived"

    def test_it_says_where_it_picks_up_and_what_is_done(self, interrupted_at_root: Folder) -> None:
        interrupted_at_root.fix()
        resumed = interrupted_at_root.resume("--yes")
        assert "resumes at: fixable" in resumed.stdout
        assert "done: first" in resumed.stdout

    def test_it_runs_in_the_directory_the_run_used(
        self, interrupted_at_root: Folder, tmp_path: Path
    ) -> None:
        """Scripts resolve paths against it; resuming from elsewhere would move them."""
        interrupted_at_root.fix()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        resumed = subprocess.run(
            [
                sys.executable,
                "-m",
                "ictus.cli",
                "resume",
                str(interrupted_at_root.root),
                "--yes",
                "--foreground",
                "--no-web",
            ],
            cwd=elsewhere,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert f"in {interrupted_at_root.work}" in resumed.stdout

    def test_a_stage_is_reported_as_restarting_with_the_scripts_that_repeat(
        self, interrupted_in_stage: Folder
    ) -> None:
        folder = interrupted_in_stage
        folder.fix()
        resumed = folder.resume("--yes")
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert "resumes at: inner" in resumed.stdout
        assert "inner/child_a" in resumed.stdout
        assert "run again" in resumed.stdout
        assert folder.steps() == ["first", "child-a", "child-a", "child-b", "last"]

    def test_trace_reads_the_runs_own_event_log(self, interrupted_at_root: Folder) -> None:
        interrupted_at_root.fix()
        assert interrupted_at_root.resume("--yes").returncode == 0
        traced = interrupted_at_root.ictus("trace", str(interrupted_at_root.root))
        assert traced.returncode == 0, traced.stdout + traced.stderr
        assert "longrun" in traced.stdout


class TestWhatResumeRefuses:
    def test_a_build_that_changed_since_launch(self, interrupted_at_root: Folder) -> None:
        folder = interrupted_at_root
        folder.fix()
        emitted = folder.root / "build" / "inner.yaml"
        emitted.write_text(emitted.read_text() + "\n# edited\n")
        refused = folder.resume("--yes")
        assert refused.returncode != 0
        assert "inner.yaml (changed)" in refused.stdout + refused.stderr
        assert folder.steps() == ["first"], "nothing was launched"

    def test_a_source_that_no_longer_compiles_to_what_was_running(
        self, interrupted_at_root: Folder
    ) -> None:
        folder = interrupted_at_root
        folder.fix()
        source = folder.root / "pipeline.py"
        source.write_text(source.read_text().replace('"finished"', '"done differently"'))
        refused = folder.resume("--yes")
        assert refused.returncode != 0
        assert "no longer compiles" in refused.stdout + refused.stderr
        assert folder.steps() == ["first"]

    def test_being_asked_with_nobody_to_answer(self, interrupted_at_root: Folder) -> None:
        interrupted_at_root.fix()
        refused = interrupted_at_root.resume()
        assert refused.returncode != 0
        assert "--yes" in refused.stdout + refused.stderr
        assert interrupted_at_root.steps() == ["first"]

    def test_a_run_that_finished(self, interrupted_at_root: Folder) -> None:
        folder = interrupted_at_root
        folder.fix()
        assert folder.resume("--yes").returncode == 0
        again = folder.resume("--yes")
        assert again.returncode != 0
        assert "nothing to resume" in again.stdout + again.stderr

    def test_a_folder_that_was_never_run(self, tmp_path: Path) -> None:
        folder = Folder(tmp_path, in_stage=False)
        refused = folder.resume("--yes")
        assert refused.returncode != 0
        assert "no recorded run" in refused.stdout + refused.stderr


class TestStartingOverWhileOneCanBeResumed:
    def test_a_new_run_says_the_interrupted_one_is_still_there(
        self, interrupted_at_root: Folder
    ) -> None:
        again = interrupted_at_root.run()
        assert "--run" in again.stdout
        assert "can still be resumed" in again.stdout
