"""Typed references to values produced elsewhere in the graph.

A prompt used to be a string containing an engine's template syntax, which had
two costs. It put one engine's dialect into user code, where no library refactor
can reach it. And it made the reference unverifiable except by regular
expression — the lint that checked whether a referenced field existed did so by
parsing the emitted format back out of author text.

A ``Ref`` is checked when it is written: the port must exist, and its type comes
with it. How a reference is spelled is then the backend's problem, and the
guard a deferred reference needs can be added by the compiler rather than
remembered by the author.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ictus.graph.node import Node
    from ictus.graph.pipeline import WorkflowInput
    from ictus.graph.ports import PortType

__all__ = [
    "Comparison",
    "OptionalBlock",
    "Ref",
    "Template",
    "TemplatePart",
    "equals",
    "not_equals",
    "optional",
    "ref_to",
    "tpl",
]


@dataclass(frozen=True, slots=True)
class Ref:
    """A reference to one output port of one node, or to a workflow input.

    ``source`` is kept so a lint can check the referenced node is actually in
    the pipeline — an identity check the old regex over prompt text could never
    make. ``from_input`` is set by whichever constructor made the reference, so
    nothing has to re-derive which sort it is.
    """

    source_id: str
    port: str
    port_type: PortType
    from_input: bool = False
    source: Node | WorkflowInput | None = None
    fallback: str | None = None
    """Value to use when this reference resolves to nothing.

    Not decoration: a gate's free-text field exists only on the branch that
    asked for it, and reading it on any other branch is a hard template error
    that kills the run. Verified against the engine, not assumed.
    """

    def or_else(self, fallback: str) -> Ref:
        """The same reference, rendered as ``fallback`` when it has no value."""
        return Ref(
            source_id=self.source_id,
            port=self.port,
            port_type=self.port_type,
            from_input=self.from_input,
            source=self.source,
            fallback=fallback,
        )


@dataclass(frozen=True, slots=True)
class OptionalBlock:
    """A run of template parts that only renders once its references resolve.

    Needed because a deferred reference usually comes with prose that makes no
    sense without it — "a previous draft was rejected with these notes" reads as
    a lie on the first pass through a loop.
    """

    parts: tuple[TemplatePart, ...]

    def refs(self) -> Iterator[Ref]:
        """Every reference inside this block, nesting included."""
        for part in self.parts:
            if isinstance(part, Ref):
                yield part
            elif isinstance(part, Template | OptionalBlock | Comparison):
                yield from part.refs()


@dataclass(frozen=True, slots=True)
class Comparison:
    """A reference tested against a value, for use as a route condition.

    ``tpl(ref)`` interpolates — ``{{ x }}`` — which is right for a boolean and
    useless for anything else. Choosing a branch by a gate's answer needs the
    test *inside* the braces, which is a different shape entirely.
    """

    ref: Ref
    value: str
    negated: bool = False

    def refs(self) -> Iterator[Ref]:
        """The reference under test."""
        yield self.ref


TemplatePart = Union[str, Ref, Comparison, "OptionalBlock", "Template"]


@dataclass(frozen=True, slots=True)
class Template:
    """Literal text interleaved with typed references."""

    parts: tuple[TemplatePart, ...]

    def refs(self) -> Iterator[Ref]:
        """Every reference in the template, nesting included.

        Templates compose: one built for a caller can be dropped whole into
        another, which is what lets a stage pass a failure report into a helper
        without flattening it back to a string first.
        """
        for part in self.parts:
            if isinstance(part, Ref):
                yield part
            elif isinstance(part, Template | OptionalBlock | Comparison):
                yield from part.refs()

    def literal_text(self) -> str:
        """Just the prose, for error messages and previews."""
        return "".join(p for p in self.parts if isinstance(p, str))


def ref_to(node_id: str, port: str, port_type: PortType) -> Ref:
    """A reference to a node that does not exist yet.

    Needed for a loop's back-edge: the producer's prompt reads the reviewer's
    notes, and the reviewer's prompt reads the producer's draft, so whichever is
    built first must name the other. Unlike a raw template string this is still
    checked — the lint resolves ``node_id`` and ``port`` against the finished
    graph and compares the declared type.
    """
    return Ref(source_id=node_id, port=port, port_type=port_type)


def tpl(*parts: TemplatePart) -> Template:
    """Build a template from literal text and references."""
    return Template(tuple(parts))


def equals(ref: Ref, value: str) -> Template:
    """A condition that holds when ``ref`` equals ``value``."""
    return Template((Comparison(ref=ref, value=value),))


def not_equals(ref: Ref, value: str) -> Template:
    """A condition that holds when ``ref`` does not equal ``value``."""
    return Template((Comparison(ref=ref, value=value, negated=True),))


def optional(*parts: TemplatePart) -> OptionalBlock:
    """A block that renders only when the references inside it have resolved."""
    return OptionalBlock(tuple(parts))
