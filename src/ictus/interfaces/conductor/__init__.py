"""The Conductor backend.

Everything Conductor-shaped lives under this package: its field names, its
template dialect, its iteration accounting, its terminal marker, its CLI. If a
Conductor spelling appears anywhere above ``ictus.interfaces``, that is a defect
with a name.

    workflow.py   the ``workflow:`` block and the defaults worth stating
    agents.py     one node to one ``agents:`` entry
    serialize.py  YAML text, without altering any value
    lints.py      rules that are true because of how Conductor runs
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from ictus.graph.node import NODE_KINDS
from ictus.interfaces import Capabilities, Document, PreflightIssue, ValidationResult
from ictus.interfaces.conductor.agents import agent_entry
from ictus.interfaces.conductor.lints import conductor_problems
from ictus.interfaces.conductor.mapping import for_each_block
from ictus.interfaces.conductor.mcp import preflight_issues
from ictus.interfaces.conductor.parallel import parallel_block
from ictus.interfaces.conductor.serialize import dump_yaml
from ictus.interfaces.conductor.templates import output_block
from ictus.interfaces.conductor.workflow import NOTHING_INHERITED, Inherited, workflow_block

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ictus.graph.pipeline import Pipeline
    from ictus.graph.values import YamlDict, YamlValue

__all__ = ["ConductorBackend", "conductor"]

BINARY = "conductor"


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
            conditional_routes=True,
            cycles=True,
            sub_graphs=True,
            notes="Loops are bounded by a single global step budget, not per cycle.",
        )

    def document(self, pipeline: Pipeline, inherited: Inherited = NOTHING_INHERITED) -> YamlDict:
        """The workflow mapping for one pipeline, before serialization.

        Public because the backend's own tests assert on the structure; the
        ``Backend`` protocol only promises rendered text.
        """
        # A map group's body lives inline under `for_each:`; emitting it here as
        # well would leave a step Conductor schedules once on its own.
        agents: list[YamlValue] = [
            agent_entry(pipeline, node) for node in pipeline.nodes if pipeline.map_of(node) is None
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

        The parent comes first; each nested stage follows as its own file,
        because ``type: workflow`` references a sibling rather than inlining a
        graph. A stage placed twice in one parent is two nodes over one file.
        """
        out = [
            Document(f"{pipeline.pipeline_id}.yaml", dump_yaml(self.document(pipeline, inherited)))
        ]
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

        Conductor validates that a provider *can* honour MCP. Whether the server
        is installed, the token is set and the endpoint answers is checked here,
        because nothing else checks it and the failure otherwise lands mid-run.
        """
        return preflight_issues(pipeline, probe=probe)

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
        working_dir: Path | None = None,
    ) -> int:
        """Run a compiled workflow, serving the dashboard by default.

        A gate is only answerable from elsewhere while the run has a dashboard
        port, and mid-run guidance needs one too.

        ``working_dir`` is the directory the agents read and write in: Conductor
        resolves script paths and the model's own tools against the process's
        cwd, so this is what "run it on that project" means. The workflow path
        is made absolute first, because it is almost never inside the project
        being worked on.
        """
        command = [self._binary(), "run", str(path.resolve())]
        for name, value in inputs.items():
            command += ["-i", f"{name}={value}"]
        if background:
            command.append("--web-bg")
        elif dashboard:
            command.append("--web")
        return subprocess.run(command, check=False, cwd=working_dir).returncode

    @staticmethod
    def _binary() -> str:
        found = shutil.which(BINARY)
        if found is None:
            raise FileNotFoundError(
                f"{BINARY!r} is not on PATH; the Conductor backend cannot check or run "
                "what it compiles without it"
            )
        return found


conductor = ConductorBackend()
