# ictus stdlib

Ready-made pieces, all built from the public API. `from ictus.stdlib import ...`

Every constructor also takes `description`, and most take `inputs`. Options below
are the rest.

| Tier | Placed with | Costs the caller |
| --- | --- | --- |
| **Node** | `pipeline.add(...)` | 1 iteration |
| **Stage** | `stage.instantiate(parent)` | 1 iteration, whatever it contains |
| **Scope** | `scope.instantiate(parent)` + `parent.branch_on_outcome(...)` | 1 iteration |

A **Scope** is a stage whose every exit is an outcome you route on, instead of a
failure that kills the caller.

## Model calls

| Constructor | Use | Options | Produces |
| --- | --- | --- | --- |
| `briefing` | Summarise upstream output for a person to decide on | `subject`, `source`, `output_name` | `summary: string` |
| `verdict` | Answer a yes/no question as a boolean a route can test | `question`, `source`, `output_name` | `verdict: boolean`, `rationale: string` |
| `voice` | One standpoint's assessment — persona plus focus | `persona`, `focus`, `subject`, `intent`, `prior`, `direction`, `tools` | `satisfied: boolean`, `position: string`, `concerns: string` |
| `validate_mcp` | Prove an MCP server is reachable by calling a read-only tool | `server` | `available: boolean`, `detail: string` |
| `remediate` | Work through a blockage with the person at the terminal | `problem`, `subject` | `resolved: boolean`, `summary: string` |

## Human decisions

| Constructor | Use | Options | Produces |
| --- | --- | --- | --- |
| `approval_gate` | Approve or reject, free text on reject | `prompt`, `approve_label`, `reject_label`, `notes_field` | `selected: string`, `notes: string` |
| `choice_gate` | Arbitrary `(value, label)` options | `prompt`, `choices` | `selected: string` |
| `ask_human` | Ask questions known at composition time | `questions`, `allow_abort`, `allow_skip` | `answers: object`, `transcript`, `answered_count`, `outcome`, one port per question id |
| `ask_human_for` | Ask questions an earlier node produced | `source`, `allow_abort` | `answers: object`, `transcript`, `answered_count`, `outcome` |

Route with `pipeline.branch(gate, {...})`. `Question(text, id=, choices=, required=)`.

## Steps without a model

No provider call, still 1 iteration each.

| Constructor | Use | Options | Produces |
| --- | --- | --- | --- |
| `constant` | One computed value | `value`, `output_type` | `value`, typed as `output_type` |
| `bindings` | Several named values at once | `values`, `outputs` | one port per declared output |
| `counter` | Count passes through a point, from one | — | `value: number` |
| `save_text` | Write a value another step produced to a file | `text`, `to`, `append`, `working_dir` | `path: string` |
| `shell` | Run a command | `command`, `args`, `outputs`, `stdin`, `timeout`, `working_dir` | whatever `outputs` declares |
| `wait` | Pause | `seconds`, `reason` | — |

## Terminals

| Constructor | Use | Options |
| --- | --- | --- |
| `succeed` | End the run successfully | `reason`, `result` |
| `fail` | End as failed, non-zero exit | `reason`, `result` |

## Stages

| Constructor | Use | Options | Contract |
| --- | --- | --- | --- |
| `briefing_gate` | Turn data into a human decision and report which was taken | `subject`, `question`, `data_type`, `options` | in `data` → out `decision`, `summary`, `notes` |
| `resolve_unknowns` | Work out what is missing, ask only about that | `subject`, `needs` | in `brief` → out `known: object`, `answers: object` |
| `script_sequence` | Chain commands, threading each output into the next | `steps`, `parameter`, `working_dir` | in `<parameter>` → out `result` |
| `validate_mcps` | Prove every declared MCP server is reachable, with a fix-it loop | `servers`, `retries` | out `report` |

`ReviewOption(value, label, ask_for_notes=)`; default pair is `APPROVE_OR_REJECT`.
`ScriptStep(node_id, command, args, output, receives_previous=)`.

## Scopes

| Constructor | Use | Options | Outcomes | Carries |
| --- | --- | --- | --- | --- |
| `converge` | Bounded try/judge loop; running out is a value, not a crash | `attempt`, `judge`, `judge_prompt`, `verdict_port`, `passes`, `pause_between` | `converged`, `exhausted` | the attempt's outputs, `feedback`, `passes` |
| `council` | Several standpoints deliberating until they agree on a report | `voices`, `subject`, `rounds`, `interject`, `synthesis` | `agreed`, `unresolved`, `halted` (with `interject`) | `report`, `dissent`, `rounds` |

