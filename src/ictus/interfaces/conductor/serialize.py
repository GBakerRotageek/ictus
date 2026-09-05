"""Rendering a compiled document to YAML text.

Two settings here are correctness, not formatting. Wrapping is disabled outright:
ruamel wraps a long double-quoted scalar at a line break without the trailing
continuation marker, and reloading then turns that break into a space — silently
editing the prompt an agent runs, invisibly in a diff. Multi-line strings are
emitted as block scalars, which carry their line breaks literally and read as
prose in the file.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

if TYPE_CHECKING:
    from ictus.graph.values import YamlDict, YamlValue

__all__ = ["dump_yaml"]


def _yaml() -> YAML:
    yaml = YAML()
    yaml.default_flow_style = False
    # Never let the emitter wrap a scalar. Wrapping a long double-quoted string
    # at a line break omits the trailing continuation marker, and reloading then
    # turns that break into a space — silently editing a prompt.
    yaml.width = 1 << 30
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def blockify(value: YamlValue) -> YamlValue:
    """Render multi-line strings as block scalars.

    A prompt is the substance of an agent and is usually several lines. Block
    style keeps it readable in the emitted file and, unlike a quoted scalar,
    carries its line breaks literally.
    """
    if isinstance(value, dict):
        return {key: blockify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [blockify(item) for item in value]
    if isinstance(value, str) and "\n" in value and block_safe(value):
        return LiteralScalarString(value)
    return value


def block_safe(text: str) -> bool:
    """Whether block style round-trips this string exactly.

    Trailing whitespace on a line and carriage returns are not recoverable from
    a block scalar, so those stay quoted.
    """
    if "\r" in text or "\t" in text:
        return False
    return not any(line != line.rstrip() for line in text.split("\n"))


def dump_yaml(document: YamlDict) -> str:
    """Render a compiled document to YAML text."""
    buf = io.StringIO()
    _yaml().dump(blockify(document), buf)
    return buf.getvalue()
