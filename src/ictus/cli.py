"""The ``ictus`` command line."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ictus.config import CONFIG_FILE, MINIMAL, PipelineConfig, read_config
from ictus.errors import IctusError
from ictus.gate import add_start_gate
from ictus.graph.pipeline import Pipeline
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline
from ictus.runspec import PipelineFolder, read_input_file
from ictus.scaffold import STARTER_INPUT, STARTER_PIPELINE

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


def _folders(path: Path) -> list[PipelineFolder]:
    """The pipeline folders at or under ``path``."""
    try:
        return PipelineFolder.find(path)
    except IctusError as exc:
        _fail(str(exc))
        return []


def _load_module(module_path: Path) -> list[Pipeline]:
    """Import one module and collect the top-level pipelines it defines.

    A pipeline that is the body of a stage is dropped: it is emitted as part of
    its parent, and emitting it again at top level would produce a second file
    claiming the same name.
    """
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

    found: list[Pipeline] = []
    for name in sorted(dir(module)):
        value = getattr(module, name)
        if isinstance(value, Pipeline) and not any(value is p for p in found):
            found.append(value)
    nested = {id(child) for p in found for child in _descendants(p)}
    return [p for p in found if id(p) not in nested]


def _load(folder: PipelineFolder) -> list[Pipeline]:
    """The pipelines a folder defines, with its run policy applied.

    Policy and the start gate are applied here rather than in the composition so
    every command sees the same thing: a lint reading a different provider from
    the emit would be checking a workflow nobody runs.
    """
    pipelines = _load_module(folder.module)
    try:
        settings = read_config(folder.config_file)
        _check_provider(settings.provider, where=str(folder.config_file))
        for pipeline in pipelines:
            settings.apply(pipeline, where=str(folder.config_file))
            if settings.start_gate:
                add_start_gate(pipeline)
    except IctusError as exc:
        _fail(str(exc))
    return pipelines


def _check_provider(name: str, *, where: str) -> None:
    """Refuse a provider the backend cannot use, where it was written."""
    known = BACKEND.capabilities().providers
    if known and name not in known:
        _fail(
            f"{where}: {name!r} is not a provider {BACKEND.capabilities().name} can use; "
            f"choose one of {sorted(known)}"
        )


def _policy(folder: PipelineFolder) -> PipelineConfig:
    """A folder's run policy, on its own."""
    try:
        return read_config(folder.config_file)
    except IctusError as exc:
        _fail(str(exc))
        raise


def _only(folder: PipelineFolder) -> Pipeline:
    """The single pipeline a folder defines, which a run needs."""
    pipelines = _load(folder)
    if not pipelines:
        _fail(f"{folder.module} defines no pipeline")
    if len(pipelines) > 1:
        names = ", ".join(sorted(p.pipeline_id for p in pipelines))
        _fail(
            f"{folder.module} defines more than one top-level pipeline ({names}); "
            "a pipeline folder runs exactly one"
        )
    return pipelines[0]


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
    where: Annotated[Path, typer.Argument(help="A pipeline folder, or a directory of them")] = Path(
        "pipelines"
    ),
    out: Annotated[
        Path | None,
        typer.Option(help="Write every workflow here instead of each folder's build/"),
    ] = None,
    prune: Annotated[
        bool, typer.Option(help="Delete YAML in the output that no pipeline claims")
    ] = True,
) -> None:
    """Compile pipeline folders to Conductor YAML.

    Each folder's YAML goes to its own ``build/`` — committed, so a diff shows
    what changed in what actually runs.
    """
    folders = _folders(where)
    targets = [(f, out if out is not None else f.build) for f in folders]
    pipelines = [p for folder, _ in targets for p in _load(folder)]
    if not pipelines:
        _fail(f"no pipelines found in {where} (nothing was emitted)")

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
    for folder, destination in targets:
        for pipeline in _load(folder):
            try:
                written.update(_write(pipeline, destination))
            except IctusError as exc:
                _fail(f"{pipeline.pipeline_id}: {exc}")

    if prune:
        for _, destination in targets:
            if not destination.is_dir():
                continue
            for stale in sorted(set(destination.glob("*.yaml")) - written):
                stale.unlink()
                typer.echo(f"pruned {stale}")

    for path in sorted(written):
        typer.echo(f"emitted {path}")
    typer.secho(
        f"{len(written)} workflow(s) from {len(pipelines)} pipeline(s)", fg=typer.colors.GREEN
    )


