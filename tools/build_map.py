"""Generate `MAP.md`: every module under `src/ictus/`, and what it defines.

The map exists to turn "which file, and where in it?" into one targeted grep.
Agents locating code reliably find the right *file* and then miss the right
*span*, so the useful thing to hand them is the symbol name — with it,
`rg -n 'def branch' src/ictus/graph/pipeline.py` is a single call.

Deliberately no line numbers. They would make the map more precise and would
also invalidate it on every edit, and a committed artifact that goes stale
hourly gets regenerated without being read, then deleted. Names churn when the
API changes, which is exactly when the map should change.

Committed and checked, like each pipeline's `build/`: run `make map` after
adding or removing a public name, and `--check` is what fails if you did not.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "src" / "ictus"
MAP = REPO_ROOT / "MAP.md"

HEADER = """# Map

`src/ictus/`, module by module: what each holds and the names it defines.
Generated — run `make map` after adding or removing a public name.

Use it to skip the search, not to skip the file: a name here plus
`rg -n 'def <name>' <module>` lands on the definition in one call.
"""

SURFACE_NOTE = (
    "`ictus/__init__.py` is the only surface a consumer imports from; "
    "`ictus.graph` re-exports nothing on purpose, so a name has one home "
    "rather than two that drift.\n"
)


def _summary(tree: ast.Module) -> str:
    """The module docstring's opening paragraph, collapsed to one line."""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    return " ".join(doc.strip().split("\n\n", 1)[0].split())


def _methods(node: ast.ClassDef) -> list[str]:
    """A class's public methods and properties, in source order."""
    return [
        m.name
        for m in node.body
        if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef) and not m.name.startswith("_")
    ]


def _definitions(tree: ast.Module) -> dict[str, str]:
    """Every top-level definition, name -> how it renders in the table.

    Methods are the half that matters in the long modules. `pipeline.py` is
    1300 lines and defines one name worth grepping for at the top level, while
    the thing anyone actually looks for is `feed` or `branch_on_outcome`.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = f"`{node.name}`"
        elif isinstance(node, ast.ClassDef):
            api = _methods(node)
            out[node.name] = f"`{node.name}`" + (f" ({', '.join(api)})" if api else "")
    return out


def _exported(tree: ast.Module) -> list[str]:
    """The names in a module-level `__all__`, empty if it declares none."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "__all__" not in targets or not isinstance(node.value, ast.List):
            continue
        return [
            e.value
            for e in node.value.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


@dataclass(frozen=True)
class Module:
    """One module, as the map describes it. Built once per file, in `read`."""

    path: str
    summary: str
    exported: tuple[str, ...]
    cells: tuple[str, ...]

    @property
    def is_surface(self) -> bool:
        """A package `__init__.py` that declares what the package offers."""
        return self.path.endswith("__init__.py") and bool(self.exported)

    @property
    def dotted(self) -> str:
        """`src/ictus/stdlib/__init__.py` -> `ictus.stdlib`."""
        return ".".join(Path(self.path).parent.parts[1:])


def read(path: Path) -> Module:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = _definitions(tree)
    exported = _exported(tree)
    # `__all__` decides *which* names are public where a module declares one; it
    # does not decide how they render, so a class still arrives with its
    # methods. A name in `__all__` defined elsewhere is a re-export and has none.
    cells = (
        [definitions.get(n, f"`{n}`") for n in exported]
        if exported
        else [cell for name, cell in definitions.items() if not name.startswith("_")]
    )
    return Module(
        path=path.relative_to(REPO_ROOT).as_posix(),
        summary=_summary(tree),
        exported=tuple(exported),
        cells=tuple(cells),
    )


def modules() -> list[Module]:
    paths = sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)
    return [read(p) for p in paths]


def render(found: list[Module]) -> str:
    lines = [HEADER, "\n## Public surface\n", SURFACE_NOTE]
    for module in found:
        if not module.is_surface:
            continue
        names = ", ".join(f"`{n}`" for n in sorted(module.exported))
        lines.append(f"\n**`{module.dotted}`** — {len(module.exported)} names\n")
        lines.append(f"> {names}\n")

    lines.append("\n## Modules\n")
    lines.append("| Module | What it holds | Defines |")
    lines.append("| --- | --- | --- |")
    lines.extend(f"| `{m.path}` | {m.summary} | {', '.join(m.cells)} |" for m in found)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    # Not argparse: two modes, and a mistyped flag must not fall through to the
    # write branch. In CI that would regenerate the artifact and report success.
    check = argv == ["--check"]
    if argv and not check:
        print(f"usage: {Path(__file__).name} [--check]", file=sys.stderr)
        return 2

    fresh = render(modules())
    if not check:
        MAP.write_text(fresh, encoding="utf-8")
        print(f"wrote {MAP.relative_to(REPO_ROOT)}")
        return 0
    if not MAP.is_file():
        print(f"{MAP.name} is missing; run `make map`", file=sys.stderr)
        return 1
    if MAP.read_text(encoding="utf-8") != fresh:
        print(f"{MAP.name} is stale; run `make map`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
