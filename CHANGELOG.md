# Changelog

What changed, for whoever has a pipeline written against the last version.

The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are [semantic](https://semver.org/), with the pre-1.0 caveat that
means: **the stdlib's constructors are the API and they still move.** Until
1.0 a minor bump may rename or remove one. Every such change is in the
**Changed** and **Removed** sections below, with the line to write instead.

`ictus.__version__` says which version is installed.

## [0.1.0] — 2026-10-08

The first version with a number. Everything before this was `0.0.0`, so this
entry is the backlog: what the package looked like when it stopped being
unversioned, and what moved to get there.

The compiler itself — `graph/`, the lints, the emitted YAML — is unchanged.
Every workflow and manifest this release emits is byte-identical to the one
before it. What moved is where things live and what they are called.

### Added

- **`ictus stdlib`** — lists every ready-made node, stage and scope, grouped,
  with a one-line summary and the import line for each. `ictus stdlib <term>`
  searches by name *or* by what a thing does, so `ictus stdlib approve` finds
  `approval_gate`. Read off `__all__` and the docstrings, so it cannot drift
  from the code. `STDLIB.md` remains the fuller catalogue.
- **`ictus adapters`** — lists the services this installation can report to and
  read from, including ones from other packages. `--check` imports each and
  says which will not work.
- **Entry-point groups `ictus.notify` and `ictus.sources`.** A third party can
  now ship an adapter without a pull request: declare the group in your
  `pyproject.toml` and `ictus adapters` finds it. ictus's own adapters register
  the same way. `ictus.plugins` is the API.
- **`ictus.stdlib.catalogue`** — the stdlib described from the stdlib, for
  anything that wants the list programmatically.
- **`ictus.runs.answer.Press`** — an engine- and service-neutral record of
  somebody choosing one of a gate's options. `resolve` and `submit` take one.
- **`ictus.stdlib.scopes.outcomes`** — every scope outcome name, defined once.
- **`ictus-bridge`** — a second console script, for the chat daemon.
- **`tests/test_boundaries.py`** — the layering rules are now asserted over
  every module rather than described in `AGENTS.md`: no service named outside an
  adapter, nothing in ictus importing the bridge, no adapter imported by name.

### Changed

- **Prompt text moved out of Python, and a module with prompts became a
  folder** — `stdlib/llm/voice/__init__.py` plus `stdlib/llm/voice/stance.md`,
  read through `ictus.prompting.prompt`. Import paths are unchanged. Fifteen files, about 9,000 characters that used to
  be backslash-continued string literals. No emitted workflow changed. The
  layout is enforced by `tests/test_prompt_layout.py` rather than described
  anywhere: inline prose over 60 characters in prompt position fails, as does a
  prompt file nothing reads, a request with no file, and a file missing from
  the built wheel.
- **`ictus.stdlib.prompts` is now `ictus.stdlib.baseline`.** Prompt text sits in
  the folder of the module that reads it, so there is no `prompts/` directory
  anywhere for the old name to describe.

Breaking, with what to write instead:

| Was | Now |
| --- | --- |
| `ictus listen` | `ictus-bridge listen` |
| `from ictus.stdlib import briefing, remediate, validate_mcp, voice` | `from ictus.stdlib.llm import ...` |
| `from ictus.sources import readonly_sqlite` | `from ictus.sources.sqlite import readonly_sqlite` (and so on per engine) |
| `ictus.config` | `ictus.runspec.config` |
| `ictus.runspec` (module) | `ictus.runspec.inputs` |
| `ictus.baseline` | `ictus.stdlib.baseline` (the prompt) and `ictus.runspec.config` (`NO_BASELINE`) |
| `ictus.scaffold` | `ictus.runspec.scaffold` |
| `ictus.gate` | `ictus.assemble.start_gate` |
| `ictus.integrate` | `ictus.assemble.announcements` |
| `ictus.answer` | `ictus.runs.answer` |
| `ictus.websocket` | `ictus.net.websocket` |
| `ictus.notify.slack.listen` | `ictus.bridge.slack.listen` |
| `ictus.notify.slack.trigger` | `ictus.runs.triggers` and `ictus.runs.launch`; the Slack half is in `ictus.bridge.slack.listen` |
| `ictus.interfaces.conductor.runs` | `ictus.interfaces.conductor.control.live` |
| `ictus.interfaces.conductor.agents` | `ictus.interfaces.conductor.emit.agents` |
| `ictus.interfaces.conductor.events` | `ictus.interfaces.conductor.control.events` |
| `ictus.interfaces.conductor.mcp` | `ictus.interfaces.conductor.preflight` for `preflight_issues`, `…emit.mcp` for `mcp_servers_block` |
| `Pipeline.add_subworkflow` / `widen_subworkflow` | `Pipeline.add_subgraph` / `widen_subgraph` |

The rest of `interfaces/conductor/` moved the same way: `workflow`, `templates`,
`serialize`, `mapping`, `parallel` and `manifest` into `emit/`; `live`,
`respond`, `trace` and `signals` into `control/`.

| Was | Now |
| --- | --- |
| `ictus.cli` (module) | `ictus.cli` (package: `app`, `building`, `running`, `watching`, `catalogue`) |

- **`resolve` and `submit` take a `Press`**, not a Slack `Click`/`Note`.
  `submit` takes the text as a second argument rather than inside the record.
- **The stdlib's folders are named for what things are**, in `graph.NodeKind`'s
  vocabulary rather than Conductor's step kinds: `agents/` → `llm/`,
  `terminals/` → `exits/`, and the six scopes moved out of `stages/` into
  `scopes/`. The flat `ictus.stdlib` namespace is unaffected except as noted
  above.
- **`ictus.notify` and `ictus.sources` re-export no service.** Both are boundary
  packages now held to the same rule, and a flat re-export puts every service's
  name in the one file whose job is not to have it.
- **The Slack bridge is `ictus.bridge`**, its own package with its own console
  script. It may import ictus; nothing in ictus may import it.

### Removed

- **`ictus.stdlib.verdict`.** It was exported and documented and called by
  nothing — no stage, no pipeline, no test. For a yes/no a route can test,
  declare a `BOOLEAN` output on the node that decides and branch on it.

### Fixed

- **`render_output_schema` was lowering output ports to Conductor's `output:`
  block from inside `graph/node.py`** — its docstring said so, and its only
  caller was always the Conductor backend. Moved to
  `interfaces/conductor/emit/agents.py`. `graph/` now builds no YAML shape at
  all.
- **`Pipeline.add_subworkflow` and `widen_subworkflow` used the engine's noun**
  for a thing the graph itself calls a sub-graph (`NodeKind.SUB_GRAPH`,
  `SubGraphNode`). Renamed to `add_subgraph` and `widen_subgraph`.
- **The engine boundary was prose and is now a test.** `test_boundaries.py`
  refuses Conductor's spelling, and any function returning `YamlDict`, in the
  five packages that model a pipeline without knowing what runs it. Both of the
  defects above are what it was written against.
- **The boundary tokeniser read f-string prose as identifiers.** Since 3.12 an
  f-string's text is `FSTRING_MIDDLE`, not `STRING`, so sentences inside one
  were being scanned for vendor names.
- **`interfaces/conductor/` was emission and live-run control in one package.**
  The five control modules were never imported by the emit path, which is what
  made the seam obvious. Now `emit/` is a pure function of a pipeline — no
  environment read, no process started, so `ictus emit` still works on a laptop
  with no credentials and no engine — `control/` only ever acts on a run that
  exists, and `mcp.py` split along the same line into `emit/mcp.py` and
  `preflight.py`.
- **`__all__` is now on every library module, and states the real surface.**
  Six lacked one entirely — `errors` and five of `graph`, the core public API —
  and eleven more understated it, declaring two names while another module
  imported five. Both are now checked.
- **`UnsupportedFeatureError` removed.** Defined and exported from
  `ictus.interfaces`, raised nowhere and caught nowhere; the lint layer refuses
  an unsupported feature with a `CompositionError` long before a backend sees
  it.
- **`notify/__init__.py` held 176 lines of implementation** — subprocess calls
  and credential masking in the file whose job is to be a boundary, and the only
  package init in the tree carrying one. Moved to `notify/deliver.py`.

- **Scope outcome constants were defined twice.** `AGREED`, `UNRESOLVED` and
  `HALTED` existed in both `council` and `roundtable`, and `EXHAUSTED` in both
  `converge` and `investigate`. The flat re-export shipped whichever module
  imported first, so one of each pair was reachable only by its module path.
  The values matched, so nothing was wrong — one edit to either and the export
  would have been a lie that read as true.
- **Four of the five example pipelines were never linted.** `ruff` honours
  `.gitignore`, which keeps all but one demo folder out of the repository, so
  `make soundcheck` checked one of them. `soundcheck` now names them, as it
  already did for mypy. Two real errors were sitting in them, including an
  unused import in a file people copy.
- **The README opened with the internal source tree.** It now opens with a
  working pipeline and a table of what is already built; the tree moved to the
  end, where it is contributor material.
