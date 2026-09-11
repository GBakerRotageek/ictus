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
from typing import TYPE_CHECKING

import pytest
from conftest import REPO_ROOT, pipeline_roots

import ictus.stdlib as stdlib

if TYPE_CHECKING:
    from pathlib import Path

CATALOGUE = REPO_ROOT / "STDLIB.md"
DOC = CATALOGUE.read_text(encoding="utf-8")
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

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
    assert stdlib.UNCLEAR == "unclear"
    assert stdlib.DONE == "done"
    for constant in (
        "converged",
        "exhausted",
        "agreed",
        "unresolved",
        "halted",
        "ok",
        "failed",
        "unclear",
        "done",
    ):
        assert f"`{constant}`" in DOC, f"outcome {constant!r} is not named in STDLIB.md"


def test_the_readme_points_at_the_catalogue() -> None:
    assert "STDLIB.md" in README


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
    assert f"`{gone}`" not in README, f"README.md still lists {gone}"


AGENTS = REPO_ROOT / "AGENTS.md"

# Every instruction file the repo owns, root first. A subtree one is loaded only
# when something in that subtree is read, which is what keeps the always-on
# budget to the root file alone.
#
# Scoped to the directories we author rather than rglob from the root: `.venv`
# is under it, and a dependency shipping its own AGENTS.md would fail this on a
# file nobody here wrote.
OURS = ("src", "tests", "tools")
INSTRUCTIONS = [
    AGENTS,
    *sorted(p for d in OURS for p in (REPO_ROOT / d).rglob("AGENTS.md")),
]


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

    @pytest.mark.parametrize(
        "agents", INSTRUCTIONS, ids=lambda p: "root" if p.parent == REPO_ROOT else p.parent.name
    )
    def test_every_instruction_file_is_paired_with_a_claude_pointer(self, agents: Path) -> None:
        """Claude Code discovers `CLAUDE.md` and does *not* discover `AGENTS.md`.

        Measured rather than assumed: the same canary text placed in a scratch
        repo was recovered from `CLAUDE.md` and not from `AGENTS.md` (Claude
        Code 2.1.268). So the file every *other* tool reads is invisible to the
        one most often pointed at this repo unless a pointer sits beside it —
        and a subtree file nobody loads is worse than none, because it reads as
        covered.

        A one-line `@AGENTS.md` import rather than a copy: two files saying the
        same thing is the drift this module exists to prevent.
        """
        pointer = agents.parent / "CLAUDE.md"
        assert pointer.is_file(), f"{agents} has no CLAUDE.md beside it, so Claude Code ignores it"
        body = pointer.read_text(encoding="utf-8")
        assert "@AGENTS.md" in body, f"{pointer} must import AGENTS.md, not restate it"
        assert len(body.split()) < 20, f"{pointer} is a pointer; the account lives beside it"

    def test_it_says_how_to_reach_the_engine_source(self) -> None:
        """Every "the engine cannot do X" claim is settled by this lookup."""
        text = AGENTS.read_text(encoding="utf-8")
        assert "readlink -f" in text, "the console-script resolution must be spelled out"
        assert "config/schema.py" in text
        assert "not importable" in text or "not* importable" in text

    def test_no_pipeline_context_restates_the_project(self) -> None:
        """Duplicated, the two drift and the run reads whichever is stale.

        Written for one council's `context.md` and widened to all of them: the
        rule is a property of every pipeline in the repo, and pinning it to a
        single path meant it stopped being checked the moment that pipeline
        stopped being committed.

        What is forbidden is restating the *lookup* — the resolution recipe and
        the machine-specific path it prints. Naming `conductor-cli` is not:
        several unrelated products are called Conductor, and a pipeline that
        tells its voices which one it means is doing its own job. Widening this
        check is what showed that the original spelling would have failed that
        file for a legitimate reason.
        """
        # The lookup, not the subject: `readlink -f` is the recipe in AGENTS.md
        # and `share/uv/tools` is the path it resolves to on this machine.
        restated = ("readlink -f", "share/uv/tools")
        contexts = [c for root in pipeline_roots() for c in sorted(root.glob("*/context.md"))]
        for path in contexts:
            context = path.read_text(encoding="utf-8")
            for marker in restated:
                assert marker not in context, (
                    f"{path} restates {marker!r}; the engine lookup belongs in AGENTS.md, "
                    "which the run discovers"
                )
