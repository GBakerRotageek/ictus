# Working in this repository

ictus is a typed Python composition layer that compiles to Conductor workflow
YAML. Pipelines are authored as Python, checked by mypy and by composition
lints, emitted as YAML, and executed by Conductor.

The point is that mistakes are caught when a pipeline is written rather than
when it runs. A live run costs money and takes minutes; a composition error
costs nothing. **An abstraction here earns itself only by moving a failure
earlier** — one that merely moves a failure somewhere else is worse than none.

## Where the ground truth is

Conductor is a **uv tool install**. It lives in its own isolated virtualenv and
is *not* importable from this project's `.venv` or from system python. Resolve
it through the console script:

```sh
"$(dirname "$(readlink -f "$(command -v conductor)")")/python" \
  -c "import conductor, pathlib; print(pathlib.Path(conductor.__file__).parent)"
```

On this machine that is
`~/.local/share/uv/tools/conductor-cli/lib/python3.12/site-packages/conductor`.

| File | Settles |
| --- | --- |
| `config/schema.py` | every field a workflow may contain |
| `engine/workflow.py` | what actually executes |
| `providers/` | how each provider is driven |
| `executor/` | what a step does at run time, as against what it accepts |
| `gates/`, `interrupt/` | human gates, and the pause / skip / stop / guidance API |

Any claim about what can or cannot be expressed, or about how the engine
behaves, is settled there — not by this project's documentation, which describes
what ictus chose to surface rather than what the engine offers.

**`import conductor` failing is a fact about your shell, not about Conductor.**
`ModuleNotFoundError` means you asked the wrong interpreter. It is not evidence
that a feature is absent. A council once reported that retry, per-agent
timeouts, reasoning effort and skills all needed engine changes; all four were
fields already in `config/schema.py`, and every voice had given up after one
failed import.

**Check the case you are actually claiming.** Two mistakes have now been made
twice each here:

- *Schema vs executor.* `AgentDef.working_dir` is one field serving two step
  kinds, and it behaves differently in each — script steps get it as the
  subprocess cwd (`executor/script.py`, `cwd=`), agents get it resolved against
  the workflow file. A docstring covering one case is not evidence about the
  other.
- *ictus vs Conductor.* "The engine supports it" and "ictus exposes it" are
  different questions. A field present in `config/schema.py` and absent from
  `src/ictus` is not a closed finding — it is the cheapest kind of open one.
- *Schema vs provider.* `config/schema.py` says what a workflow may **say**;
  `providers/<name>.py` says what the provider you chose actually **reads**.
  `retry` and `reasoning` are on every `AgentDef` and `claude-agent-sdk` reads
  neither; `context_tier` is documented as Copilot-only and `aca` forwards it
  too. Check the provider before calling a field a wiring gap. Two authoritative
  sources: each provider's `CAPABILITIES` block (`providers/capabilities.py`
  names the flags) and, for fields with no flag, the code that consumes the
  value. `interfaces/conductor/lints.py` keeps the answers in three sets, split
  by *who notices*: `HONOURED_BY` where nothing upstream checks and the ictus
  lint is the only guard, `VALIDATED_UPSTREAM` where `conductor validate`
  refuses it already and a lint here would duplicate it, `HONOURED_EVERYWHERE`
  where the field holds on every provider. Before adding an entry, emit a
  workflow that sets the field on a provider that ignores it and run
  `conductor validate` on it — that one command decides which set it belongs in.

## Layers, and what each may know

- `graph/` models pipelines and knows **no engine**: nodes, typed ports, typed
  references, scopes, map groups.
- `interfaces/` is the boundary — `Backend`, `Capabilities`, engine-neutral
  environment checks.
- `interfaces/conductor/` is the **only** package permitted to know Conductor's
  spelling: field names, template dialect, iteration accounting, CLI.
