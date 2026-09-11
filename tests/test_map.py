"""`MAP.md` has to describe the package that exists.

The same bargain as each pipeline's `build/`: a generated artifact is committed
so it can be read without running anything, which only works if something fails
when it goes stale. `tools/build_map.py --check` is that something.

What the map is for: agents locating code find the right *file* and then miss
the right *span*, so the map lists names — with one, `rg -n 'def feed'` lands on
the definition in a single call. A name added to a public class and not to the
map is the drift this catches.
"""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT

MAP = REPO_ROOT / "MAP.md"
GENERATOR = REPO_ROOT / "tools" / "build_map.py"


def test_the_map_exists_at_the_project_root() -> None:
    assert MAP.is_file(), "MAP.md is committed so it can be read without running anything"


def test_the_committed_map_matches_a_fresh_generation() -> None:
    """Added a public name and not regenerated is the failure this catches."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_it_names_the_command_that_regenerates_it() -> None:
    """A reader who finds it stale needs to be told what to run, in the file itself."""
    assert "make map" in MAP.read_text(encoding="utf-8")
