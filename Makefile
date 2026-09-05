.RECIPEPREFIX = >
.PHONY: soundcheck emit lint validate run clean

# `validate` is the last step and it is not optional: ictus exists to produce
# Conductor workflows, so a green build that never asked Conductor whether the
# output loads has checked nothing. It was removed once; that is how five
# commits of unloadable YAML shipped green.
soundcheck:
> uv run ruff check .
> uv run ruff format --check .
> uv run mypy src demo_work tests
> uv run pytest -q
> $(MAKE) emit
> $(MAKE) validate

emit:
> uv run ictus emit ./demo_work/pipelines --out ./demo_work/build

lint:
> uv run ictus lint ./demo_work/pipelines

validate:
> uv run ictus validate ./demo_work/build

# WF is the pipeline_id, e.g. `make run WF=smoke-test`.
run:
> uv run ictus run $(WF) --out ./demo_work/build

clean:
> rm -rf demo_work/build/*.yaml
