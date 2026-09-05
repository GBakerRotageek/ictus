"""The composition model — what a pipeline is made of.

Deliberately holds no re-exports. ``ictus/__init__.py`` is the one public
surface; a second package re-exporting the same names is how the previous
``core/`` shim came to duplicate it. Inside the library, import the module you
mean: ``from ictus.graph.node import AgentNode``.

    values.py    the value domain that crosses a node boundary
    ports.py     typed inputs and outputs
    node.py      one class per Conductor agent type
    pipeline.py  the graph: control edges, data edges, loop bounds
    stage.py     a reusable sub-graph, compiled as a nested workflow
"""

from __future__ import annotations
