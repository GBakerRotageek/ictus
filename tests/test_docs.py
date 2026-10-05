"""The stdlib catalogue has to describe the stdlib that exists.

A catalogue nobody checks rots quietly, and a wrong one is worse than none: the
README spent a session listing two constructors that had been deleted and a node
class that never existed under that name. These tests are cheap and they catch
exactly that — a thing added and not written down, a thing removed and left in,
an option renamed underneath its description.

They deliberately check *names and signatures*, not prose. Whether the one-line
description is any good is a judgement no test can make; whether it describes
something that is still there is not.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import ictus.stdlib as stdlib

CATALOGUE = Path(__file__).resolve().parent.parent / "STDLIB.md"
DOC = CATALOGUE.read_text(encoding="utf-8")

# Constructors live above the "## Running" section; below it are tables about
# settings and commands, whose first column looks the same and is not a
# constructor. Scoping the scan is what keeps `provider:` from reading as one.
CATALOGUE_TEXT = DOC.split("\n## Running", 1)[0]

# A table row naming a constructor: `| `name` | use | options | ... |`
ROWS = {
    m.group(1): m.group(2)
    for m in re.finditer(r"^\| `([a-z_]+)` \| [^|]+ \| ([^|]*) \|", CATALOGUE_TEXT, re.M)
}

CODE = "\n".join(re.findall(r"```(?:python|sh)\n(.*?)```", DOC, re.S))

CONSTRUCTORS = sorted(n for n in stdlib.__all__ if callable(getattr(stdlib, n)) and n[0].islower())
SPECS = sorted(n for n in stdlib.__all__ if inspect.isclass(getattr(stdlib, n)))


def test_the_catalogue_exists_at_the_project_root() -> None:
    assert CATALOGUE.is_file()


@pytest.mark.parametrize("name", CONSTRUCTORS)
def test_every_stdlib_constructor_is_catalogued(name: str) -> None:
    """Added and not written down is the failure this catches."""
    assert name in ROWS, f"{name} is exported from ictus.stdlib but has no row in STDLIB.md"


@pytest.mark.parametrize("name", SPECS)
def test_every_spec_type_is_mentioned(name: str) -> None:
    """`Voice`, `Attempt`, `ScriptStep`, `ReviewOption` — you cannot use the stage without them."""
    assert f"`{name}" in DOC, f"{name} is exported but never mentioned in STDLIB.md"


@pytest.mark.parametrize("name", sorted(ROWS))
def test_nothing_catalogued_has_been_removed(name: str) -> None:
    """Removed and left in is the other half, and the one that misleads."""
    assert hasattr(stdlib, name), f"STDLIB.md documents {name!r}, which ictus.stdlib no longer has"


@pytest.mark.parametrize("name", sorted(ROWS))
def test_every_documented_option_is_a_real_parameter(name: str) -> None:
    """An option renamed underneath its description reads as true and is not."""
    if not hasattr(stdlib, name):
        pytest.skip("covered by the removal test")
    params = set(inspect.signature(getattr(stdlib, name)).parameters)
    # A backtick token followed by ':' is a YAML key being named, not a parameter.
    claimed = set(re.findall(r"`([a-z_]+)`(?!:)", ROWS[name]))
    unknown = sorted(claimed - params)
    assert not unknown, (
        f"STDLIB.md lists {unknown} for {name}, whose parameters are {sorted(params)}"
    )


def test_the_outcome_vocabularies_are_stated_correctly() -> None:
    """A caller must route every outcome, so the catalogue naming them wrongly is a trap."""
    assert stdlib.CONVERGED == "converged"
    assert stdlib.EXHAUSTED == "exhausted"
    assert stdlib.AGREED == "agreed"
    assert stdlib.UNRESOLVED == "unresolved"
    assert stdlib.HALTED == "halted"
    assert stdlib.OK == "ok"
    assert stdlib.FAILED == "failed"
    for constant in ("converged", "exhausted", "agreed", "unresolved", "halted", "ok", "failed"):
        assert f"`{constant}`" in DOC, f"outcome {constant!r} is not named in STDLIB.md"


def test_the_readme_points_at_the_catalogue() -> None:
    readme = (CATALOGUE.parent / "README.md").read_text(encoding="utf-8")
    assert "STDLIB.md" in readme


@pytest.mark.parametrize("gone", ["revise_loop", "poll_until"])
def test_deleted_constructors_are_not_offered_as_usable(gone: str) -> None:
    """Both were replaced by `converge`; both survived in the README for a while.

    Naming them in prose is fine and useful — a reader who knew the old ones
    needs to be told where they went. Listing them in a table of what is
    available, or in code someone could copy, is the part that misleads.
    """
    assert not hasattr(stdlib, gone)
    assert gone not in ROWS, f"STDLIB.md still offers {gone} as a constructor"
    assert gone not in CODE, f"STDLIB.md has copyable code using {gone}"
    readme = (CATALOGUE.parent / "README.md").read_text(encoding="utf-8")
    assert f"`{gone}`" not in readme, f"README.md still lists {gone}"


AGENTS = CATALOGUE.parent / "AGENTS.md"


class TestRepositoryInstructions:
    """What a run picks up from the repository itself, rather than from a pipeline.

    `ictus run` passes `--workspace-instructions`, and Conductor discovers
    `AGENTS.md`, `.github/copilot-instructions.md`, `CLAUDE.md` and
    `.github/instructions/*.instructions.md`, walking up to the git root. That
    is the only route to a project's own account of itself: the provider pins
    `setting_sources=[]`, so nothing else reaches a step.

    The split under test is that the repo-wide account lives here, once, and a
    pipeline's own instructions carry only what is specific to that pipeline.
    Before it, the account lived in one council's `context.md` — so every other
    pipeline pointed at this repo, and every interactive session opened in it,
    got nothing.
    """

    def test_the_repo_carries_an_agents_file_where_the_engine_looks(self) -> None:
        assert AGENTS.is_file(), "AGENTS.md is what a run against this repo reads"
        assert AGENTS.stat().st_size > 0

    def test_it_says_how_to_reach_the_engine_source(self) -> None:
        """Every "the engine cannot do X" claim is settled by this lookup."""
        text = AGENTS.read_text(encoding="utf-8")
        assert "readlink -f" in text, "the console-script resolution must be spelled out"
        assert "config/schema.py" in text
        assert "not importable" in text or "not* importable" in text

    def test_no_pipeline_restates_the_project(self) -> None:
        """Duplicated, the two drift and the run reads whichever is stale.

        Written against one council's `context.md`, which has since left the
        repository. Naming a single file made the check disappear with it, so it
        now reads whatever prose a committed pipeline carries — which is also
        the shape the rule always had.
        """
        folders = sorted((CATALOGUE.parent / "demo_work" / "pipelines").glob("*/"))
        assert folders, "the gate needs at least one committed pipeline; see .gitignore"
        for prose in sorted((CATALOGUE.parent / "demo_work" / "pipelines").glob("*/*.md")):
            text = prose.read_text(encoding="utf-8")
            assert "readlink -f" not in text, (
                f"{prose}: the engine lookup belongs in AGENTS.md, which the run discovers"
            )
            assert "conductor-cli" not in text, prose
