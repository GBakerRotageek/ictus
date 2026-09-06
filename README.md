# ictus

Typed composition, validation and linting for multi-stage agent pipelines.
Pipelines are authored as Python, checked by mypy and by composition lints,
emitted as Conductor YAML. Conductor executes.

    src/ictus/               the library
      __init__.py            the one public surface
      errors.py              what ictus raises, and why
      cli.py                 emit / lint / validate / run
      graph/                 the composition model — knows no engine
        values.py            the value domain that crosses a node boundary
        ports.py             typed inputs and outputs
        ref.py               typed references and templates
        node.py              one class per kind of work
        pipeline.py          the graph: control edges, data edges, loop bounds
        stage.py             a reusable sub-graph
      interfaces/            the boundary to whatever executes a graph
        __init__.py          Backend, Capabilities, Document
        conductor/           every Conductor-shaped thing, and nothing else
          workflow.py        the workflow: block and the defaults worth stating
          agents.py          one node to one agents: entry
          templates.py       typed references into Conductor's Jinja
          serialize.py       YAML text, without altering any value
          lints.py           rules true because of how Conductor runs
      lint/                  composition rules that hold for any graph
        rules.py             the individual checks
      stdlib/                ready-made primitives, one per module
        gates/               human decision points
        agents/              model calls
        steps/               zero-model steps (set, wait, script)
        terminals/           explicit exits
        stages/              reusable sub-graphs

    demo_work/               what a *consumer* of ictus writes, not the library
      pipelines/
        <name>/              one folder per pipeline — the running contract
          pipeline.py        the composition
          input.md           what to run it on
          build/             emitted YAML, committed so diffs show what runs
      scripts/               shell steps invoked by script nodes

`ictus/__init__.py` is the only public surface — `ictus.graph` deliberately
re-exports nothing, so there is one place a name is exported from rather than
two that can drift.

Nothing in `src/` imports from `demo_work/`. The demo is there to be read and
run; deleting it would not touch the library.

## Three tiers

| Tier | You write | Conductor gets |
|---|---|---|
| **Node** | `AgentNode`, `GateNode`, `QuestionsNode`, `ScriptNode`, `ComputeNode`, `WaitNode`, `TerminateNode` | one entry in the flat `agents:` list |
| **Stage** | `Stage`, with its own graph and an input/output contract | its own YAML file plus a `type: workflow` agent in the parent |
| **Scope** | `Scope` — a stage whose every exit is an outcome the caller routes on | the same, plus a closed vocabulary the parent must route exhaustively |
| **Pipeline** | `Pipeline` | the `WorkflowConfig` envelope |

Conductor has no nested-step construct inside an agent. `type: workflow` is its
only nesting, which is what a stage compiles to — its own entry point, its own
loops and gates, and one iteration of the parent's budget.

## The engine boundary

`ictus.interfaces` is the only place allowed to know an engine's spelling — its
field names, template dialect, iteration accounting, CLI. `graph/` contains zero
Conductor strings, and that is checkable rather than aspirational: a grep for
Conductor's vocabulary above `interfaces/` should return nothing.

A `Backend` supplies four things: what it can express (`Capabilities`), how to
render a graph (`compile`), its own extra lint rules, and how to validate and
run what it produced. Rules like "a route list with no catch-all raises at run
time" belong to a backend, not to graphs in general, so `lint_pipeline(p)`
without a backend reports only what is true anywhere.

Not a plan to leave Conductor — a way to keep the coupling countable.

## Composition is checked where it is written

Node references are objects, never strings. A target that is not in the graph is
rejected by `connect`, so `to("plan_gaet")` is not expressible — and neither is a
node borrowed from a different pipeline.

```python
p.connect(plan, "plan", breakdown, "plan")  # port types must match
p.branch(gate, {"approved": execution, "rejected": breakdown})
```

References into prompts are typed too. `plan.ref("plan")` fails if that port is
not declared, and carries its type with it, so the reference is checked where it
is written rather than recovered from prompt text by a regular expression:

```python
prompt = tpl(
    "Break this plan into steps.\n\n",
    plan.ref("plan"),
    optional("Address these notes:\n", ref_to("review", "notes", STRING)),
)
```

`ref_to` names a node that does not exist yet — a loop's back-edge — and is
resolved against the finished graph by the lint. The guard such a reference
needs on the first pass is emitted by the compiler, which already knows the
reference is deferred. A live run once died on exactly that omission.

`connect` wires control **and** data. Conductor keeps those separate — `routes:`
decides what runs next, `input:` decides what a node may read — so when they
diverge, say them separately:

```python
p.route(a, b)  # control only
p.feed(breakdown, "steps", execution, "steps")  # data only, across a gate
```

Every rejection happens at the call that introduces it: a port type mismatch, a
foreign node, an unrouted gate choice, a second unconditional route from one
node (Conductor takes the first match, so the second would be silently dropped).

## What the lints add

`conductor validate` checks every reference it can see. These it cannot, and each
is silent until a run is already in flight:

- an agent unreachable from the entry point
- conditional routes with no catch-all — a **runtime** error that passes validation
- a reference to an output *field* that was never declared (only the agent
  segment is checked)
- a duplicate agent name
- a stage whose contract has drifted from the workflow it hosts
- a required input nothing is wired to

## The stdlib

One primitive per module, so the docstring beside a thing is about that thing.
**[STDLIB.md](STDLIB.md) is the catalogue** — every constructor, what it is for,
its options and what it produces.

| Group | Emits | What's there |
|---|---|---|
| `gates/` | `human_gate`, `questions` | `approval_gate`, `choice_gate`, `ask_human`, `ask_human_for` |
| `agents/` | `agent` | `briefing`, `verdict`, `voice`, `validate_mcp`, `remediate` |
| `steps/` | `set`, `wait`, `script` | `constant`, `bindings`, `counter`, `wait`, `shell` |
| `terminals/` | `terminate` | `succeed`, `fail` |
| `stages/` | `workflow` | `briefing_gate`, `resolve_unknowns`, `script_sequence`, `validate_mcps`, and the scopes `converge` and `council` |

Each stage is a whole sub-graph costing its caller one iteration. The last two are
**scopes** — a stage whose every exit is an outcome the caller routes on, rather
than a failure that raises past it. `converge` is a bounded try/judge loop;
`council` runs several `voice` nodes at once and loops them over a synthesised
report until they agree.

## Preflight

A pipeline declares what it needs from the environment, where it is written:

```python
pipeline.require_mcp(
    McpServer(
        name="github",
        purpose="Read the change set and comment on its PR",  # read by whoever configures it
        transport=McpTransport.STDIO,
        command="github-mcp-server",
        args=("stdio",),
        env=(EnvVar("GITHUB_TOKEN", "a token with repo:read and pull_request:write"),),
        setup_hint="Install github-mcp-server, then `export GITHUB_TOKEN=$(gh auth token)`",
    )
)
```

`ictus run` checks those before it launches anything, and refuses if they are
unmet — a database reset should not get halfway before discovering a token is
missing. `--probe` (on by default) additionally opens each connection, which
catches a rejected credential that a "is the variable set?" check cannot.
`--skip-preflight` overrides.

Preflight also exists *inside* a run. `validate_mcps` is a stage: one
`validate_mcp` agent per server, all in a parallel group, then a gate if any
failed that loops back so the person can connect the thing and retry without
losing the run.

The in-workflow check earns its model call by answering a question the CLI
cannot: the command can be installed, the token set and the endpoint answering
while the server never reaches the model. Only asking the model to *use* it
proves the connection end to end — and being a node, it is visible in the
dashboard while it happens.

An agent is also the only node type Conductor permits inside a parallel group;
scripts, waits, gates, sub-workflows and terminals are all rejected as members.

The gate offers three ways out, not two:

```
Help me fix it                → a helper that talks you through it
I have fixed it — check again → straight back to the checks
Abort the run                 → terminate, status: failed
```

"Help me fix it" routes to a `remediate` node carrying Conductor's `dialog`, so
it opens a multi-turn conversation — in the dashboard when one is served, in the
terminal otherwise. Whatever it does, the route returns to the checks: a claimed
fix is only believed once it passes. (`dialog` is forbidden on a gate, so the
helper must be a separate node — which the node types already enforce, since
only a model call carries the field.)

That helper draws one hard line, and it is a security boundary rather than a
preference: **it never handles credentials.** It diagnoses, and repairs what
needs no secret. The moment a fix needs a token or an access change it stops and
hands over the exact command to run in your own shell. Nothing asks you to paste
a secret into a conversation with a model, and no secret value is printed back.

Two commands, two questions, deliberately not merged:

| | asks | must pass on a machine with no credentials |
|---|---|---|
| `ictus validate` | is this workflow well-formed? | **yes** — else CI cannot check the committed artifact |
| `ictus preflight` | can *this* machine run it? | no — that is the whole point |

Secrets are never emitted. ictus writes the reference `${GITHUB_TOKEN:-}` and
checks separately that the variable is set. The empty default is load-bearing: a
bare `${VAR}` that cannot be expanded is a hard error in Conductor's loader, so
committing one would make the artifact unvalidatable anywhere the secret is
absent.