@app.command()
def lint(
    where: Annotated[Path, typer.Argument(help="A pipeline folder, or a directory of them")] = Path(
        "pipelines"
    ),
) -> None:
    """Run the composition lints without writing anything."""
    pipelines = [p for folder in _folders(where) for p in _load(folder)]
    if not pipelines:
        _fail(f"no pipelines found in {where}")
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
    where: Annotated[Path, typer.Argument(help="A pipeline folder, or a directory of them")] = Path(
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
    pipelines = [p for folder in _folders(where) for p in _load(folder)]
    if not pipelines:
        _fail(f"no pipelines found in {where}")
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
    where: Annotated[
        Path,
        typer.Argument(help="A pipeline folder, a directory of them, or a directory of YAML"),
    ] = Path("pipelines"),
) -> None:
    """Check emitted YAML with Conductor's own validator."""
    # Pipeline folders first. A folder holds `config.yaml`, which is not a
    # workflow — globbing *.yaml here handed it to Conductor's loader, which
    # rejected it for the entirely correct reason that it has no `agents:`.
    try:
        found = PipelineFolder.find(where)
    except IctusError:
        found = []
    if found:
        files = sorted(f for folder in found for f in folder.build.glob("*.yaml"))
    elif where.is_dir():
        files = sorted(where.glob("*.yaml"))
    else:
        _fail(f"{where} is not a directory")
        return
    if not files:
        _fail(f"no compiled output under {where}; run `ictus emit` first")
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
    folder: Annotated[Path, typer.Argument(help="The pipeline folder to run")] = Path(),
    input_file: Annotated[
        Path | None,
        typer.Option("--input-file", "-f", help="Use this instead of the folder's input.md"),
    ] = None,
    repo: Annotated[
        Path | None,
        typer.Option(help="Work in this directory instead of the current one"),
    ] = None,
    web: Annotated[
        bool, typer.Option(help="Serve the dashboard so gates can be answered remotely")
    ] = True,
    probe: Annotated[bool, typer.Option(help="Open each declared connection at preflight")] = True,
    skip_preflight: Annotated[
        bool, typer.Option(help="Launch without checking the environment first")
    ] = False,
    reemit: Annotated[
        bool, typer.Option(help="Compile before running, so the YAML matches the source")
    ] = True,
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
        typer.Option("--input", "-i", help="Override one input as name=value; repeatable"),
    ] = None,
) -> None:
    """Run a pipeline folder against a project.

    The work happens in the directory you invoked this from, so `cd` to a
    project and go. A `repo:` key in the input file, or `--repo`, overrides that
    — a run that touches a checkout can then say which one in something you can
    commit.
    """
    started_in = Path.cwd()
    try:
        target = PipelineFolder.at(folder)
    except IctusError as exc:
        _fail(str(exc))
        return
    pipeline = _only(target)

    spec = None
    source = input_file if input_file is not None else target.input_file
    if source.is_file():
        try:
            spec = read_input_file(source, pipeline, cwd=started_in)
        except IctusError as exc:
            _fail(str(exc))
    elif input_file is not None:
        _fail(f"{input_file} does not exist")

    supplied: dict[str, str] = dict(spec.inputs) if spec else {}
    for pair in inputs or []:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            _fail(f"--input expects name=value, got {pair!r}")
        supplied[name] = value

    declared = {param.name: param for param in pipeline.workflow_inputs}
    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        known = ", ".join(sorted(declared)) or "(none)"
        _fail(f"{unknown} are not inputs of {pipeline.pipeline_id!r}; declared: {known}")
    missing = sorted(n for n, d in declared.items() if d.required and n not in supplied)
    if missing:
        hint = f" Add them to {source}," if source.is_file() else f" Create {target.input_file},"
        _fail(f"{pipeline.pipeline_id!r} requires {missing}.{hint} or pass -i name=value.")

    working = (
        repo.expanduser().resolve()
        if repo is not None
        else (spec.working_dir if spec else started_in)
    )
    if not working.is_dir():
        _fail(f"{working} is not a directory")

    if reemit:
        try:
            _write(pipeline, target.build)
        except IctusError as exc:
            _fail(f"{pipeline.pipeline_id}: {exc}")
    path = target.build / f"{pipeline.pipeline_id}.yaml"
    if not path.is_file():
        _fail(f"{path} does not exist; run `ictus emit {folder}` first")

    if not skip_preflight:
        # Preflight reads the pipeline module, not the emitted file: the
        # requirements are a property of what was authored, and deliberately do
        # not carry secrets into the compiled artifact.
        issues = BACKEND.preflight(pipeline, probe=probe)
        blocking = [i for i in issues if i.blocking]
        if issues:
            _report_preflight(issues, probed=probe)
        if blocking:
            _fail(
                f"{len(blocking)} requirement(s) unmet; nothing was launched. "
                "Fix them, or pass --skip-preflight to launch anyway."
            )

    typer.secho(f"{pipeline.pipeline_id} in {working}", fg=typer.colors.CYAN)
    for name in sorted(supplied):
        preview = supplied[name].replace("\n", " ")
        typer.echo(f"  {name} = {preview[:70]}{'…' if len(preview) > 70 else ''}")

    try:
        code = BACKEND.run(
            path,
            inputs=supplied,
            dashboard=web,
            background=background,
            working_dir=working,
        )
    except FileNotFoundError as exc:
        _fail(str(exc))
        return
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()


@app.command()
def init(
    folder: Annotated[Path, typer.Argument(help="The pipeline folder to scaffold")],
) -> None:
    """Create the files a pipeline folder needs.

    Nothing that already exists is touched: running this on a folder someone has
    started is how you add the file you forgot, not a way to lose work.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in (
        (CONFIG_FILE, MINIMAL),
        ("input.md", STARTER_INPUT),
        ("pipeline.py", STARTER_PIPELINE),
    ):
        target = folder / name
        if target.exists():
            typer.echo(f"kept  {target}")
            continue
        target.write_text(body, encoding="utf-8")
        typer.secho(f"wrote {target}", fg=typer.colors.GREEN)
    typer.echo(f"\nnow: ictus lint {folder} && ictus run {folder}")
