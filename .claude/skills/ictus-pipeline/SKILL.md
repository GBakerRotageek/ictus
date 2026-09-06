---
name: ictus-pipeline
description: Author, lint and run an ictus pipeline — a typed Python graph that compiles to Conductor workflow YAML. Use when creating a new pipeline, adding a stage/council/gate to one, debugging an emit, lint, validate or preflight failure, or pointing a pipeline at a target project so its nodes can actually read that project. Encodes this machine's environment limits and the tools/turn-budget rules that decide whether a node can reach anything at all.
---

# Authoring an ictus pipeline

ictus compiles typed Python into Conductor workflow YAML. Conductor executes it.
The whole point is that mistakes surface while the pipeline is being written —
a live run costs money and minutes, a composition error costs nothing. An
abstraction earns its place here only by moving a failure *earlier*.

## 1. Read the environment before you assert anything about it

**Conductor is a uv tool install in its own isolated virtualenv. It is NOT
importable from this project's `.venv` or from system python.**

```sh
# WRONG — fails with ModuleNotFoundError from every interpreter you have
python -c "import conductor"

# RIGHT — resolve through the console script
"$(dirname "$(readlink -f "$(command -v conductor)")")/python" \
  -c "import conductor, pathlib; print(pathlib.Path(conductor.__file__).parent)"
```

`ModuleNotFoundError` here is a fact about which interpreter you asked, **not a
fact about Conductor**. Four voices on a council once concluded that retry,
per-agent timeouts, reasoning effort and skills all required engine changes.
All four were fields already in `config/schema.py`; every voice had given up
after one failed import. Do not repeat it.

Ground truth, once you have the path:

| File | Settles |
| --- | --- |
| `config/schema.py` | every field a workflow may contain (`AgentDef`, `RetryPolicy`, `ReasoningConfig`, `limits`) |
| `engine/workflow.py` | what actually executes |
| `providers/` | how each provider is driven |
| `gates/`, `interrupt/` | human gates, and the pause/skip/stop/guidance API |

Before writing "ictus cannot express X", grep the schema. A gap that turns out
to be an unwired field is a different and much cheaper finding.

## 2. A pipeline is a folder

```
pipelines/<name>/
  pipeline.py     the graph — the only file that matters
  config.yaml     policy: provider, budget, gates
  input.md        this run's values (YAML frontmatter + prose body)
  build/          emitted YAML, committed so a diff shows what runs
```

Start with `ictus init pipelines/<name>`. It writes `CHANGE-ME` where a decision
goes; `ictus lint` refuses to let that reach a billable run.

`config.yaml` minimally:

```yaml
provider: claude-agent-sdk
budget_usd: 5.0          # optional; budget_mode: enforce to stop at it
start_gate: true         # default — nothing launches without a person
```

## 3. Giving a node full power to reach the target project

This is the part that decides whether your pipeline can do anything. Two
independent settings, and both must be right.

### 3a. Tools — three states, not shades of one thing

```python
AgentNode(node_id="survey", prompt=..., tools=None)  # full Claude Code session
AgentNode(node_id="judge", prompt=..., tools=())  # no tools at all
AgentNode(node_id="x", prompt=..., tools=["Read"])  # REFUSED at composition
```

- **`tools=None`** — the engine's default. On `claude-agent-sdk` that is a full
  Claude Code session: filesystem, bash, web, permissions bypassed. **This is
  what a node needs to read a real project.** Note `AgentNode.tools` already
  defaults to `None`, so a plain node has full power unless you take it away.
- **`tools=()`** — denied. Correct for a node whose entire input is in its
  prompt: a council of four judging a diff should not be four agents opening the
  same file. `voice()` defaults to this deliberately.
- **a list** — refused while you write it. Conductor's `tools:` are *workflow*
  tool names, not the CLI's, and its provider raises mid-run rather than
  granting the wrong ones. There is no per-tool allowlist for an agent step. (An
  MCP server has its own `tools:` list; that one does work.)

### 3b. Three ways to bound a step, and they are not interchangeable

| Setting | Bounds | Enforced by |
| --- | --- | --- |
| `max_turns` | tool-use rounds | the engine, fatally — see below |
| `timeout_seconds` | wall clock, whole call | the engine, cancelling from outside |
| `max_session_seconds` | wall clock, provider session | the provider itself |

Setting `max_session_seconds` at or above `timeout_seconds` is refused at
composition: the engine gets there first, so the number could never fire.

There is a fourth check that is about the *answer* rather than the clock:

