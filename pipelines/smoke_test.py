"""Smoke test pipeline using the three-tier node/stage/pipeline hierarchy.

Demonstrates:
- Nodes with typed inputs/outputs
- Stages that compose nodes
- Pipelines that compose stages
- Composition-time validation of port connections
- Branching with human approval gates
"""

from __future__ import annotations

from ictus import NodeBuilder, Pipeline, PortType

# ============================================================================
# Define nodes with typed I/O
# ============================================================================

# Plan node: takes a ticket, outputs the plan text
plan_node = (
    NodeBuilder("Plan", description="Parse and validate ticket")
    .with_id("plan")
    .output("plan", PortType.TEXT, "Structured ticket plan")
    .build()
)

# Breakdown node: takes plan, outputs breakdown steps
breakdown_node = (
    NodeBuilder("Breakdown", description="Break down plan into steps")
    .with_id("breakdown")
    .input("plan", PortType.TEXT, "Ticket plan to break down")
    .output("steps", PortType.JSON, "Array of breakdown steps")
    .build()
)

# Review gate: takes breakdown, outputs decision
review_gate_node = (
    NodeBuilder("Review Gate", description="Human approval of breakdown")
    .with_id("review_gate")
    .input("steps", PortType.JSON, "Steps to review")
    .output("decision", PortType.STRING, "approved or rejected")
    .build()
)

# Execution node: takes steps, outputs results
execution_node = (
    NodeBuilder("Execution", description="Execute breakdown steps")
    .with_id("execution")
    .input("steps", PortType.JSON, "Steps to execute")
    .output("results", PortType.JSON, "Execution results")
    .build()
)

# Testing node: takes results, outputs test report
testing_node = (
    NodeBuilder("Testing", description="Test results")
    .with_id("testing")
    .input("results", PortType.JSON, "Results to test")
    .output("report", PortType.TEXT, "Test report")
    .build()
)

# Finish node: takes report, outputs status
finish_node = (
    NodeBuilder("Finish", description="Finalize workflow")
    .with_id("finish")
    .input("report", PortType.TEXT, "Test report")
    .output("status", PortType.STRING, "Final status")
    .build()
)

# ============================================================================
# Build the pipeline with composition-time validation
# ============================================================================

smoke_test = Pipeline(
    name="Smoke Test Pipeline",
    pipeline_id="smoke-test-pipeline",
    description="Ticket plan -> breakdown -> review gate -> execute -> test -> finish",
)

# Add nodes to pipeline
plan_elem = smoke_test.add_node(plan_node)
breakdown_elem = smoke_test.add_node(breakdown_node)
review_elem = smoke_test.add_node(review_gate_node)
execution_elem = smoke_test.add_node(execution_node)
testing_elem = smoke_test.add_node(testing_node)
finish_elem = smoke_test.add_node(finish_node)

# Connect with composition-time type checking
# plan -> breakdown
smoke_test.connect(
    plan_elem,
    breakdown_elem,
    from_port="plan",
    to_port="plan",
)

# breakdown -> review_gate
smoke_test.connect(
    breakdown_elem,
    review_elem,
    from_port="steps",
    to_port="steps",
)

# branching: review_gate -> rejected (back to breakdown) or approved (to execution)
# For branching, we use the default connection first, then add branches
smoke_test.branch(
    review_elem,
    branches={
        "rejected": breakdown_elem,
        "approved": execution_elem,
    },
)

# execution -> testing
smoke_test.connect(
    execution_elem,
    testing_elem,
    from_port="results",
    to_port="results",
)

# testing -> finish
smoke_test.connect(
    testing_elem,
    finish_elem,
    from_port="report",
    to_port="report",
)