- `stdlib/` holds ready-made nodes and stages built on the graph layer.
- `tests/fixtures/pipelines/` holds the folder-shaped pipelines the gate reads,
  one folder each. Nothing in `src/` imports from them.
- `demo_work/pipelines/` is local, gitignored work in the same shape. It is
  checked too when it is present, and a clone will not have it.

A Conductor field name appearing above the `interfaces/conductor/` line is a
defect with a name, not a style preference.

## Three tiers

A **Node** is one entry in the flat `agents:` list. A **Stage** is its own YAML
file plus a `type: workflow` agent in the parent. A **Scope** is a stage whose
every exit is an outcome the caller routes on rather than an exception that
kills it.

Two scopes put several agents on one question and they are not interchangeable.
`council` **polls**: its voices run at once, so none has heard the others when
it speaks, and a synthesis step writes each round up for the next — breadth, and
a round of lag nothing can remove from a parallel group. `roundtable` **talks**:
everyone reads alone first, then speakers take turns, so the second has heard
the first *this* round and the last has heard everyone. Order is part of its
design, and the cost of arguing is wall-clock.

## The running contract

A pipeline is a folder: `pipeline.py` (the graph), `config.yaml` (policy —
provider, budget, gates), `input.md` (this run's values, as YAML frontmatter
over a prose body), and `build/` (emitted YAML, **committed** so a diff shows
what runs).

## How to work here

```sh
make soundcheck          # ruff, ruff format, mypy strict, pytest, emit, validate
make emit                # after ANY change that reaches YAML — every root, one pass
make lint
make validate            # Conductor's own loader

# Or a root at a time. The first is committed and is what a clone has:
uv run ictus lint      tests/fixtures/pipelines
uv run ictus emit      tests/fixtures/pipelines
uv run ictus validate  tests/fixtures/pipelines
uv run ictus preflight demo_work/pipelines   # can this machine run it?
uv run ictus trace     <pipeline>            # what each step actually did
```

`make` iterates both roots, so prefer it over naming a path: `demo_work/` is
gitignored and a command that hard-codes it fails on a clone with
`is not a directory`.

`build/` is committed, so a change to a prompt, a baseline or a constructor
leaves the tree stale until you re-emit — `test_committed_yaml_matches_a_fresh_emit`
is what catches it. Run `lint` **and** `validate`; neither is sufficient alone.

Conventions that are enforced rather than suggested:

- **Tests assert behaviour at the public boundary.** A test that would still
  pass with the implementation deleted is not a test. Reproduce a bug with a
  failing test before fixing it.
- **Make invalid states unrepresentable** before adding a runtime check. Prefer
  a `CompositionError` where it is written over a lint over a run-time failure.
- **Errors carry context** about what was being attempted. Never swallow one to
  simplify a signature.
- **Comment the non-obvious decision**, never restate the code. Most comments
  here record why an alternative was rejected, usually because it failed on a
  live run.
- **Pin versions.** Prefer the standard library; justify each dependency.
- Do not commit, push or rewrite history unless asked.

## Reference

- `MAP.md` — every module and the names it defines, generated by `make map`.
  Read it before grepping: a name from it plus `rg -n 'def <name>' <module>`
  lands on a definition in one call.
- `README.md` — the architecture, the engine boundary, what the lints add.
- `STDLIB.md` — every constructor, the wiring table, and the gotchas that have
  each cost a real run.
- `.claude/skills/ictus-pipeline/SKILL.md` — authoring a new pipeline, and the
  environment limits that decide whether a node can reach anything.

## Licence

GPL-3.0-or-later, in `LICENSE`. Contributions are under the same terms; a change
that adds a dependency needs one whose licence is compatible with it.

## What this is for

Not to replace an interactive coding agent. Those are better at open-ended work
done once, because they accumulate context, iterate against reality, and take
correction mid-flight. This is for work you do repeatedly with a shape you have
already learned: the same review every change, the same provisioning every
deploy, gates in known places, a cost ceiling, and a run a second person can
read afterwards.
