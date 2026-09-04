"""Example pipeline using stdlib gate nodes.

Demonstrates the standard library nodes for a typical approval workflow:
1. Gather data
2. Summarize it
3. Human gate for approval
4. Execute if approved, fail if rejected
5. Report status
"""

from __future__ import annotations

from ictus import (
    NodeBuilder,
    Pipeline,
    PortType,
    approval_gate,
    failure_node,
    status_report,
    success_node,
    text_summary,
)

# ============================================================================
# Custom domain nodes
# ============================================================================

# Custom node: gather requirements
gather_node = (
    NodeBuilder("Gather Requirements", description="Collect ticket requirements")
    .with_id("gather")
    .output("requirements", PortType.JSON, "Gathered requirements")
    .build()
)

# Custom node: execute work
execute_node = (
    NodeBuilder("Execute Work", description="Execute the approved work")
    .with_id("execute")
    .input("requirements", PortType.JSON, "Requirements to execute")
    .output("results", PortType.JSON, "Execution results")
    .build()
)

# ============================================================================
# Use stdlib gate nodes
# ============================================================================

# Pre-built summary node - formats requirements for human review
summary_node = text_summary(name="Summarize Requirements", node_id="summarize_reqs")

# Pre-built approval gate - human reviews summary and approves/rejects
gate_node = approval_gate(name="Review & Approve", node_id="review_approve")

# Pre-built terminal nodes
success = success_node(name="Approved", node_id="approved")
failure = failure_node(name="Rejected", node_id="rejected")

# Pre-built status report - documents final outcome
report_node = status_report(name="Final Report", node_id="final_report")

# ============================================================================
# Build pipeline with gates
# ============================================================================

pipeline = Pipeline(
    name="Approval Workflow Example",
    pipeline_id="approval_workflow",
    description="Gather data -> Summarize -> Human approval -> Execute/Fail",
)

# Add all nodes
gather_elem = pipeline.add_node(gather_node)
summary_elem = pipeline.add_node(summary_node)
gate_elem = pipeline.add_node(gate_node)
execute_elem = pipeline.add_node(execute_node)
success_elem = pipeline.add_node(success)
failure_elem = pipeline.add_node(failure)
report_elem = pipeline.add_node(report_node)

# Linear flow: gather -> summarize -> gate
pipeline.connect(gather_elem, summary_elem, from_port="requirements", to_port="data")
pipeline.connect(summary_elem, gate_elem, from_port="summary", to_port="summary")

# Branching from gate: approved -> execute, rejected -> failure
pipeline.branch(
    gate_elem,
    branches={
        "approved": execute_elem,
        "rejected": failure_elem,
    },
)

# Execution path: execute -> success -> report
pipeline.connect(execute_elem, success_elem, from_port="results", to_port="results")
pipeline.connect(success_elem, report_elem, from_port="results", to_port="results")
