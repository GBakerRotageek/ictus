"""The ``ictus`` command line."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import typer

from ictus.config import CONFIG_FILE, MINIMAL, PipelineConfig, read_config
from ictus.errors import IctusError
from ictus.gate import add_start_gate
from ictus.graph.pipeline import Pipeline
from ictus.interfaces.conductor import conductor
from ictus.interfaces.conductor.trace import LOG_DIR, find_logs, read_trace
from ictus.lint import lint_pipeline
from ictus.runspec import PipelineFolder, read_input_file
from ictus.runstate import RunRecord, changed_files, fingerprint, prune_runs, runs_of, start_run
from ictus.scaffold import STARTER_INPUT, STARTER_PIPELINE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ictus.interfaces import PreflightIssue, ResumePlan

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


def _load(folder: PipelineFolder, *, require_config: bool = True) -> list[Pipeline]:
    """The pipelines a folder defines, with its run policy applied.

    Policy and the start gate are applied here rather than in the composition so
    every command sees the same thing: a lint reading a different provider from
    the emit would be checking a workflow nobody runs.

    ``require_config`` is what ``lint`` relaxes. A folder with no ``config.yaml``
    used to fail before a single composition rule ran, so the one command whose
    whole job is to find problems in a graph reported exactly one problem and it
    was about a file. The graph is still worth checking; what the run would do
    with it is not yet decided. A malformed config still fails either way — that
    is an error to fix, not a decision left open.
    """
    pipelines = _load_module(folder.module)
    if not require_config and not folder.config_file.is_file():
        typer.secho(
            f"warning: {folder.config_file} does not exist, so the run policy is unknown. "
            "Checking the graph only — the provider and the start gate are not applied, "
            "and `ictus emit` will still refuse this folder.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return pipelines
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


@dataclass(frozen=True, slots=True)
class _Written:
    """One emitted file, and what writing it did to the bytes already there.

    ``build/`` is committed, so "did this write move the artifact" is the thing
    a caller needs to report. A compile that produces identical bytes is not a
    change, and saying "emitted" for it hides the writes that are.
    """

    path: Path
    status: Literal["wrote", "updated", "same"]

    @property
    def changed(self) -> bool:
        return self.status != "same"


def _write(pipeline: Pipeline, out: Path) -> list[_Written]:
    """Compile and write every document a pipeline produces.

    Each file goes to a temporary sibling and is renamed, so a failure part way
    through cannot leave a truncated document that still parses.
    """
    out.mkdir(parents=True, exist_ok=True)
    written: list[_Written] = []
    for document in BACKEND.compile(pipeline):
        target = out / document.filename
        before = target.read_text(encoding="utf-8") if target.is_file() else None
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(document.content, encoding="utf-8")
        tmp.replace(target)
        status: Literal["wrote", "updated", "same"] = (
            "wrote" if before is None else "same" if before == document.content else "updated"
        )
        written.append(_Written(target, status))
    return written


def _prune(destination: Path, keep: set[Path]) -> list[Path]:
    """Delete YAML in ``destination`` that no pipeline claims."""
    if not destination.is_dir():
        return []
    stale = sorted(set(destination.glob("*.yaml")) - keep)
    for path in stale:
        path.unlink()
    return stale


def _report_written(written: Sequence[_Written], pruned: Sequence[Path]) -> None:
    """Say which committed files moved, so the write is auditable without a diff."""
    for path in pruned:
        typer.secho(f"pruned  {path}", fg=typer.colors.YELLOW)
    for item in sorted(written, key=lambda w: w.path):
        colour = typer.colors.YELLOW if item.changed else None
        typer.secho(f"{item.status:<7} {item.path}", fg=colour)


def _refuse_problems(problems: list[str], *, consequence: str) -> None:
    """Report composition problems and stop, naming what did not happen."""
    if not problems:
        return
    for problem in problems:
        typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
    _fail(f"{len(problems)} composition problem(s); {consequence}")


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
    # Loaded once: emitting objects re-loaded after the lint would write a graph
    # nothing checked, because a module is free to build a different one.
    targets = [(out if out is not None else f.build, _load(f)) for f in folders]
    pipelines = [p for _, group in targets for p in group]
    if not pipelines:
        _fail(f"no pipelines found in {where} (nothing was emitted)")

    problems = [p for pipeline in pipelines for p in lint_pipeline(pipeline, backend=BACKEND)]
    _refuse_problems(problems, consequence="nothing was written")

    seen: dict[str, str] = {}
    for pipeline in pipelines:
        try:
            documents = BACKEND.compile(pipeline)
        except IctusError as exc:
            _fail(f"{pipeline.pipeline_id}: {exc}")
            return
        for document in documents:
            owner = seen.get(document.filename)
            if owner is not None:
                problems.append(
                    f"{document.filename} is claimed by both {owner!r} and {pipeline.pipeline_id!r}"
                )
            seen[document.filename] = pipeline.pipeline_id
    _refuse_problems(problems, consequence="nothing was written")

    written: list[_Written] = []
    for destination, group in targets:
        for pipeline in group:
            try:
                written.extend(_write(pipeline, destination))
            except IctusError as exc:
                _fail(f"{pipeline.pipeline_id}: {exc}")

    pruned: list[Path] = []
    if prune:
        keep = {item.path for item in written}
        for destination in dict.fromkeys(d for d, _ in targets):
            pruned.extend(_prune(destination, keep))

    _report_written(written, pruned)
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
    pipelines = [p for folder in _folders(where) for p in _load(folder, require_config=False)]
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
        commands = pipeline.all_executables()
        total = len(declared) + len(commands)
        typer.echo(f"{pipeline.pipeline_id}: {total} requirement(s) declared")
        for server in declared:
            typer.echo(f"  - mcp:{server.name} — {server.purpose}")
        for tool in commands:
            typer.echo(f"  - exe:{tool.name} — {tool.purpose}")
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
        bool | None,
        typer.Option(
            "--web/--no-web",
            help="Serve the dashboard (default: config.yaml, else on)",
        ),
    ] = None,
    probe: Annotated[bool, typer.Option(help="Open each declared connection at preflight")] = True,
    workspace_instructions: Annotated[
        bool | None,
        typer.Option(
            "--workspace-instructions/--no-workspace-instructions",
            help="Read the target project's CLAUDE.md/AGENTS.md (default: config.yaml, else on)",
        ),
    ] = None,
    skip_preflight: Annotated[
        bool, typer.Option(help="Launch without checking the environment first")
    ] = False,
    reemit: Annotated[
        bool, typer.Option(help="Compile before running, so the YAML matches the source")
    ] = True,
    background: Annotated[
        bool | None,
        typer.Option(
            "--background/--foreground",
            "-b/-F",
            help="Detach and let the dashboard drive (default: on when the dashboard is enabled)",
        ),
    ] = None,
    inputs: Annotated[
        list[str] | None,
        typer.Option("--input", "-i", help="Override one input as name=value; repeatable"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the execution plan and stop, spending nothing"),
    ] = False,
    log_file: Annotated[
        str | None,
        typer.Option(
            "--log-file",
            "-l",
            help="Write full debug output here, or 'auto' for a generated temp file",
        ),
    ] = None,
) -> None:
    """Run a pipeline folder against a project.

    The work happens in the directory you invoked this from, so `cd` to a
    project and go. A `repo:` key in the input file, or `--repo`, overrides that
    — a run that touches a checkout can then say which one in something you can
    commit.

    Detached by default when the dashboard is enabled. A foreground run holds
    the terminal: Conductor puts it in cbreak mode for its interrupt listener and
    answers gates there, and anything that blocks on it blocks the same event loop that serves the
    dashboard — so the browser freezes on whatever it last saw and the run looks
    hung when it is waiting for a keystroke nobody is watching. Detached, the
    dashboard is the only place anything is answered, which is the one place you
    are looking.
    """
    started_in = Path.cwd()
    try:
        target = PipelineFolder.at(folder)
    except IctusError as exc:
        _fail(str(exc))
        return
    pipeline = _only(target)
    # Before the inputs, because a broken graph is a source defect and saying so
    # first is more use than asking for values that will not be spent. Before
    # `_write` and the launch, because `--reemit` is on by default: without this
    # the committed artifact is overwritten with output nothing checked, and then
    # run.
    _refuse_problems(
        lint_pipeline(pipeline, backend=BACKEND),
        consequence="nothing was compiled or launched",
    )

    policy = _policy(target)
    serves_dashboard, detached = _launch_mode(target, policy, web=web, background=background)

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
    # A dry run substitutes nothing, so demanding values it will never spend
    # would put the plan behind the very inputs you are reading it to decide.
    # A *misspelled* one is still refused above: that is a defect either way.
    if missing and not dry_run:
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
            written = _write(pipeline, target.build)
        except IctusError as exc:
            _fail(f"{pipeline.pipeline_id}: {exc}")
            raise
        # Only the moves: a reemit that says nothing has confirmed `build/` was
        # already what the source compiles to, which is the common case and the
        # one worth being quiet about.
        _report_written(
            [item for item in written if item.changed],
            _prune(target.build, {item.path for item in written}),
        )
    path = target.build / f"{pipeline.pipeline_id}.yaml"
    if not path.is_file():
        _fail(f"{path} does not exist; run `ictus emit {folder}` first")

    if dry_run:
        # Ahead of preflight, which opens real connections: a plan that spends
        # nothing should not need a live environment to print.
        typer.secho(f"{pipeline.pipeline_id}: plan only, nothing runs", fg=typer.colors.CYAN)
        raise typer.Exit(code=BACKEND.plan(path, working_dir=working))

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

    reads_project = (
        workspace_instructions
        if workspace_instructions is not None
        else policy.workspace_instructions
    )
    if reads_project:
        typer.secho(
            "  reading the project's own instruction files (CLAUDE.md, AGENTS.md)",
            fg=typer.colors.BRIGHT_BLACK,
        )

    earlier = runs_of(target)
    if earlier and _resumable(earlier[0]):
        # Starting over is a legitimate choice, so this informs rather than
        # refuses — but only the newest run is what `ictus resume` picks, and
        # this launch is about to become it.
        typer.secho(
            f"  run {earlier[0].name} was interrupted and can still be resumed: "
            f"ictus resume {target.root} --run {earlier[0].name}",
            fg=typer.colors.YELLOW,
        )
    record = start_run(target, pipeline_id=pipeline.pipeline_id, workflow=path, working_dir=working)
    prune_runs(target, resumable=_resumable)
    typer.secho(
        f"  if this run is interrupted: ictus resume {target.root}", fg=typer.colors.BRIGHT_BLACK
    )

    try:
        code = BACKEND.run(
            path,
            inputs=supplied,
            dashboard=serves_dashboard,
            background=detached,
            workspace_instructions=reads_project,
            working_dir=working,
            log_file=log_file,
            state_dir=record.engine_dir,
        )
    except FileNotFoundError as exc:
        _fail(str(exc))
        return
    _report_activity(pipeline.pipeline_id, background=detached, state_dir=record.engine_dir)
    raise typer.Exit(code=code)


@app.command()
def resume(
    folder: Annotated[
        Path, typer.Argument(help="The pipeline folder whose run to resume")
    ] = Path(),
    run_name: Annotated[
        str | None,
        typer.Option("--run", help="Resume this recorded run instead of the newest"),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Resume without asking, once the plan is printed")
    ] = False,
    web: Annotated[
        bool | None,
        typer.Option("--web/--no-web", help="Serve the dashboard (default: config.yaml, else on)"),
    ] = None,
    background: Annotated[
        bool | None,
        typer.Option(
            "--background/--foreground",
            "-b/-F",
            help="Detach and let the dashboard drive (default: on when the dashboard is enabled)",
        ),
    ] = None,
    log_file: Annotated[
        str | None,
        typer.Option(
            "--log-file",
            "-l",
            help="Write full debug output here, or 'auto' for a generated temp file",
        ),
    ] = None,
) -> None:
    """Continue an interrupted run from its last checkpoint.

    Refuses when what is on disk is no longer what was running — a changed
    `build/`, or a source that compiles to something else — because continuing
    a different workflow from a checkpoint taken in this one is not resuming.

    Says first what will happen again: the step that was running starts over, a
    stage restarts from its first step, and a command that had already done
    something does it twice. Then asks, unless `--yes`.
    """
    try:
        target = PipelineFolder.at(folder)
        runs = runs_of(target)
    except IctusError as exc:
        _fail(str(exc))
        return
    if not runs:
        _fail(f"{target.root} has no recorded run; start one with `ictus run {target.root}`")
    chosen = runs[0] if run_name is None else next((r for r in runs if r.name == run_name), None)
    if chosen is None:
        known = ", ".join(r.name for r in runs)
        _fail(f"{target.root} has no run named {run_name!r}; recorded: {known}")
        return
    manifest = chosen.manifest

    # Before loading the source: a changed artifact is refused whatever the
    # source says, and it is the cheaper check.
    drifted = changed_files(manifest.fingerprint, fingerprint(target.build))
    if drifted:
        _fail(
            f"build/ has changed since run {chosen.name} was launched: {', '.join(drifted)}. "
            "Resuming would continue a different workflow from a checkpoint taken in this "
            "one. Restore those files, or start a new run."
        )
    pipeline = _only(target)
    compiled = {
        d.filename: hashlib.sha256(d.content.encode("utf-8")).hexdigest()
        for d in BACKEND.compile(pipeline)
    }
    diverged = changed_files(manifest.fingerprint, compiled)
    if diverged:
        _fail(
            f"{target.module} no longer compiles to what run {chosen.name} was running "
            f"({', '.join(diverged)}). What resumes is the emitted YAML, but what would be "
            "reported about it comes from the source, and the two now disagree. Restore the "
            "source, or start a new run."
        )
    if not manifest.working_dir.is_dir():
        _fail(f"run {chosen.name} worked in {manifest.working_dir}, which no longer exists")

    try:
        plan = BACKEND.resume_plan(pipeline, manifest.workflow, state_dir=chosen.engine_dir)
    except IctusError as exc:
        _fail(str(exc))
        return
    if plan is None:
        _fail(
            f"run {chosen.name} left nothing to resume: it finished, or it stopped before "
            "its first step completed. Start a new run."
        )
        return

    _report_resume(pipeline.pipeline_id, chosen, plan)
    if not yes:
        if not sys.stdin.isatty():
            _fail("nobody is here to answer; pass --yes to resume without being asked")
        if not typer.confirm("Resume?", default=False):
            typer.echo("not resumed")
            raise typer.Exit(code=0)

    policy = _policy(target)
    serves_dashboard, detached = _launch_mode(target, policy, web=web, background=background)
    try:
        code = BACKEND.resume(
            plan,
            state_dir=chosen.engine_dir,
            dashboard=serves_dashboard,
            background=detached,
            working_dir=manifest.working_dir,
            log_file=log_file,
        )
    except FileNotFoundError as exc:
        _fail(str(exc))
        return
    _report_activity(pipeline.pipeline_id, background=detached, state_dir=chosen.engine_dir)
    raise typer.Exit(code=code)


def _report_resume(pipeline_id: str, run: RunRecord, plan: ResumePlan) -> None:
    """Everything resuming will and will not do again, before it does any of it."""
    typer.secho(f"{pipeline_id}: resuming run {run.name}", fg=typer.colors.CYAN)
    typer.echo(f"  in {run.manifest.working_dir}")
    stopped = plan.reason or "no error recorded — killed, or its machine went down"
    typer.echo(f"  saved {plan.saved_at}; stopped: {stopped}")
    if plan.completed:
        counts = {name: plan.completed.count(name) for name in dict.fromkeys(plan.completed)}
        done = ", ".join(f"{n} x{c}" if c > 1 else n for n, c in counts.items())
        typer.echo(f"  done: {done}")
    typer.echo(f"  resumes at: {plan.step}")
    if plan.reruns != (plan.step,):
        typer.secho(
            f"  {plan.step} restarts from its first step; these run again: "
            f"{', '.join(plan.reruns)}",
            fg=typer.colors.YELLOW,
        )
    if plan.scripts:
        typer.secho(
            f"  commands that run again, repeating anything they did before: "
            f"{', '.join(plan.scripts)}",
            fg=typer.colors.YELLOW,
        )
    if plan.cold_sessions:
        typer.secho(
            f"  sessions that do not survive a resume; these start without them: "
            f"{', '.join(plan.cold_sessions)}",
            fg=typer.colors.YELLOW,
        )


def _launch_mode(
    target: PipelineFolder, policy: PipelineConfig, *, web: bool | None, background: bool | None
) -> tuple[bool, bool]:
    """Whether to serve the dashboard, and whether to detach.

    Flags override folder policy. Both are resolved before checking they fit
    together: disabling the dashboard makes the default launch use the terminal.
    """
    serves_dashboard = web if web is not None else policy.dashboard
    detached = background if background is not None else serves_dashboard
    if detached and not serves_dashboard:
        _fail(
            "--background requires the dashboard so detached gates can be answered. "
            f"Enable it with --web or dashboard: true in {target.config_file}, "
            "or pass --foreground."
        )
    return serves_dashboard, detached


def _resumable(run: RunRecord) -> bool:
    return BACKEND.can_resume(run.manifest.workflow, state_dir=run.engine_dir)


def _report_activity(workflow: str, *, background: bool, state_dir: Path | None = None) -> None:
    """Say which steps answered without looking at anything.

    A run's exit code says whether it finished, not whether it thought. The
    engine already records every tool call; not reading them back is how a
    council shipped a report whose findings nobody had checked. A background run
    is still going, so it gets the command instead of the answer.
    """
    if background:
        typer.secho(f"\nwhen it finishes: ictus trace {workflow}", fg=typer.colors.BRIGHT_BLACK)
        return
    found = find_logs(workflow, state_dir=state_dir)
    if not found:
        return
    try:
        seen = read_trace(found[0])
    except OSError:
        return
    idle = seen.incurious
    if idle:
        typer.secho(
            f"\n{len(idle)} step(s) answered without consulting anything: "
            f"{', '.join(s.name for s in idle)}",
            fg=typer.colors.YELLOW,
        )
        typer.echo(
            "Right for a step whose whole input is in its prompt, wrong for one asked "
            f"to assess something it was only shown a summary of. `ictus trace {workflow}` "
            "shows what each one opened."
        )
    stopped = seen.capped
    if stopped:
        typer.secho(
            f"\n{len(stopped)} step(s) hit the turn ceiling: "
            f"{', '.join(s.name for s in stopped)}. Give them max_turns.",
            fg=typer.colors.RED,
        )


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
    # Deliberately stops short of `ictus run`. The old closing line named it, and
    # following the tool's own instruction on an untouched folder starts a real,
    # billable Conductor process against a placeholder.
    typer.echo(
        f"\nnext:\n"
        f"  1. {folder / 'pipeline.py'} — replace CHANGE-ME with a name, "
        f"then say what the step does\n"
        f"  2. {folder / 'input.md'} — the values to run it on\n"
        f"  3. ictus lint {folder}\n"
    )


def _declared_ceilings(pipeline: Pipeline, _into: dict[str, int] | None = None) -> dict[str, int]:
    """Each step's own ``max_turns``, including those inside nested stages.

    A stage compiles to its own workflow file but its steps appear in the parent
    run's event log under their own names, so a trace of the whole run needs
    every level's limits or it measures a stage's steps against the default.
    """
    found = {} if _into is None else _into
    for node in pipeline.nodes:
        limit = getattr(node, "max_turns", None)
        if limit is not None:
            found[node.node_id] = limit
    for child in pipeline.children.values():
        _declared_ceilings(child, found)
    return found


@app.command()
def trace(
    folder: Annotated[
        Path | None,
        typer.Argument(help="A pipeline folder, to trace its most recent run"),
    ] = None,
    log: Annotated[Path | None, typer.Option("--log", help="Read this event log instead")] = None,
    files: Annotated[bool, typer.Option(help="List what each step opened")] = False,
) -> None:
    """Show what each step of the last run actually did.

    A step's output says what it concluded. It does not say whether it looked at
    anything first, and those are different runs that read identically. The
    engine records every tool call already; this reads them back.
    """
    ceilings: dict[str, int] = {}
    if log is not None:
        path = log
    else:
        located = PipelineFolder.at(folder or Path())
        pipeline = _only(located)
        ceilings = _declared_ceilings(pipeline)
        # The newest recorded run's own directory first; a run launched straight
        # through the engine, or before runs were recorded, left its log in LOG_DIR.
        recorded = runs_of(located)
        found = (
            find_logs(pipeline.pipeline_id, state_dir=recorded[0].engine_dir) if recorded else []
        ) or find_logs(pipeline.pipeline_id)
        if not found:
            _fail(
                f"no run of {pipeline.pipeline_id!r} found in its recorded runs or under "
                f"{LOG_DIR}. Runs write an event log as they go; this one may not have started."
            )
        path = found[0]
    if not path.is_file():
        _fail(f"{path} does not exist")

    seen = read_trace(path, ceilings=ceilings)
    typer.secho(f"{seen.workflow}  {path.name}", fg=typer.colors.CYAN)
    if not seen.steps:
        typer.echo("no steps recorded yet")
        return

    header = f"  {'step':<20} {'turns':>5} {'looked':>7} {'tokens':>8} {'cost':>8}  tools"
    typer.secho(header, fg=typer.colors.BRIGHT_BLACK)
    for step in seen.steps.values():
        used = ", ".join(f"{t}x{n}" for t, n in step.tools.most_common()) or "—"
        colour = typer.colors.YELLOW if not step.looked and step.turns else None
        typer.secho(
            f"  {step.name:<20} {step.turns:>5} {step.investigated:>7} "
            f"{step.tokens:>8} {step.cost_usd:>8.4f}  {used}",
            fg=colour,
        )
        if files and step.reads:
            for target in dict.fromkeys(step.reads):
                typer.secho(f"      {target}", fg=typer.colors.BRIGHT_BLACK)

    stopped = seen.capped
    if stopped:
        typer.secho(
            f"\n{len(stopped)} step(s) hit the turn ceiling: {', '.join(s.name for s in stopped)}",
            fg=typer.colors.RED,
        )
        typer.echo(
            "A step that runs out of turns does not return what it had — the provider "
            "raises, and that error is not one a scope can turn into an outcome, so it "
            "fails the whole run. Give it max_turns."
        )

    idle = seen.incurious
    if idle:
        typer.secho(
            f"\n{len(idle)} step(s) answered without consulting anything: "
            f"{', '.join(s.name for s in idle)}",
            fg=typer.colors.YELLOW,
        )
        typer.echo(
            "That is right for a step whose whole input is in its prompt, and wrong "
            "for one asked to assess something it was only shown a summary of."
        )


# Last in the file, and it must stay last. Under `python -m ictus.cli` this
# guard is true and the module stops executing here, so a command decorated
# below it is never registered — while the console script, which imports the
# module rather than running it, registers everything. `trace` sat below this
# for a release: `ictus trace` worked, `python -m ictus.cli trace` said no such
# command, and the docs looked wrong. `test_every_command_is_reachable` is what
# keeps it honest.
if __name__ == "__main__":
    app()
