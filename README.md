# ictus

Typed composition, validation and linting for multi-stage agent pipelines.
Pipelines are authored as Python, checked by mypy and by composition lints,
emitted as Conductor YAML. Conductor executes.

    src/ictus/core/   node and stage builders
    src/ictus/lint/   composition rules (the soundcheck)
    src/ictus/emit/   YAML emitter
    pipelines/        authored pipelines, one module per ticket type
    build/pipelines/  emitted YAML, committed so diffs show what runs
    scripts/          shell steps invoked by script nodes

Node references are objects, never strings. `to(plan_gate)` is checked;
`to("plan_gaet")` is not expressible.

## Usage

    make soundcheck
    make run WF=two-repo-ticket
