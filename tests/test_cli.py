"""CLI behaviour, including the failure modes that previously exited zero."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ictus.cli import app
from ictus.config import MINIMAL as MINIMAL_CONFIG

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

NEEDS_INPUT = """
from ictus import AgentNode, END, InputPort, OutputPort, Pipeline, PortType, tpl
demo = Pipeline(pipeline_id="demo")
subject = demo.declare_input("subject", PortType.STRING, prose=True)
node = demo.add(AgentNode(node_id="a", inputs=(InputPort("s", PortType.STRING),),
                          prompt=tpl("do ", subject.ref()),
                          declared_outputs=(OutputPort("v", PortType.STRING),)))
demo.set_entry(node)
demo.connect_input(subject, node, "s")
demo.route(node, END)
"""

BROKEN = """
from ictus import AgentNode, InputPort, Pipeline, PortType
demo = Pipeline(pipeline_id="broken")
demo.add(AgentNode(node_id="a", inputs=(InputPort("never_wired", PortType.STRING),),
                   prompt="x"))
"""


def _write(directory: Path, name: str, body: str, *, config: str = MINIMAL_CONFIG) -> Path:
    """Lay out one pipeline folder: pipeline.py plus the config every folder needs."""
    folder = directory / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pipeline.py").write_text(body)
    (folder / "config.yaml").write_text(config)
    return folder


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
    assert "no pipeline folders" in result.output


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


def test_run_refuses_a_folder_that_is_not_a_pipeline(tmp_path: Path) -> None:
    bare = tmp_path / "not-a-pipeline"
    bare.mkdir()
    result = runner.invoke(app, ["run", str(bare)])
    assert result.exit_code == 1
    assert "pipeline.py" in result.output


def test_run_says_what_the_input_file_is_missing(tmp_path: Path) -> None:
    """A required input with nowhere to come from should name the file to put it in."""
    src = tmp_path / "pipelines"
    folder = _write(src, "demo", NEEDS_INPUT)
    result = runner.invoke(app, ["run", str(folder), "--skip-preflight"])
    assert result.exit_code == 1
    assert "requires ['subject']" in result.output
    assert "input.md" in result.output


def test_init_scaffolds_a_folder_that_actually_works(tmp_path: Path) -> None:
    """A scaffold that does not lint and emit is a trap, not a starting point."""
    folder = tmp_path / "fresh"
    assert runner.invoke(app, ["init", str(folder)]).exit_code == 0
    assert (folder / "config.yaml").is_file()
    assert (folder / "input.md").is_file()
    assert runner.invoke(app, ["lint", str(folder)]).exit_code == 0
    assert runner.invoke(app, ["emit", str(folder)]).exit_code == 0
    assert (folder / "build").is_dir()


def test_init_keeps_what_is_already_there(tmp_path: Path) -> None:
    folder = tmp_path / "fresh"
    folder.mkdir()
    (folder / "config.yaml").write_text("provider: mine\n")
    runner.invoke(app, ["init", str(folder)])
    assert (folder / "config.yaml").read_text() == "provider: mine\n"


def test_validate_does_not_mistake_the_config_for_a_workflow(tmp_path: Path) -> None:
    """`config.yaml` sits in the folder; globbing *.yaml handed it to the loader."""
    src = tmp_path / "pipelines"
    folder = _write(src, "demo", MINIMAL.format(pid="demo"))
    assert runner.invoke(app, ["emit", str(folder)]).exit_code == 0
    result = runner.invoke(app, ["validate", str(folder)])
    assert result.exit_code == 0, result.output
    assert "config.yaml" not in result.output


def test_a_folder_without_a_config_says_what_to_write(tmp_path: Path) -> None:
    src = tmp_path / "pipelines"
    folder = _write(src, "demo", MINIMAL.format(pid="demo"))
    (folder / "config.yaml").unlink()
    result = runner.invoke(app, ["lint", str(folder)])
    assert result.exit_code == 1
    assert "provider: claude-agent-sdk" in result.output


def test_the_start_gate_is_on_by_default(tmp_path: Path) -> None:
    src = tmp_path / "pipelines"
    folder = _write(src, "demo", MINIMAL.format(pid="demo"))
    runner.invoke(app, ["emit", str(folder)])
    emitted = (folder / "build" / "demo.yaml").read_text()
    assert "entry_point: confirm_start" in emitted


def test_a_pipeline_can_turn_the_start_gate_off(tmp_path: Path) -> None:
    src = tmp_path / "pipelines"
    folder = _write(
        src,
        "demo",
        MINIMAL.format(pid="demo"),
        config="provider: claude-agent-sdk\nstart_gate: false\n",
    )
    runner.invoke(app, ["emit", str(folder)])
    emitted = (folder / "build" / "demo.yaml").read_text()
    assert "confirm_start" not in emitted


def test_a_misspelled_provider_is_caught_where_it_is_written(tmp_path: Path) -> None:
    """Otherwise it surfaces from Conductor's loader, after the whole thing compiles."""
    src = tmp_path / "pipelines"
    folder = _write(src, "demo", MINIMAL.format(pid="demo"), config="provider: claud\n")
    result = runner.invoke(app, ["lint", str(folder)])
    assert result.exit_code == 1
    assert "is not a provider" in result.output
    assert "claude-agent-sdk" in result.output
