"""The value domain.

Every value that crosses a node boundary ends up in Conductor's YAML, so the
set of representable values is exactly the set YAML represents. Naming that
union is what lets the emitter be type-checked end to end — the previous
``dict[str, object]`` alias is why ``mypy --strict`` certified output that
Conductor rejected.
"""

from __future__ import annotations

type YamlScalar = str | int | float | bool | None
type YamlValue = YamlScalar | list[YamlValue] | dict[str, YamlValue]
type YamlDict = dict[str, YamlValue]