## Asking a person for something

A gate offers a decision among known options. `ask_human` collects *values* — a
path, an id, a name — and all of them cost one iteration together, not one each:

```python
ask_human(
    questions=(
        Question(id="api_path", text="Where is the API repo checked out?", required=True),
        Question(id="env", text="Which environment?", choices=("staging", "prod")),
    )
)
```

Each named question becomes a typed output port, so `ask.ref("api_path")` is
checked and renders `{{ ask.output.answers.api_path }}`.

Some questions cannot be written in advance — a ticket touching an unknown
number of repositories has an unknown number of questions. `ask_human_for`
takes them from an upstream node instead, and `resolve_unknowns` is the whole
pattern: work out what is missing, ask **only** about that, carry the answers
out.

Asking unconditionally is the easy version and the wrong one. A pipeline that
stops to ask about things it already knows gets skipped past, and then the one
time it mattered nobody read it either.

## Things Conductor defaults that will bite you

- `limits.max_iterations` defaults to **10 total step executions**. A loop needs
  more, so `Pipeline` requires `loop_passes` once the graph has a cycle and
  derives the bound from the graph.
- `context.mode` defaults to `accumulate`, under which `input:` is parsed and
  never consulted. ictus emits `explicit`, which is what makes the port graph
  mean something at run time.
- `runtime.provider` defaults to **copilot**. ictus always emits it, so the
  choice is visible in the diff rather than discovered on a failed run.
- Checkpointing is failure-only by default. Any graph with a gate gets
  `checkpoint.every_agent`, because the human may be hours away.

## The running contract

A pipeline is a folder, not a module — three files, three questions:

    pipelines/needs-council/
      pipeline.py            what the graph is       (composition)
      config.yaml            how it runs             (policy)
      input.md               what to run it on       (this run's values)
      build/                 emitted YAML, committed

`ictus init <folder>` writes all three. `config.yaml` is required and the minimal
one is a line:

    provider: claude-agent-sdk

`provider` has no default because Conductor's is `copilot`, and a pipeline that
silently inherited it is how four emitted workflows once ran somewhere nobody
chose. Policy lives here rather than in the composition so a pipeline moves
between providers without editing Python, and so a value set in both places and
set differently is refused instead of silently resolved.

`input.md` is YAML frontmatter over a Markdown body — the shape Conductor
already uses for `SKILL.md` and plugin agents, so it is one convention across
both tools. Frontmatter holds the short values; the body is the long one, and
which input it feeds is declared once with `declare_input(..., prose=True)`.

    ---
    scope: the stdlib and the CLI      # a declared input
    repo: ../../some-project           # optional; relative to this file
    ---
    The body feeds the input declared with prose=True.

A key matching no declared input is refused rather than ignored: `scpoe:` doing
nothing quietly is how a run does the default thing and nobody notices until the
output is wrong.

The work happens in the directory you invoke it from, so `cd` to a project and
go. `repo:` — or `--repo` — overrides that, which is how a run that touches a
particular checkout says so in something you can commit.

## Nothing starts without a person

Every pipeline gets a confirmation gate at its entry point, unless its
`config.yaml` says `start_gate: false`. The run loads, the dashboard comes up
with the whole graph in it and the actual input values rendered in the prompt,
and nothing happens until someone chooses.

This exists because Conductor's dashboard is a view onto a live engine, not a
launcher: the web API has `stop`, `kill` and `resume` but no start, and
`--dry-run` returns before the dashboard is built. A human gate costs no provider
call and no money, so it is the one mechanism that can hold a run open for
inspection. It is added at emit time, so what is committed in `build/` is what
runs — a confirmation step that only appeared at launch would make the artifact
a lie.

Declining is a *success*: nothing was attempted, so nothing failed, and a caller
should not have to treat "a person looked at it and said no" as an error.

## Usage

    make soundcheck            # lint, types, tests, emit, and conductor validate
    make run WF=smoke-test     # run that folder, dashboard on

    cd ~/work/my-service
    ictus run ~/pipelines/needs-council            # input.md supplies the inputs
    ictus run ~/pipelines/needs-council -i scope='the stdlib and the CLI'
    ictus run ~/pipelines/needs-council -f focused-review.md

    ictus emit pipelines/      # each folder's own build/
    ictus lint pipelines/      # composition rules only, writes nothing
    ictus validate pipelines/  # hands the emitted YAML to conductor

`soundcheck` ends with `conductor validate`, and that step is not optional: a
green build that never asked Conductor whether the output loads has checked
nothing.
