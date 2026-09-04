from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")

# Type alias for serialized data (heterogeneous dicts for YAML/JSON)
SerializedDict = dict[str, object]


class TaskType(StrEnum):
    """Task execution type (maps to workflow engine agent types)."""

    AGENT = "agent"  # worker/service agent
    HUMAN_GATE = "human_gate"  # requires human approval
    SCRIPT = "script"  # runs a script
    WORKFLOW = "workflow"  # calls another workflow


@dataclass
class Task(ABC):
    """Base task type. Subclass to define specific task behaviors."""

    name: str
    ref_name: str = field(default="")

    def __post_init__(self) -> None:
        if not self.ref_name:
            self.ref_name = self.name

    @abstractmethod
    def task_type(self) -> TaskType:
        """Return the Conductor task type."""

    @abstractmethod
    def to_dict(self) -> SerializedDict:
        """Serialize to Conductor task dict."""


@dataclass
class SimpleTask(Task):
    """A task that invokes a worker/service."""

    def task_type(self) -> TaskType:
        return TaskType.AGENT

    def to_dict(self) -> SerializedDict:
        return {
            "name": self.name,
            "task_id": self.ref_name,
            "type": self.task_type().value,
        }


@dataclass
class HumanTask(Task):
    """A task requiring human approval/review."""

    def task_type(self) -> TaskType:
        return TaskType.HUMAN_GATE

    def to_dict(self) -> SerializedDict:
        return {
            "name": self.name,
            "task_id": self.ref_name,
            "type": self.task_type().value,
        }


@dataclass
class BranchTask(Task):
    """Task that branches execution based on a condition."""

    case_expr: str = field(default="")

    def task_type(self) -> TaskType:
        return TaskType.AGENT

    def to_dict(self) -> SerializedDict:
        return {
            "name": self.name,
            "task_id": self.ref_name,
            "type": self.task_type().value,
        }


@dataclass
class TaskRef:
    """Reference to another task in the pipeline. Ensures type safety."""

    task: Task

    def __call__(self) -> Task:
        return self.task


@dataclass
class Edge:
    """An edge in the workflow graph connecting tasks."""

    from_task: Task
    to_task: Task | None = None
    case: str | None = None  # for branch conditions


@dataclass
class Pipeline:
    """A Conductor workflow pipeline."""

    name: str
    description: str = ""
    tasks: list[Task] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    start_task: Task | None = None

    def add_task(self, task: Task) -> TaskRef:
        """Add a task to the pipeline and return a reference."""
        self.tasks.append(task)
        if self.start_task is None:
            self.start_task = task
        return TaskRef(task)

    def add_edge(self, from_task: Task, to_task: Task, case: str | None = None) -> None:
        """Add an edge between tasks."""
        self.edges.append(Edge(from_task, to_task, case))

    def branch(
        self, from_task: Task, branches: dict[str, Task], default: Task | None = None
    ) -> None:
        """Add conditional branching edges."""
        for case, to_task in branches.items():
            self.add_edge(from_task, to_task, case)
        if default:
            self.add_edge(from_task, default, None)

    def to_dict(self) -> SerializedDict:
        """Serialize pipeline to Conductor workflow dict."""
        if not self.tasks:
            raise ValueError("Pipeline must have at least one task")
        if self.start_task is None:
            raise ValueError("Pipeline must have a start task")

        # Build agent definitions with transitions
        agents: list[SerializedDict] = []
        for task in self.tasks:
            agent_dict = task.to_dict()

            # Find outgoing edges
            outgoing = [e for e in self.edges if e.from_task == task]

            if outgoing:
                if len(outgoing) == 1 and outgoing[0].case is None:
                    # Simple linear edge
                    if outgoing[0].to_task:
                        agent_dict["next_task_id"] = outgoing[0].to_task.ref_name
                else:
                    # Branching - build transitions dict
                    transitions: dict[str, str] = {}
                    default_transition: str | None = None
                    for edge in outgoing:
                        if edge.case:
                            if edge.to_task:
                                transitions[edge.case] = edge.to_task.ref_name
                        else:
                            default_transition = edge.to_task.ref_name if edge.to_task else None
                    agent_dict["transitions"] = transitions
                    if default_transition:
                        agent_dict["default_transition"] = default_transition

            agents.append(agent_dict)

        # Workflow definition with entry point
        workflow_def: SerializedDict = {
            "name": self.name,
            "description": self.description,
            "version": "1",
            "entry_point": self.start_task.ref_name,
        }

        return {
            "workflow": workflow_def,
            "agents": agents,
        }
