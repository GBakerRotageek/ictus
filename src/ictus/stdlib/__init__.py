"""Standard library of reusable nodes for common workflow patterns.

Organized into focused modules:
- gates: Human approval/choice points
- summary: Data formatting and reporting
- terminal: Workflow endpoint nodes
- utilities: Data transformation and flow control
- branching: Conditional routing nodes
- builders: Pre-built node combinations
"""

from __future__ import annotations

from ictus.stdlib.branching import if_then_node, switch_node
from ictus.stdlib.builders import build_approval_gate_pair, build_summary_approval_gate
from ictus.stdlib.gates import approval_gate, multi_choice_gate
from ictus.stdlib.summary import status_report, text_summary
from ictus.stdlib.terminal import failure_node, success_node
from ictus.stdlib.utilities import (
    filter_array,
    log_node,
    merge_data,
    transform_data,
    wait_node,
)

__all__ = [
    "approval_gate",
    "build_approval_gate_pair",
    "build_summary_approval_gate",
    "failure_node",
    "filter_array",
    "if_then_node",
    "log_node",
    "merge_data",
    "multi_choice_gate",
    "status_report",
    "success_node",
    "switch_node",
    "text_summary",
    "transform_data",
    "wait_node",
]
