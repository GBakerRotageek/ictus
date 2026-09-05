"""CLI behaviour, including the failure modes that previously exited zero."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ictus.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

MINIMAL = """
from ictus import AgentNode, END, OutputPort, Pipeline, PortType
demo = Pipeline(pipeline_id="{pid}")
node = demo.add(AgentNode(node_id="only", prompt="hello",
                          declared_outputs=(OutputPort("v", PortType.STRING),)))
demo.route(node, END)
"""

BROKEN = """
from ictus import AgentNode, InputPort, Pipeline, PortType
demo = Pipeline(pipeline_id="broken")
demo.add(AgentNode(node_id="a", inputs=(InputPort("never_wired", PortType.STRING),),
                   prompt="x"))
"""


def _write(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body)


def test_emit_is_a_subcommand(tmp_path: Path) -> None:
    """`ictus emit <dir>` must parse; a one-command Typer app swallows the argument."""
    src, out = tmp_path / "pipelines", tmp_path / "out"
    _write(src, "demo", MINIMAL.format(pid="demo"))
    result = runner.invoke(app, ["emit", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "demo.yaml").is_file()


def test_emit_fails_when_nothing_was_emitted(tmp_path: Path) -> None:
    """Success on an empty run reported green while producing no artifacts."""
    src, out = tmp_path / "pipelines", tmp_path / "out"
    src.mkdir(parents=True)
    result = runner.invoke(app, ["emit", str(src), "--out", str(out)])
    assert result.exit_code == 1
    assert "no pipelines found" in result.output


def test_emit_prunes_yaml_no_pipeline_claims(tmp_path: Path) -> None:
    src, out = tmp_path / "pipelines", tmp_path / "out"
    _write(src, "demo", MINIMAL.format(pid="demo"))
    out.mkdir(parents=True)
    stale = out / "deleted-last-week.yaml"
    stale.write_text("workflow: {}\n")
    result = runner.invoke(app, ["emit", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert not stale.exists()


def test_emit_writes_nothing_when_the_lints_fail(tmp_path: Path) -> None:
    src, out = tmp_path / "pipelines", tmp_path / "out"
    _write(src, "broken", BROKEN)
    result = runner.invoke(app, ["emit", str(src), "--out", str(out)])
    assert result.exit_code == 1
    assert "never_wired" in result.output
    assert not list(out.glob("*.yaml")) if out.exists() else True


def test_emit_refuses_two_pipelines_claiming_one_filename(tmp_path: Path) -> None:
    """Last-write-wins silently deleted a pipeline from the output directory."""
    src, out = tmp_path / "pipelines", tmp_path / "out"
    _write(src, "first", MINIMAL.format(pid="clash"))
    _write(src, "second", MINIMAL.format(pid="clash"))
    result = runner.invoke(app, ["emit", str(src), "--out", str(out)])
    assert result.exit_code == 1
    assert "claimed by both" in result.output


def test_lint_reports_without_writing(tmp_path: Path) -> None:
    src = tmp_path / "pipelines"
    _write(src, "broken", BROKEN)
    result = runner.invoke(app, ["lint", str(src)])
    assert result.exit_code == 1
    assert "never_wired" in result.output


def test_validate_requires_emitted_yaml(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    assert "run `ictus emit` first" in result.output


def test_validate_accepts_what_emit_produced(tmp_path: Path) -> None:
    """The whole loop: author, emit, and have Conductor accept the result."""
    src, out = tmp_path / "pipelines", tmp_path / "out"
    _write(src, "demo", MINIMAL.format(pid="demo"))
    assert runner.invoke(app, ["emit", str(src), "--out", str(out)]).exit_code == 0
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 0, result.output


def test_run_names_the_available_workflows(tmp_path: Path) -> None:
    src, out = tmp_path / "pipelines", tmp_path / "out"
    _write(src, "demo", MINIMAL.format(pid="demo"))
    runner.invoke(app, ["emit", str(src), "--out", str(out)])
    result = runner.invoke(app, ["run", "typo", "--out", str(out)])
    assert result.exit_code == 1
    assert "available: demo" in result.output
