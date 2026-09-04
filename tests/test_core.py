"""Tests for the core pipeline composition module."""

from __future__ import annotations

from ictus.core import HumanTask, SimpleTask, TaskType


def test_simple_task_creation() -> None:
    """Test creating a simple task."""
    task = SimpleTask(name="Test Task")
    assert task.name == "Test Task"
    assert task.ref_name == "Test Task"
    assert task.task_type() == TaskType.AGENT


def test_human_task_creation() -> None:
    """Test creating a human approval task."""
    task = HumanTask(name="Review Gate")
    assert task.name == "Review Gate"
    assert task.task_type() == TaskType.HUMAN_GATE


def test_task_custom_ref_name() -> None:
    """Test setting a custom reference name for a task."""
    task = SimpleTask(name="My Task", ref_name="my_task_ref")
    assert task.name == "My Task"
    assert task.ref_name == "my_task_ref"


def test_simple_task_to_dict() -> None:
    """Test serializing a simple task to dict."""
    task = SimpleTask(name="Plan", ref_name="plan")
    result = task.to_dict()
    assert result["name"] == "Plan"
    assert result["task_id"] == "plan"
    assert result["type"] == "agent"
