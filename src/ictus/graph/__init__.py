"""The composition model — what a pipeline is made of.

No re-exports: ``ictus/__init__.py`` is the one public surface. Inside the
library, import the module you mean.

    values.py    the value domain that crosses a node boundary
    ports.py     typed inputs and outputs
    node.py      one class per Conductor agent type
    pipeline.py  the graph: control edges, data edges, loop bounds
    stage.py     a reusable sub-graph, compiled as a nested workflow
"""

from __future__ import annotations
