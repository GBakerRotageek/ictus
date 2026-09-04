.RECIPEPREFIX = >
.PHONY: soundcheck emit validate run clean

soundcheck:
> uv run ruff check .
> uv run ruff format --check .
> uv run mypy src pipelines
> uv run pytest -q
> $(MAKE) emit

emit:
> uv run ictus ./pipelines --out ./build/pipelines

validate:
> @for f in build/pipelines/*.yaml; do echo "-- $$f"; conductor validate "$$f" || exit 1; done

run:
> conductor run build/pipelines/$(WF).yaml --web

clean:
> rm -rf build/pipelines/* output/* logs/*
