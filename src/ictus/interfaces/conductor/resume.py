"""Reading a Conductor checkpoint, and saying what resuming it will repeat.

Conductor resumes at the granularity it checkpoints at, which is coarser than a
run looks (verified by hard-killing runs and resuming them):

* A checkpoint is written at each *root* step boundary (``runtime.checkpoint.
  every_agent``, which this backend always emits) and when a step raises. The
  step named in it is the one that was running or about to — it starts over.
* A sub-workflow is never checkpointed inside (``_periodic_checkpoints_active``
  is root-only), so a stage interrupted part-way restarts from its first step.
  A step in it that had already run, runs twice.
* A parallel or for-each group's boundary is after the whole group, so every
  member or item runs again.
* A provider whose ``CAPABILITIES.checkpoint_resume`` is false does not carry a
  session across, so a step that remembers a conversation starts without it.

None of that can be changed from here. What can be is that nobody discovers it
from the repeated side effects: the plan names each of these before resuming.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ictus.errors import IctusError
from ictus.graph.node import AgentNode, ScriptNode, SubGraphNode
from ictus.graph.pipeline import ParallelGroup
from ictus.interfaces import ResumePlan
from ictus.interfaces.conductor.workflow import DEFAULT_PROVIDER

if TYPE_CHECKING:
    from pathlib import Path

    from ictus.graph.mapping import MapGroup
    from ictus.graph.node import Node
    from ictus.graph.pipeline import Pipeline

__all__ = [
    "SESSION_RESTORING_PROVIDERS",
    "CheckpointError",
    "checkpoint_dir",
    "latest_checkpoint",
    "resume_plan",
]

#: ``CAPABILITIES.checkpoint_resume`` is true for these and false for
#: claude-agent-sdk, claude, openai and aca (providers/<name>.py).
SESSION_RESTORING_PROVIDERS = frozenset({"copilot", "hermes"})


class CheckpointError(IctusError):
    """A saved checkpoint could not be read as one."""


def checkpoint_dir(state_dir: Path) -> Path:
    """Where Conductor writes checkpoints when ``TMPDIR`` is ``state_dir``.

    ``CheckpointManager.get_checkpoints_dir``: ``tempfile.gettempdir()``, then
    ``conductor/checkpoints``. The backend sets ``TMPDIR`` to steer it.
    """
    return state_dir / "conductor" / "checkpoints"


def latest_checkpoint(workflow: Path, state_dir: Path) -> tuple[Path, dict[str, object]] | None:
    """The newest checkpoint for ``workflow`` in ``state_dir``, the way Conductor picks it.

    ``CheckpointManager.list_checkpoints``: files named ``<stem>-*.json``,
    newest by ``created_at`` — a UTC ISO timestamp, so ordering the text orders
    the instants. Conductor skips a file it cannot parse and resumes from an
    older one; this refuses instead, since that older one is not what the
    person asking to resume was told they would get.
    """
    directory = checkpoint_dir(state_dir)
    if not directory.is_dir():
        return None
    found: list[tuple[str, Path, dict[str, object]]] = []
    for path in directory.glob(f"{workflow.stem}-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"checkpoint {path} could not be read: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("created_at"), str):
            raise CheckpointError(f"checkpoint {path} has no 'created_at'; not a checkpoint")
        found.append((str(data["created_at"]), path, data))
    if not found:
        return None
    _, path, data = max(found, key=lambda entry: entry[0])
    return path, data


def resume_plan(pipeline: Pipeline, workflow: Path, state_dir: Path) -> ResumePlan | None:
    """Read the newest checkpoint and say what continuing from it repeats."""
    latest = latest_checkpoint(workflow, state_dir)
    if latest is None:
        return None
    path, data = latest
    step = data.get("current_agent")
    context = data.get("context")
    history = context.get("execution_history") if isinstance(context, dict) else None
    if not isinstance(step, str) or not isinstance(history, list):
        raise CheckpointError(
            f"checkpoint {path} lacks 'current_agent' or 'context.execution_history'"
        )
    failure = data.get("failure")
    message = failure.get("message") if isinstance(failure, dict) else None
    reruns = _reruns(pipeline, step, where=path)
    return ResumePlan(
        step=step,
        completed=tuple(str(name) for name in history),
        reruns=tuple(name for name, _ in reruns),
        scripts=tuple(name for name, node in reruns if isinstance(node, ScriptNode)),
        cold_sessions=_cold_sessions(pipeline, pipeline.provider or DEFAULT_PROVIDER, ""),
        saved_at=str(data["created_at"]),
        reason=message if data.get("trigger") == "failure" and isinstance(message, str) else None,
        source=path,
    )


def _reruns(pipeline: Pipeline, step: str, *, where: Path) -> list[tuple[str, Node]]:
    """Every step that starts over when ``step`` does, qualified by its stage."""
    by_id = {node.node_id: node for node in pipeline.nodes}
    groups: dict[str, ParallelGroup | MapGroup] = {
        **{group.group_id: group for group in pipeline.groups},
        **{group.group_id: group for group in pipeline.maps},
    }
    if step in groups:
        group = groups[step]
        members = group.members if isinstance(group, ParallelGroup) else (group.body,)
        found: list[tuple[str, Node]] = []
        for member in members:
            found += _expand(pipeline, member, prefix=f"{step}/")
        return found
    node = by_id.get(step)
    if node is None:
        raise CheckpointError(
            f"checkpoint {where} resumes at {step!r}, which pipeline "
            f"{pipeline.pipeline_id!r} does not have"
        )
    return _expand(pipeline, node, prefix="")


def _expand(pipeline: Pipeline, node: Node, *, prefix: str) -> list[tuple[str, Node]]:
    """A node, or everything inside it when it is a stage that restarts whole."""
    if not isinstance(node, SubGraphNode):
        return [(f"{prefix}{node.node_id}", node)]
    child = pipeline.children[node.node_id]
    inner = f"{prefix}{node.node_id}/"
    found: list[tuple[str, Node]] = []
    for member in child.nodes:
        found += _expand(child, member, prefix=inner)
    return found


def _cold_sessions(pipeline: Pipeline, inherited: str, prefix: str) -> tuple[str, ...]:
    """Every step that remembers a session on a provider that cannot restore one.

    The whole graph, not only what reruns: a session that did not survive is
    gone for every later pass of that step too.
    """
    provider = pipeline.provider or inherited
    found: list[str] = []
    for node in pipeline.nodes:
        if (
            isinstance(node, AgentNode)
            and node.session_key is not None
            and (node.provider or provider) not in SESSION_RESTORING_PROVIDERS
        ):
            found.append(f"{prefix}{node.node_id}")
        if isinstance(node, SubGraphNode):
            found += _cold_sessions(
                pipeline.children[node.node_id], provider, f"{prefix}{node.node_id}/"
            )
    return tuple(found)
