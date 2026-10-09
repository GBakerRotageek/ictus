"""The Conductor backend.

The only package that may know Conductor's spelling: field names, template
dialect, iteration accounting, CLI.

    workflow.py   the ``workflow:`` block and the defaults worth stating
    agents.py     one node to one ``agents:`` entry
    serialize.py  YAML text, without altering any value
    lints.py      rules that are true because of how Conductor runs
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

from ictus.errors import IctusError
from ictus.graph.node import NODE_KINDS
from ictus.interfaces import Capabilities, Document, PreflightIssue, ValidationResult
from ictus.interfaces.conductor.control.signals import REPORTABLE
from ictus.interfaces.conductor.emit import manifest
from ictus.interfaces.conductor.emit.agents import agent_entry
from ictus.interfaces.conductor.emit.mapping import for_each_block
from ictus.interfaces.conductor.emit.parallel import parallel_block
from ictus.interfaces.conductor.emit.serialize import dump_yaml
from ictus.interfaces.conductor.emit.templates import output_block
from ictus.interfaces.conductor.emit.workflow import NOTHING_INHERITED, Inherited, workflow_block
from ictus.interfaces.conductor.lints import conductor_problems
from ictus.interfaces.conductor.preflight import preflight_issues
from ictus.interfaces.environment import (
    datasource_issues,
    executable_issues,
    integration_issues,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence
    from pathlib import Path

    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict, YamlValue

__all__ = [
    "TYPED_INPUT_FLAG",
    "ConductorBackend",
    "binary",
    "conductor",
    "launch_command",
    "launch_env",
]

BINARY = "conductor"


def binary() -> str:
    """The Conductor executable, or a ``FileNotFoundError`` naming what is missing."""
    found = shutil.which(BINARY)
    if found is None:
        raise FileNotFoundError(
            f"{BINARY!r} is not on PATH; the Conductor backend cannot check or run "
            "what it compiles without it"
        )
    return found


#: Variables about the machine, which a run cannot work without and which are
#: nobody's pipeline secret.
MACHINE_ENV: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "SYSTEMROOT",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
    }
)

#: Prefixes for the engine's own settings and for model-provider credentials.
#: By prefix rather than by list, so a provider added upstream still works.
MACHINE_PREFIXES: tuple[str, ...] = (
    "CONDUCTOR_",
    "CLAUDE_",
    "ANTHROPIC_",
    "OPENAI_",
    "AZURE_",
    "COPILOT_",
    "GITHUB_",
    "ACA_",
    "OTEL_",
)


def launch_env(declared: Iterable[str], source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a run should receive: what it declared, and nothing else.

    The run's process is the only place an environment can be cut; a step with
    a shell sees everything the run was given. ``declared`` is the pipeline's
    integrations, datasources and MCP servers. Anything outside that and
    :data:`MACHINE_ENV` is absent rather than empty.
    """
    present = os.environ if source is None else source
    wanted = set(declared) | MACHINE_ENV
    return {
        name: value
        for name, value in present.items()
        if name in wanted or name.startswith(MACHINE_PREFIXES)
    }


#: Conductor's typed input transport: ``name=<json>``, decoded strictly.
#:
#: ``-i`` runs a value through ``coerce_value``, which guesses a type. That is
#: wanted for a number and ruinous for a string that looks like one: a Slack
#: timestamp of ``1700000000.000200`` arrives as a float and comes back
#: ``1700000000.0002``, which matches no message — so a run's reports land at
#: the top of the channel, and the sending program blames a deleted message.
#: About one timestamp in ten ends in a zero.
#:
#: Conductor marks this flag hidden and internal (``cli/run.py``,
#: ``parse_input_json_flags``) while calling ``coerce_value`` a public contract
#: that must not change. So it is used only where the public one would corrupt
#: the value, never as the general way in, and
#: ``test_conformance.py::test_a_string_input_survives_the_engine_verbatim``
#: runs the installed engine to check it is still honoured.
TYPED_INPUT_FLAG = "--input-json"


def launch_command(
    executable: str,
    path: Path,
    *,
    inputs: Mapping[str, str],
    dashboard: bool,
    background: bool = False,
    workspace_instructions: bool = True,
    log_file: str | None = None,
    verbatim: Collection[str] = (),
) -> list[str]:
    """The argv that runs one compiled workflow.

    One place, so the CLI and the listener spell the flags the same way.

    ``verbatim`` names the inputs that must arrive as the text they were given,
    whatever they look like. Everything else is coerced by the engine, which is
    how an ``int`` input gets an int.
    """
    command = [executable, "run", str(path.resolve())]
    for name, value in inputs.items():
        if name in verbatim:
            command += [TYPED_INPUT_FLAG, f"{name}={json.dumps(value)}"]
        else:
            command += ["-i", f"{name}={value}"]
    if log_file is not None:
        # Verbatim: `auto` is Conductor's spelling for a generated temp path.
        command += ["--log-file", log_file]
    if workspace_instructions:
        # The provider runs every step with `setting_sources=[]`. This flag
        # walks from the working directory to the git root and prepends
        # AGENTS.md, CLAUDE.md, .github/copilot-instructions.md and
        # .github/instructions/*.instructions.md to every prompt.
        command.append("--workspace-instructions")
    if background:
        command.append("--web-bg")
    elif dashboard:
        command.append("--web")
    return command


