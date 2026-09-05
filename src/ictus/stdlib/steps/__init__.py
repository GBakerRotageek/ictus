"""Steps that cost no model call.

Every one of these is a step type Conductor implements natively. Modelling any
of them as an LLM agent means a billable, non-deterministic call standing in for
something the engine does for free.
"""

from __future__ import annotations

from ictus.stdlib.steps.bindings import bindings
from ictus.stdlib.steps.constant import constant
from ictus.stdlib.steps.counter import counter
from ictus.stdlib.steps.save_text import save_text
from ictus.stdlib.steps.shell import shell
from ictus.stdlib.steps.wait import wait

__all__ = ["bindings", "constant", "counter", "save_text", "shell", "wait"]