```python
AgentNode(
    node_id="survey",
    prompt=...,
    tools=None,
    max_turns=200,
    timeout_seconds=900,
    validator=Validator(criteria="Every claim cites a file and line."),
)
```

`validator` runs a second model call that judges the output against the rubric
and re-runs the step once with the feedback. Budget two calls per step where you
set it, three where the revision fires; `revise=False` reports without acting.
It catches what the other checks cannot — `declared_outputs` fixes the shape and
`retry` covers a call that fell over, but neither notices a well-formed answer
that is wrong.

### 3c. Turn budget — the default is a kill, not a throttle

A node with tools will use them. The engine allows **50 tool-use rounds** and
then *raises* rather than returning what the step had. That error is not one a
scope can turn into an outcome, so it destroys the whole run — after every
earlier step has been paid for.

```python
AgentNode(node_id="survey", prompt=..., tools=None, max_turns=200)
Voice(node_id="capability", persona=..., focus=..., tools=None, max_turns=200)
```

`voice()` **refuses at composition** if you give it tools and no `max_turns`.
For raw `AgentNode`s nothing forces you; set it anyway on anything told to go
and look. 200 is a sane figure for a node reading a repository.

### 3d. What a step knows about the project

A step is **not** a Claude Code session opened in the repo, and the difference is
context, not capability. The provider pins `setting_sources=[]` — no `CLAUDE.md`,
no settings, no ambient skills, no hooks.

Two levers, both already on by default:

| Lever | Reaches | Set in |
| --- | --- | --- |
| `--workspace-instructions` | the **target project's** `AGENTS.md`, `.github/copilot-instructions.md`, `CLAUDE.md`, `.github/instructions/*.instructions.md`, walking up to its git root | on by default; `workspace_instructions: false` in `config.yaml` to disable |
| `instructions:` in `config.yaml` | literal text or paths **you** name, prepended to every prompt | `instructions: [./context.md]` |

Use `instructions:` for what the *pipeline* needs every step to know; leave
`--workspace-instructions` on so the step also reads what the *project* says
about itself.

What you cannot recover: **Claude Code's own system prompt.** Conductor types
`AgentDef.system_prompt` as `str | None`, and naming the SDK's `claude_code`
preset needs a mapping — so a plain string *replaces* the preset rather than
appending to it. `ictus.baseline.AGENT_BASELINE` is the stand-in. Put working
discipline there or in `instructions:`, not in the hope that the model brings it.

`AgentNode` exposes `working_dir`, `skills` and `plugins` — the three fields
`claude-agent-sdk` actually honours. `skills` and `plugins` are tri-state like
`tools`: unset takes the workflow default, `()` denies every one, a non-empty
tuple names exactly what to load.

```python
AgentNode(
    node_id="review",
    prompt=...,
    tools=None,
    max_turns=200,
    working_dir="/srv/target",
    plugins=("prs",),
)
```

Two traps the lint catches for you:

- **A relative path on an agent resolves against `build/`**, the emitted output
  directory — not the project. Use an absolute path, `~/...`, or a template.
  (A `shell` step is the exception: its `working_dir` goes to the subprocess and
  resolves against the directory you launched from.)
- **A skill must come from a plugin on this provider.** `claude-agent-sdk`
  raises on a skill under no plugin root, so a bare `.claude/skills/<name>` fails
  at run time. Name the plugin instead.

`retry` and `reasoning` exist on `AgentNode`'s Conductor counterpart and this
provider reads neither — `retry` is wired and the lint refuses it here, so you
find out while writing rather than from a run that never retried.

### 3e. Pointing the run at a project

Three ways, in precedence order:

1. `ictus run <folder> --repo ~/work/the-project`
2. `repo: ../../the-project` in `input.md` frontmatter — resolved **relative to
   the input file**, and passed to the pipeline as an input named `repo` if one
   is declared.
3. `cd ~/work/the-project && ictus run ~/…/pipelines/<name>` — the default is
   the directory you launched from.

Agents then read and write inside that directory. Two traps:

- `save_text` writes relative to the **run's** working directory (the target
  project), not the pipeline folder.
- `working_dir` resolves differently per node kind: on a model call it resolves
  against the workflow file; on a `shell` step it goes straight to the
  subprocess and resolves against the cwd you ran from.

### 3f. Declare what the environment must supply

If a node checks its claims against a tool, declare it. A missing tool does not
crash the step — the step runs, the lookup fails, and the model reports the
thing as absent. That is a confident wrong answer at full price.

```python
from ictus import Executable

pipeline.require_executable(
    Executable(
        name="conductor",
        purpose="the schema every claim about the engine is checked against",
        probe=("--version",),
        setup_hint="uv tool install conductor-cli",
    )
)
```