class ConductorBackend:
    """Compiles an ictus graph to Conductor workflow YAML and drives its CLI."""

    def capabilities(self) -> Capabilities:
        """Conductor expresses every node kind ictus models."""
        return Capabilities(
            name="conductor",
            kinds=frozenset(NODE_KINDS),
            # AgentDef.provider / ProviderSettings.name (config/schema.py) is a
            # closed Literal; anything else is rejected by the loader.
            providers=frozenset(
                {"copilot", "openai", "claude", "claude-agent-sdk", "hermes", "aca"}
            ),
            signals=REPORTABLE,
            # Conductor's `tools:` holds workflow tool names, which
            # claude-agent-sdk cannot translate to CLI tool ids; it raises
            # ProviderError on a non-empty list.
            tool_allowlists=False,
            # `capabilities.session_continuity` is true for this one alone;
            # config/validator.py `_check_agent_capabilities` rejects a session_key
            # on any other.
            remembering_providers=frozenset({"claude-agent-sdk"}),
            conditional_routes=True,
            cycles=True,
            sub_graphs=True,
            notes="Loops are bounded by a single global step budget, not per cycle.",
        )

    def document(self, pipeline: Pipeline, inherited: Inherited = NOTHING_INHERITED) -> YamlDict:
        """The workflow mapping for one pipeline, before serialization."""
        # A map group's body lives inline under `for_each:`, not in `agents:`.
        # An unset system prompt is an empty one to the engine, not a default,
        # so what a step inherits is decided here and passed down.
        baseline = pipeline.system_prompt or inherited.system_prompt
        agents: list[YamlValue] = [
            agent_entry(pipeline, node, baseline)
            for node in pipeline.nodes
            if pipeline.map_of(node) is None
        ]
        doc: YamlDict = {"workflow": workflow_block(pipeline, inherited), "agents": agents}
        groups = parallel_block(pipeline)
        if groups:
            doc["parallel"] = groups
        mapped = for_each_block(pipeline)
        if mapped:
            doc["for_each"] = mapped
        exposed = output_block(pipeline)
        if exposed:
            doc["output"] = exposed
        return doc

    def compile(
        self, pipeline: Pipeline, inherited: Inherited = NOTHING_INHERITED
    ) -> list[Document]:
        """Render ``pipeline`` and every stage it contains.

        The parent first, then one file per nested stage. A stage placed twice
        is two nodes over one file.
        """
        out = [
            Document(f"{pipeline.pipeline_id}.yaml", dump_yaml(self.document(pipeline, inherited)))
        ]
        # Only at top level: a stage has no run of its own to start.
        if inherited is NOTHING_INHERITED:
            listening = manifest.render(pipeline)
            if listening:
                out.append(Document(manifest.filename_for(pipeline), listening))
        seen = {out[0].filename}
        below = inherited.under(pipeline)
        for child in pipeline.children.values():
            for rendered in self.compile(child, below):
                if rendered.filename not in seen:
                    seen.add(rendered.filename)
                    out.append(rendered)
        return out

    def lint(self, pipeline: Pipeline) -> list[str]:
        """Conductor-specific problems the generic rules cannot know."""
        return conductor_problems(pipeline)

    def preflight(self, pipeline: Pipeline, *, probe: bool) -> list[PreflightIssue]:
        """Check this environment can supply what the pipeline declares.

        Whether an MCP server is installed, its token set and its endpoint
        answering, plus declared executables, datasources and integrations.
        """
        return [
            *executable_issues(pipeline, probe=probe),
            *integration_issues(pipeline),
            *datasource_issues(pipeline),
            *preflight_issues(pipeline, probe=probe),
        ]

    def validate(self, paths: Sequence[Path]) -> list[ValidationResult]:
        """Ask Conductor's own validator whether each document loads."""
        binary = self._binary()
        results: list[ValidationResult] = []
        for path in paths:
            done = subprocess.run(
                [binary, "validate", str(path)], capture_output=True, text=True, check=False
            )
            results.append(
                ValidationResult(
                    path=path,
                    ok=done.returncode == 0,
                    detail="" if done.returncode == 0 else done.stdout + done.stderr,
                )
            )
        return results

    def run(
        self,
        path: Path,
        *,
        inputs: Mapping[str, str],
        dashboard: bool,
        background: bool = False,
        workspace_instructions: bool = True,
        working_dir: Path | None = None,
        log_file: str | None = None,
        verbatim: Collection[str] = (),
    ) -> int:
        """Run a compiled workflow, serving the dashboard by default.

        A gate is only answerable from elsewhere while the dashboard port is up.

        ``working_dir`` is where the agents read and write, and where
        ``--workspace-instructions`` starts walking. The workflow path is made
        absolute, since it is rarely inside the project being worked on.

        Detaching without a dashboard is refused: ``--web-bg`` is what detaches.
        """
        if background and not dashboard:
            raise IctusError(
                "conductor detaches with --web-bg, which serves the dashboard: a "
                "background run without one cannot be reached, and there is no flag "
                "that does it. Ask for one or the other."
            )
        command = launch_command(
            self._binary(),
            path,
            inputs=inputs,
            dashboard=dashboard,
            background=background,
            workspace_instructions=workspace_instructions,
            log_file=log_file,
            verbatim=verbatim,
        )
        return subprocess.run(command, check=False, cwd=working_dir).returncode

    def plan(self, path: Path, *, working_dir: Path | None = None) -> int:
        """Print the engine's execution plan for a compiled workflow, running nothing.

        The plan is built from the workflow file alone (``cli/run.py``,
        ``build_dry_run_plan``): inputs are not substituted and nothing is spent.
        """
        command = [self._binary(), "run", str(path.resolve()), "--dry-run"]
        return subprocess.run(command, check=False, cwd=working_dir).returncode

    @staticmethod
    def _binary() -> str:
        return binary()


conductor = ConductorBackend()