`Attempt(node_id, prompt, produces)` — a sequence becomes a chain, each step
reading the last. `Voice(node_id, persona, focus, tools=)`.

`judge=` picks who decides: `"model"` (an agent emits `approved` + `notes`),
`"human"` (an approval gate), `"self"` (the attempt declares the verdict itself —
the polling shape, usually with `pause_between`).

## Wiring

| Call | Carries | Use when |
| --- | --- | --- |
| `pipeline.connect(src, port, dst, port)` | control + data | the target runs next and reads the value |
| `pipeline.route(src, dst, when=)` | control only | the target needs nothing from the source |
| `pipeline.feed(src, port, dst, port)` | data only | the value crosses a gate or a branch |
| `pipeline.connect_input(param, dst, port)` | a workflow input | binding the pipeline's own parameters |

## Conditions

Built from references so they stay correct when what they were derived from changes.

| Helper | Renders |
| --- | --- |
| `equals(ref, value)` / `not_equals` | `{{ x == 'value' }}` |
| `every(*refs)` / `not_every` | `{{ a and b and c }}` |
| `at_least(ref, n)` | `{{ x \| int >= n }}` |
| `tpl(...)`, `optional(...)`, `ref_to(id, port, type)` | prompt text with typed references |

## Running

A pipeline is a folder. Three files, three questions.

```
pipelines/code-council/
  pipeline.py     what the graph is
  config.yaml     how it runs
  input.md        what to run it on
  build/          emitted YAML, committed
```

`config.yaml` is required; the minimal one is a line. `provider` has no default
because Conductor's is `copilot`, and inheriting that silently is a real bug this
project has already shipped once.

| Setting | Default | |
| --- | --- | --- |
| `provider` | none — required | who answers the model calls: `claude-agent-sdk`, `claude`, `copilot`, `openai`, `hermes`, `aca` |
| `default_model` | the provider's | the model every step uses unless it says otherwise |
| `start_gate` | `true` | hold at a confirmation gate before anything runs |
| `budget_usd` / `budget_mode` | none / `audit` | |
| `max_iterations` | derived from the graph | |
| `dashboard` | `true` | serve the web UI on `ictus run` |

Override the model for one step with `AgentNode(model=..., provider=...)` — a
cheap model for triage, an expensive one for the hard step.

`input.md` is YAML frontmatter over a body; the body feeds whichever input the
pipeline declares with `prose=True`.

```markdown
---
target: HEAD~1..HEAD
repo: ../../some-project     # optional; relative to this file
---
Ship it Friday behind a flag. Prefer reversible over ideal.
```

The run works in the directory you invoke it from unless `repo:` or `--repo`
says otherwise.

Every pipeline gets a **start gate** unless `config.yaml` turns it off: the run
loads, the dashboard comes up with the whole graph in it showing the actual input
values, and nothing happens until you choose. Conductor's dashboard has stop,
kill and resume but no start, so this is the only way to look before you leap.

```sh
ictus init pipelines/my-thing               # scaffold the three files

cd ~/work/my-service
ictus run ~/pipelines/code-council          # input.md supplies the rest
ictus run ~/pipelines/code-council -i target=HEAD~5..HEAD   # override one key
ictus run ~/pipelines/code-council -f other-input.md

ictus lint pipelines/          # composition problems
ictus emit pipelines/          # each folder's build/
ictus validate pipelines/      # Conductor's own validator
ictus preflight pipelines/     # MCP servers, env vars, tokens
```

Run both `lint` and `validate` — neither is sufficient alone.

## Gotchas

- **Never `fail` inside a stage or scope.** A failed terminal raises past every
  route its caller declared. Use a scope outcome.
- **`voice` has no tools by default.** Four voices with tools is four agents
  hunting the same file. Pass `tools=None` for the workflow default.
- **Set `constant`'s `output_type`** unless the value is free text — `"no"` comes
  back `False`, `"3"` an integer.
- **`bindings` types are ictus's, not the engine's.** Each binding's runtime type
  comes from a YAML load of its rendered text. Use `constant` where it must hold.
- **A `shell` step that declares `outputs` must print a JSON object.** Stdout is
  a contract, not a log; leave `outputs` empty for a command that prints prose.
- **`save_text` writes relative to the run's working directory** — the project
  you launched against, not the pipeline folder.
- **`working_dir` resolves differently for scripts and agents.** A relative one
  on a model call is resolved against the workflow file; on a `shell` step it
  goes straight to the subprocess, so it resolves against the cwd you ran from.
- **`council` needs `rounds >= 2`.** A voice is satisfied when the report states
  its position; the first round has no report to accept.
- **A gate's free-text field only exists on the branch that asked for one.** ictus
  guards it for you; do not hand-write the reference.