`ictus preflight` then refuses the launch instead. Same for `EnvVar` and
`McpServer` (secrets are emitted as `${VAR}` references, never values).

## 4. The build loop

```sh
ictus lint      pipelines/<name>    # composition rules — free, run constantly
ictus emit      pipelines/<name>    # write build/*.yaml
ictus validate  pipelines/<name>    # Conductor's own loader
ictus preflight pipelines/<name>    # can THIS machine run it?
ictus run       pipelines/<name> --repo ~/work/target
ictus trace     <name>              # what each step actually did
```

Run `lint` and `validate` both — neither is sufficient. `lint` catches what
Conductor's loader cannot see (unreachable nodes, drifted stage contracts,
forward references into a loop); `validate` catches what ictus does not model.

`ictus trace` is the cheapest quality signal you have: it reports per step how
many tool calls were *not* just emitting an answer. A step that assessed
something it was only shown a summary of shows a `looked` count of zero. `ictus
run` now prints that automatically for a foreground run.

## 5. Building the graph

```python
from ictus import END, AgentNode, InputPort, OutputPort, Pipeline, PortType, tpl

STR = PortType.STRING

pipeline = Pipeline(pipeline_id="my-pipeline", description="What this does")
brief = pipeline.declare_input("brief", STR, prose=True, description="What to work from")

work = pipeline.add(
    AgentNode(
        node_id="work",
        inputs=(InputPort("brief", STR),),
        prompt=tpl("Do the thing described below.\n\n", brief.ref()),
        declared_outputs=(OutputPort("result", STR, "What it produced"),),
        tools=None,
        max_turns=200,
    )
)
pipeline.set_entry(work)
pipeline.connect_input(brief, work, "brief")
pipeline.route(work, END)
```

Wiring — pick by what has to travel:

| Call | Carries | Use when |
| --- | --- | --- |
| `connect(src, port, dst, port)` | control + data | target runs next *and* reads the value |
| `route(src, dst, when=)` | control only | target needs nothing from the source |
| `feed(src, port, dst, port)` | data only | the value crosses a gate or a branch |
| `connect_input(param, dst, port)` | a workflow input | binding the pipeline's parameters |

References are objects, never strings. `node.ref("port")` fails at composition
if that port is not declared. `ref_to("id", "port", TYPE)` names a node that does
not exist yet — a loop's back-edge — and is resolved by the lint against the
finished graph; wrap it in `optional(...)` so it renders to nothing on the first
pass, or the model invents what should have been there.

Three tiers: a **Node** is one `agents:` entry; a **Stage** is its own YAML file
plus a `type: workflow` agent in the parent; a **Scope** is a stage whose every
exit is an outcome the caller routes on rather than an exception that kills it.

Reach for `ictus.stdlib` before hand-rolling: `council`, `converge`,
`briefing_gate`, `resolve_unknowns`, `validate_mcps`, `script_sequence`,
`approval_gate`, `choice_gate`, `ask_human`, `shell`, `save_text`, `constant`,
`counter`, `wait`, `succeed`, `fail`.

## 6. Gotchas that have each cost a real run

- **Never `fail` inside a stage or scope.** A failed terminal raises past every
  route its caller declared. Use a scope outcome.
- **An unset `system_prompt` is an *empty* one**, not a default one. ictus
  supplies a baseline; set `none` only if you mean no discipline at all.
- **`${VAR}` in any string is expanded by Conductor at load.** Unset, the
  workflow refuses to load; set, the *value* is substituted into the prompt and
  sent to the provider. Tokens belong in an MCP header.
- **A `shell` step declaring `outputs` must print a JSON object** on stdout.
- **Set `constant`'s `output_type`** unless the value is free text — `"no"`
  comes back `False`, `"3"` an integer.
- **A council needs `rounds >= 2`** and a `verify=` if it is to measure anything
  beyond how much four models converged.
- **A loop needs `loop_passes`.** Each `wait` costs an iteration, so a poll loop
  of N checks needs roughly 2N.

## 7. When a voice or reviewer cannot check something

`voice()` declares an `unchecked` output port. A lookup that failed goes there,
with what was tried and what blocked it — **never into `concerns` as a
recommendation**. The report collects them into `unverified`, and the `verify`
step starts there, because that is where the unfounded claims are.

If you are writing a prompt for a node that assesses anything, say this in it
explicitly. Agreement between agents is not evidence: several agents blocked by
the same failed lookup reach the same wrong conclusion independently and it
looks exactly like consensus.
