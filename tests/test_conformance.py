"""The gate: everything ictus emits must load in Conductor.

This is the test whose absence let five commits of unloadable YAML ship green.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from conftest import REPO_ROOT, pipeline_roots

from ictus import (
    END,
    AgentNode,
    ComputeNode,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    ScriptNode,
    WaitNode,
)
from ictus.config import read_config
from ictus.graph.pipeline import Pipeline as PipelineType
from ictus.stdlib import approval_gate, choice_gate, succeed

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

S = PortType.STRING


def _authored_pipelines() -> list[tuple[str, PipelineType]]:
    out: list[tuple[str, PipelineType]] = []
    for root in pipeline_roots():
        out.extend(_pipelines_under(root))
    assert out, "no authored pipelines were found; the conformance suite would check nothing"
    return out


def _pipelines_under(root: Path) -> list[tuple[str, PipelineType]]:
    out: list[tuple[str, PipelineType]] = []
    for path in sorted(root.glob("*/pipeline.py")):
        # Folder names are unique per root but not across roots, and every
        # folder's module is called `pipeline`; qualifying by root keeps two
        # of them from overwriting each other in sys.modules.
        spec = importlib.util.spec_from_file_location(f"{root.name}.{path.parent.name}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        nested = {
            id(c)
            for name in dir(module)
            if isinstance(getattr(module, name), PipelineType)
            for c in getattr(module, name).children.values()
        }
        # The CLI applies the folder's config before anything else looks at the
        # pipeline; a test that skipped it would be checking a workflow that
        # never ships — with no provider, and so no session continuity.
        settings = read_config(path.parent / "config.yaml")
        for name in sorted(dir(module)):
            value = getattr(module, name)
            if isinstance(value, PipelineType) and id(value) not in nested:
                settings.apply(value, where=str(path.parent / "config.yaml"))
                out.append((f"{path.parent.name}:{name}", value))
    return out


# Evaluated once: each entry executes a pipeline module, so calling this twice
# to build the ids as well would import every folder a second time.
AUTHORED = _authored_pipelines()


@pytest.mark.parametrize(
    "pipeline",
    [p for _, p in AUTHORED],
    ids=[label for label, _ in AUTHORED],
)
def test_authored_pipelines_load_in_conductor(
    pipeline: PipelineType, validates: Callable[[PipelineType], None]
) -> None:
    """Every pipeline under a pipelines/ root must be loadable."""
    validates(pipeline)


@pytest.mark.parametrize("root", pipeline_roots(), ids=lambda r: r.name)
def test_committed_yaml_matches_a_fresh_emit(root: Path, tmp_path: Path) -> None:
    """Each folder's build/ is committed so diffs show what runs; it must be current."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ictus.cli",
            "emit",
            str(root.relative_to(REPO_ROOT)),
            "--out",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    fresh = {p.name: p.read_text() for p in tmp_path.glob("*.yaml")}
    on_disk = {p.name: p.read_text() for p in root.glob("*/build/*.yaml")}
    assert fresh, f"{root} emitted nothing, so this compared two empty sets"
    assert fresh == on_disk, (
        f"a pipeline folder's build/ under {root.name} is stale; run `make emit`"
    )


def test_every_node_kind_loads(validates: Callable[[PipelineType], None]) -> None:
    """One pipeline containing every kind ictus can emit."""
    p = Pipeline(pipeline_id="all-kinds", description="every node kind")
    param = p.declare_input("subject", S)

    agent = p.add(
        AgentNode(
            node_id="think",
            inputs=(InputPort("subject", S),),
            prompt="Consider {{ workflow.input.subject }}",
            declared_outputs=(OutputPort("idea", S),),
        )
    )
    script = p.add(
        ScriptNode(
            node_id="check",
            command="/bin/echo",
            args=("ok",),
            inputs=(InputPort("idea", S),),
            declared_outputs=(OutputPort("stdout", S),),
        )
    )
    setter = p.add(ComputeNode(node_id="stamp", value="done", value_type=S))
    waiter = p.add(WaitNode(node_id="settle", duration=1.5))
    gate = p.add(
        choice_gate(
            node_id="pick",
            prompt="Ship it?\n{{ check.output.stdout }}",
            choices=[("ship", "Ship"), ("hold", "Hold")],
            inputs=(InputPort("stdout", S),),
        )
    )
    shipped = p.add(succeed(node_id="shipped", reason="shipped"))
    held = p.add(succeed(node_id="held", reason="held"))

    p.connect_input(param, agent, "subject")
    p.connect(agent, "idea", script, "idea")
    p.route(script, setter)
    p.route(setter, waiter)
    p.feed(script, "stdout", gate, "stdout")
    p.route(waiter, gate)
    p.branch(gate, {"ship": shipped, "hold": held})
    validates(p)


def test_conditional_routes_and_end_load(validates: Callable[[PipelineType], None]) -> None:
    """A machine-decided branch with a catch-all, plus an explicit $end."""
    p = Pipeline(pipeline_id="conditional", description="condition + $end")
    start = p.add(
        AgentNode(
            node_id="judge",
            prompt="Is this ready?",
            declared_outputs=(OutputPort("ready", PortType.BOOLEAN),),
        )
    )
    ready = p.add(succeed(node_id="ok", reason="ready"))
    p.connect(
        start,
        "ready",
        p.add(
            AgentNode(
                node_id="fixup",
                inputs=(InputPort("ready", PortType.BOOLEAN),),
                prompt="Fix it",
                declared_outputs=(OutputPort("done", S),),
            )
        ),
        "ready",
        when="{{ not judge.output.ready }}",
    )
    p.route(start, ready, when="{{ judge.output.ready }}")
    p.route(start, END)
    validates(p)


def test_gate_with_notes_loop_loads(validates: Callable[[PipelineType], None]) -> None:
    """The revise loop: a gate routing backwards, feeding its notes into the retry."""
    p = Pipeline(pipeline_id="revise-loop", description="gate loop", loop_passes=4)
    draft = p.add(
        AgentNode(
            node_id="draft",
            inputs=(InputPort("notes", S, optional=True),),
            prompt="Write a draft. {{ review.output.additional_input.notes }}",
            declared_outputs=(OutputPort("text", S),),
        )
    )
    review = p.add(
        approval_gate(
            node_id="review",
            prompt="Accept?\n{{ draft.output.text }}",
            inputs=(InputPort("text", S),),
        )
    )
    done = p.add(succeed(node_id="accepted", reason="accepted"))
    p.set_entry(draft)  # every node has an inbound edge in a pure loop
    p.connect(draft, "text", review, "text")
    p.branch(review, {"approved": done, "rejected": draft})
    p.feed(review, "notes", draft, "notes")
    validates(p)
    # The bound is the backend's arithmetic; the graph only insists one exists.
    p.require_loop_bound()
