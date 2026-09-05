"""LLM steps for recurring shapes.

Ordinary ``type: agent`` nodes. What they carry is a prompt worth sending and a
declared output schema — which is what makes ``{{ node.output.field }}`` and
route conditions bind to anything at all.
"""

from __future__ import annotations

from ictus.stdlib.agents.briefing import briefing
from ictus.stdlib.agents.remediate import remediate
from ictus.stdlib.agents.validate_mcp import validate_mcp
from ictus.stdlib.agents.verdict import verdict

__all__ = ["briefing", "remediate", "validate_mcp", "verdict"]
