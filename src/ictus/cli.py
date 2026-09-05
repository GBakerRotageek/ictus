"""The ``ictus`` command line."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ictus.errors import IctusError
from ictus.graph.pipeline import Pipeline
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline

if TYPE_CHECKING:
    from ictus.interfaces import PreflightIssue

app = typer.Typer(
    help="Typed composition for Conductor workflows.",
    no_args_is_help=True,
    add_completion=False,
)


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _load_pipelines(pipelines_dir: Path) -> list[Pipeline]:
    """Import every module in ``pipelines_dir`` and collect the top-level pipelines.

    A pipeline that is the body of a stage is dropped: it is emitted as part of
    its parent, and emitting it again at top level would produce a second file
    claiming the same name.
    """
    if not pipelines_dir.is_dir():
        _fail(f"{pipelines_dir} is not a directory")

    found: list[Pipeline] = []
    for module_path in sorted(pipelines_dir.glob("*.py")):
        if module_path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
        if spec is None or spec.loader is None:
            _fail(f"could not load {module_path}")
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except IctusError as exc:
            _fail(f"{module_path}: {exc}")
        except Exception as exc:
            _fail(f"{module_path}: {type(exc).__name__}: {exc}")
        for name in sorted(dir(module)):
            value = getattr(module, name)
            if isinstance(value, Pipeline) and not any(value is p for p in found):
                found.append(value)

    nested = {id(child) for p in found for child in _descendants(p)}
    return [p for p in found if id(p) not in nested]


def _descendants(pipeline: Pipeline) -> list[Pipeline]:
    out: list[Pipeline] = []
    for child in pipeline.children.values():
        out.append(child)
        out.extend(_descendants(child))
    return out


# One line to change when a second backend exists; nothing else in the CLI
# names an engine.
BACKEND = conductor


def _write(pipeline: Pipeline, out: Path) -> list[Path]:
    """Compile and write every document a pipeline produces.

    Each file goes to a temporary sibling and is renamed, so a failure part way
    through cannot leave a truncated document that still parses.
    """
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for document in BACKEND.compile(pipeline):
        target = out / document.filename
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(document.content, encoding="utf-8")
        tmp.replace(target)
        written.append(target)
    return written


@app.command()
def emit(
    pipelines_dir: Annotated[Path, typer.Argument(help="Directory of pipeline modules")] = Path(
        "pipelines"
    ),
    out: Annotated[Path, typer.Option(help="Directory to write YAML into")] = Path(
        "build/pipelines"
    ),
    prune: Annotated[
        bool, typer.Option(help="Delete YAML in --out that no pipeline claims")
    ] = True,
) -> None:
    """Compile pipeline modules to Conductor YAML."""
    pipelines = _load_pipelines(pipelines_dir)
    if not pipelines:
        _fail(f"no pipelines found in {pipelines_dir} (nothing was emitted)")

    seen: dict[str, str] = {}
    problems: list[str] = []
    for pipeline in pipelines:
        for document in BACKEND.compile(pipeline):
            owner = seen.get(document.filename)
            if owner is not None:
                problems.append(
                    f"{document.filename} is claimed by both {owner!r} and {pipeline.pipeline_id!r}"
                )
            seen[document.filename] = pipeline.pipeline_id
        problems.extend(lint_pipeline(pipeline, backend=BACKEND))
    if problems:
        for problem in problems:
            typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
        _fail(f"{len(problems)} composition problem(s); nothing was written")

    written: set[Path] = set()
    for pipeline in pipelines:
        try:
            written.update(_write(pipeline, out))
        except IctusError as exc:
            _fail(f"{pipeline.pipeline_id}: {exc}")

    if prune:
        for stale in sorted(set(out.glob("*.yaml")) - written):
            stale.unlink()
            typer.echo(f"pruned {stale}")

    for path in sorted(written):
        typer.echo(f"emitted {path}")
    typer.secho(
        f"{len(written)} workflow(s) from {len(pipelines)} pipeline(s)", fg=typer.colors.GREEN
    )


@app.command()
def lint(
    pipelines_dir: Annotated[Path, typer.Argument(help="Directory of pipeline modules")] = Path(
        "pipelines"
    ),
) -> None:
    """Run the composition lints without writing anything."""
    pipelines = _load_pipelines(pipelines_dir)
    if not pipelines:
        _fail(f"no pipelines found in {pipelines_dir}")
    problems = [p for pipeline in pipelines for p in lint_pipeline(pipeline, backend=BACKEND)]
    if problems:
        for problem in problems:
            typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
        _fail(f"{len(problems)} composition problem(s)")
    typer.secho(f"{len(pipelines)} pipeline(s) clean", fg=typer.colors.GREEN)


def _report_preflight(issues: list[PreflightIssue], *, probed: bool) -> None:
    """Print what the environment is missing, and what to do about it."""
    for issue in issues:
        marker = "BLOCK" if issue.blocking else "warn "
        colour = typer.colors.RED if issue.blocking else typer.colors.YELLOW
        typer.secho(f"{marker} {issue.requirement}: {issue.problem}", fg=colour, err=True)
        typer.secho(f"      fix: {issue.remedy}", fg=typer.colors.CYAN, err=True)
    if not issues:
        depth = "checked and probed" if probed else "checked (offline only)"
        typer.secho(f"preflight clean — requirements {depth}", fg=typer.colors.GREEN)


@app.command()
def preflight(
    pipelines_dir: Annotated[Path, typer.Argument(help="Directory of pipeline modules")] = Path(
        "pipelines"
    ),
    probe: Annotated[
        bool, typer.Option(help="Open each declared connection, not just check it is configured")
    ] = True,
) -> None:
    """Check this environment can supply what the pipelines declare.

    Separate from `validate`: a workflow can be perfectly well-formed and still
    be unrunnable here because a server is not installed or a token is unset.
    """
    pipelines = _load_pipelines(pipelines_dir)
    if not pipelines:
        _fail(f"no pipelines found in {pipelines_dir}")
    issues: list[PreflightIssue] = []
    for pipeline in pipelines:
        declared = pipeline.all_mcp_servers()
        typer.echo(f"{pipeline.pipeline_id}: {len(declared)} requirement(s) declared")
        for server in declared:
            typer.echo(f"  - mcp:{server.name} — {server.purpose}")
        issues.extend(BACKEND.preflight(pipeline, probe=probe))
    _report_preflight(issues, probed=probe)
    if any(i.blocking for i in issues):
        _fail(f"{sum(1 for i in issues if i.blocking)} blocking requirement(s) unmet")


@app.command()
def validate(
    out: Annotated[Path, typer.Argument(help="Directory of emitted YAML")] = Path(
        "build/pipelines"
    ),
) -> None:
    """Check emitted YAML with Conductor's own validator."""
    files = sorted(out.glob("*.yaml"))
    if not files:
        _fail(f"no compiled output in {out}; run `ictus emit` first")
    try:
        results = BACKEND.validate(files)
    except FileNotFoundError as exc:
        _fail(str(exc))
        return
    failed = [r for r in results if not r.ok]
    for result in results:
        if result.ok:
            typer.secho(f"ok  {result.path}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"BAD {result.path}", fg=typer.colors.RED, err=True)
            typer.echo(result.detail, err=True)
    if failed:
        name = BACKEND.capabilities().name
        _fail(f"{len(failed)} of {len(results)} workflow(s) rejected by {name}")


@app.command()
def run(
    workflow: Annotated[str, typer.Argument(help="Pipeline id (basename of the emitted YAML)")],
    out: Annotated[Path, typer.Option(help="Directory of emitted YAML")] = Path("build/pipelines"),
    pipelines_dir: Annotated[
        Path, typer.Option("--pipelines", help="Where the pipeline modules live")
    ] = Path("pipelines"),
    web: Annotated[
        bool, typer.Option(help="Serve the dashboard so gates can be answered remotely")
    ] = True,
    probe: Annotated[bool, typer.Option(help="Open each declared connection at preflight")] = True,
    skip_preflight: Annotated[
        bool, typer.Option(help="Launch without checking the environment first")
    ] = False,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            "-b",
            help="Detach and return once started — the right mode for a gated pipeline",
        ),
    ] = False,
    inputs: Annotated[
        list[str] | None,
        typer.Option("--input", "-i", help="Workflow input as name=value; repeatable"),
    ] = None,
) -> None:
    """Run an emitted workflow with Conductor.

    The dashboard is on by default: a gate is only answerable from another
    machine while the run has a dashboard port, and mid-run guidance needs one
    too.
    """
    path = out / f"{workflow}.yaml"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in out.glob("*.yaml"))) or "(none emitted)"
        _fail(f"{path} does not exist; available: {available}")

    if not skip_preflight:
        # Preflight reads the pipeline module, not the emitted file: the
        # requirements are a property of what was authored, and deliberately do
        # not carry secrets into the compiled artifact.
        matching = [p for p in _load_pipelines(pipelines_dir) if p.pipeline_id == workflow]
        if not matching:
            _fail(
                f"cannot preflight {workflow!r}: no pipeline in {pipelines_dir} declares that id. "
                "Pass --skip-preflight to launch anyway."
            )
        issues = BACKEND.preflight(matching[0], probe=probe)
        blocking = [i for i in issues if i.blocking]
        if issues:
            _report_preflight(issues, probed=probe)
        if blocking:
            _fail(
                f"{len(blocking)} requirement(s) unmet; nothing was launched. "
                "Fix them, or pass --skip-preflight to launch anyway."
            )
    supplied: dict[str, str] = {}
    for pair in inputs or []:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            _fail(f"--input expects name=value, got {pair!r}")
        supplied[name] = value

    try:
        code = BACKEND.run(path, inputs=supplied, dashboard=web, background=background)
    except FileNotFoundError as exc:
        _fail(str(exc))
        return
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
