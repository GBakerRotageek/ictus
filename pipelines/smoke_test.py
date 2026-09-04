"""Smoke test pipeline: ticket plan -> breakdown -> review -> execution -> test."""

from __future__ import annotations

from ictus.core import HumanTask, Pipeline, SimpleTask

# Define stages as task objects
plan_task = SimpleTask(name="Plan", ref_name="plan")
breakdown_task = SimpleTask(name="Breakdown", ref_name="breakdown")
review_gate = HumanTask(name="Review Gate", ref_name="review_gate")
execution_task = SimpleTask(name="Execution", ref_name="execution")
testing_task = SimpleTask(name="Testing", ref_name="testing")
finish_task = SimpleTask(name="Finish", ref_name="finish")

# Build the pipeline with type-safe references
smoke_test = Pipeline(
    name="smoke-test-pipeline",
    description="Smoke test pipeline: plan -> breakdown -> review -> execute -> test -> finish",
)

# Add tasks to pipeline
smoke_test.add_task(plan_task)
smoke_test.add_task(breakdown_task)
smoke_test.add_task(review_gate)
smoke_test.add_task(execution_task)
smoke_test.add_task(testing_task)
smoke_test.add_task(finish_task)

# Linear flow: plan -> breakdown -> review
smoke_test.add_edge(plan_task, breakdown_task)
smoke_test.add_edge(breakdown_task, review_gate)

# Branch from review_gate: rejected -> breakdown, approved -> execution
smoke_test.branch(
    from_task=review_gate,
    branches={
        "rejected": breakdown_task,
        "approved": execution_task,
    },
)

# Linear flow: execution -> testing -> finish
smoke_test.add_edge(execution_task, testing_task)
smoke_test.add_edge(testing_task, finish_task)
