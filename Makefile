.RECIPEPREFIX = >
.PHONY: soundcheck emit lint validate map run clean

# Two roots hold folder-shaped pipelines. `tests/fixtures/pipelines/` is
# committed and is the gate; `demo_work/pipelines/` is gitignored local work,
# checked as well when it is there. The gate lives in the first because it has
# to survive a clone: while it lived only in the second, `make soundcheck` on a
# fresh checkout ran 36 fewer tests than the machine it was written on, failed
# three, and every command in AGENTS.md errored on a missing directory.
PIPELINES := tests/fixtures/pipelines $(wildcard demo_work/pipelines)

# One `ictus` subcommand over every root, stopping at the first failure. The
# CLI takes one path, so the loop lives here — defined once so the three
# targets below cannot drift apart.
over_roots = for d in $(PIPELINES); do uv run ictus $(1) "./$$d" || exit 1; done

# `validate` is the last step and it is not optional: ictus exists to produce
# Conductor workflows, so a green build that never asked Conductor whether the
# output loads has checked nothing. It was removed once; that is how five
# commits of unloadable YAML shipped green.
soundcheck:
> uv run ruff check .
> uv run ruff format --check .
> uv run mypy src tests tools
# Each pipeline folder holds a file called pipeline.py, so mypy sees several
# modules with one name. Checking them a file at a time keeps the folder names
# readable (a hyphen is not a valid module component, so package-based
# disambiguation is not available) without giving up type checking on them.
> for d in $(PIPELINES); do for f in "$$d"/*/pipeline.py; do uv run mypy "$$f" || exit 1; done; done
> uv run pytest -q
> $(MAKE) emit
> $(MAKE) validate

emit:
> $(call over_roots,emit)

lint:
> $(call over_roots,lint)

# Every folder's build/, in one pass.
validate:
> $(call over_roots,validate)

# MAP.md is committed so it can be read without running anything;
# `test_the_committed_map_matches_a_fresh_generation` is what catches it going
# stale, the same bargain as each pipeline's build/.
map:
> uv run python tools/build_map.py

# WF is the folder name, e.g. `make run WF=smoke-test`. The run works in the
# directory you invoke it from unless the folder's input.md pins a `repo:`.
# WHERE picks the root; it defaults to local work because that is what you run.
WHERE ?= demo_work/pipelines
run:
> uv run ictus run ./$(WHERE)/$(WF)

clean:
> for d in $(PIPELINES); do rm -rf "$$d"/*/build; done
