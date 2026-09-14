# Map

`src/ictus/`, module by module: what each holds and the names it defines.
Generated — run `make map` after adding or removing a public name.

Use it to skip the search, not to skip the file: a name here plus
`rg -n 'def <name>' <module>` lands on the definition in one call.


## Public surface

`ictus/__init__.py` is the only surface a consumer imports from; `ictus.graph` re-exports nothing on purpose, so a name has one home rather than two that drift.


**`ictus`** — 49 names

> `AgentNode`, `Backoff`, `CompositionError`, `ComputeNode`, `ContextTier`, `END`, `Edge`, `EmitError`, `EnvVar`, `Executable`, `GateChoice`, `GateNode`, `InputPort`, `LintError`, `McpServer`, `McpTransport`, `Node`, `OutputPort`, `Pipeline`, `PortConnection`, `PortType`, `PortTypeError`, `Question`, `QuestionsNode`, `ReasoningEffort`, `Ref`, `RetryOn`, `RetryPolicy`, `Scope`, `ScopeNode`, `ScriptNode`, `Stage`, `SubGraphNode`, `Template`, `TerminateNode`, `UnknownPortError`, `Validator`, `WaitNode`, `WorkflowInput`, `at_least`, `equals`, `every`, `not_equals`, `not_every`, `optional`, `outcome_scope`, `ref_to`, `slugify`, `tpl`


**`ictus.interfaces`** — 7 names

> `Backend`, `Capabilities`, `Document`, `PreflightIssue`, `ResumePlan`, `UnsupportedFeatureError`, `ValidationResult`


**`ictus.interfaces.conductor`** — 2 names

> `ConductorBackend`, `conductor`


**`ictus.lint`** — 2 names

> `check`, `lint_pipeline`


**`ictus.stdlib`** — 47 names

> `AGREED`, `APPROVE_OR_REJECT`, `Attempt`, `CONVERGED`, `Choice`, `DONE`, `EXHAUSTED`, `FAILED`, `HALTED`, `OK`, `READY`, `ReviewOption`, `ScriptStep`, `Speaker`, `Tier`, `UNCLEAR`, `UNRESOLVED`, `Voice`, `approval_gate`, `ask_human`, `ask_human_for`, `bindings`, `briefing`, `briefing_gate`, `choice_gate`, `classify`, `constant`, `converge`, `council`, `counter`, `fail`, `map_stage`, `poll_until`, `remediate`, `resolve_unknowns`, `roundtable`, `save_text`, `script_sequence`, `shell`, `succeed`, `tiered`, `try_shell`, `validate_mcp`, `validate_mcps`, `verdict`, `voice`, `wait`


**`ictus.stdlib.agents`** — 6 names

> `SATISFIED`, `briefing`, `remediate`, `validate_mcp`, `verdict`, `voice`


**`ictus.stdlib.gates`** — 4 names

> `approval_gate`, `ask_human`, `ask_human_for`, `choice_gate`


**`ictus.stdlib.stages`** — 30 names

> `AGREED`, `APPROVE_OR_REJECT`, `Attempt`, `CONVERGED`, `Choice`, `DONE`, `EXHAUSTED`, `FAILED`, `HALTED`, `OK`, `READY`, `ReviewOption`, `ScriptStep`, `Speaker`, `Tier`, `UNCLEAR`, `UNRESOLVED`, `Voice`, `briefing_gate`, `classify`, `converge`, `council`, `map_stage`, `poll_until`, `resolve_unknowns`, `roundtable`, `script_sequence`, `tiered`, `try_shell`, `validate_mcps`


**`ictus.stdlib.steps`** — 6 names

> `bindings`, `constant`, `counter`, `save_text`, `shell`, `wait`


**`ictus.stdlib.terminals`** — 2 names

> `fail`, `succeed`


## Modules

| Module | What it holds | Defines |
| --- | --- | --- |
| `src/ictus/__init__.py` | Typed composition for Conductor workflows. | `END`, `AgentNode`, `Backoff`, `CompositionError`, `ComputeNode`, `ContextTier`, `Edge`, `EmitError`, `EnvVar`, `Executable`, `GateChoice`, `GateNode`, `InputPort`, `LintError`, `McpServer`, `McpTransport`, `Node`, `OutputPort`, `Pipeline`, `PortConnection`, `PortType`, `PortTypeError`, `Question`, `QuestionsNode`, `ReasoningEffort`, `Ref`, `RetryOn`, `RetryPolicy`, `Scope`, `ScopeNode`, `ScriptNode`, `Stage`, `SubGraphNode`, `Template`, `TerminateNode`, `UnknownPortError`, `Validator`, `WaitNode`, `WorkflowInput`, `at_least`, `equals`, `every`, `not_equals`, `not_every`, `optional`, `outcome_scope`, `ref_to`, `slugify`, `tpl` |
| `src/ictus/baseline.py` | The system prompt every model call gets unless it says otherwise. | `AGENT_BASELINE`, `NO_BASELINE` |
| `src/ictus/cli.py` | The ``ictus`` command line. | `emit`, `lint`, `preflight`, `validate`, `run`, `resume`, `init`, `trace` |
| `src/ictus/config.py` | ``config.yaml`` — how a pipeline runs, as against what it is. | `CONFIG_FILE`, `MINIMAL`, `ConfigError`, `PipelineConfig` (apply), `read_config` |
| `src/ictus/errors.py` | Errors raised during pipeline composition and emission. | `IctusError`, `CompositionError`, `PortTypeError`, `UnknownPortError`, `EmitError`, `LintError` |
| `src/ictus/gate.py` | The start gate — a person confirms before a pipeline does anything. | `CANCELLED_ID`, `GATE_ID`, `add_start_gate` |
| `src/ictus/graph/__init__.py` | The composition model — what a pipeline is made of. |  |
| `src/ictus/graph/mapping.py` | Fan out over a list whose length is only known at run time. | `COUNT_PORT`, `ERRORS_PORT`, `OUTPUTS_PORT`, `Item` (ref, whole), `MapGroup` (node_id, outputs, get_output, output_ref, ref) |
| `src/ictus/graph/node.py` | Node kinds — one frozen class per Conductor agent type. | `NodeKind`, `Backoff`, `RetryOn`, `ReasoningEffort`, `ContextTier`, `RetryPolicy`, `slugify`, `check_route_name`, `Node` (kind, outputs, routes_via_options, accepts_routes, emits_output_schema, output_ref, guard_depth, template_strings, prompt_refs, settled_template_strings, settled_refs, path_refs, ref, get_input, get_output), `Validator`, `AgentNode` (kind, outputs, emits_output_schema, template_strings, prompt_refs), `GateChoice`, `GateNode` (kind, routes_via_options, outputs, output_ref, guard_depth, template_strings, prompt_refs), `ScriptNode` (kind, outputs, emits_output_schema, template_strings, prompt_refs), `ComputeNode` (kind, outputs, emits_output_schema, output_ref, template_strings), `WaitNode` (kind, template_strings), `TerminateNode` (kind, accepts_routes, template_strings, settled_template_strings, prompt_refs, settled_refs), `Question`, `QuestionsNode` (kind, outputs, path_refs, output_ref, template_strings, kind_flags), `SubGraphNode` (kind, outputs), `ScopeNode` (outcome), `render_output_schema` |
| `src/ictus/graph/pipeline.py` | The composition graph. | `FailureMode`, `TrimStrategy`, `ParallelGroup` (node_id), `WorkflowInput` (ref), `Edge` (condition_refs, is_end, target_node, describe_target), `ExposedOutput`, `DataDep`, `Pipeline` (add, add_subworkflow, children, parallel, map_over, wired_inputs, maps, map_of, map_named, groups, group_of, require_mcp, mcp_servers, require_executable, executables, all_executables, all_mcp_servers, declare_input, set_entry, connect, connect_input, feed, route, branch, branch_on_outcome, abort_route, expose_output, nodes, edges, workflow_inputs, input_bindings, exposed_outputs, exposed_output_ports, output_contract_names, declared_input_ports, data_deps, deps_into, outgoing, inbound, entry, reaches, reachable_from_entry, back_edges, may_be_unresolved, has_cycle, has_gate, require_loop_bound, total_cost, budget_cost, loop_cost, longest_cycle_length, step_cost) |
| `src/ictus/graph/ports.py` | Ports — the typed boundary of a node. | `PortType`, `OutputPort` (accepts), `InputPort`, `PortConnection` |
| `src/ictus/graph/ref.py` | Typed references to values produced elsewhere in the graph. | `Comparison` (refs), `OptionalBlock` (refs), `Origin`, `Ref` (from_input, or_else), `Template` (refs, literal_text), `TemplatePart`, `as_template`, `at_least`, `equals`, `every`, `not_equals`, `not_every`, `optional`, `ref_to`, `tpl` |
| `src/ictus/graph/requirements.py` | What a pipeline needs from its environment before it can run. | `EnvVar`, `Executable`, `McpServer` (required_env), `McpTransport` |
| `src/ictus/graph/scope.py` | Scopes — stages whose failures are values rather than exceptions. | `OUTCOME_PORT`, `Scope` (stage_id, output_ports, input_ports, exit, instantiate), `ScopeNode`, `outcome_scope` |
| `src/ictus/graph/stage.py` | Stages — reusable collections of nodes. | `Stage` (stage_id, description, input_ports, output_ports, instantiate) |
| `src/ictus/graph/values.py` | The value domain. |  |
| `src/ictus/interfaces/__init__.py` | The boundary between an ictus graph and whatever executes it. | `Backend` (capabilities, compile, lint, validate, preflight, run, can_resume, resume_plan, resume), `Capabilities`, `Document`, `PreflightIssue`, `ResumePlan`, `UnsupportedFeatureError`, `ValidationResult` |
| `src/ictus/interfaces/conductor/__init__.py` | The Conductor backend. | `ConductorBackend` (capabilities, document, compile, lint, preflight, validate, run, can_resume, resume_plan, resume, plan), `conductor` |
| `src/ictus/interfaces/conductor/agents.py` | Lowering one node to one entry in Conductor's ``agents:`` list. | `agent_entry` |
| `src/ictus/interfaces/conductor/lints.py` | Rules that are true because of how Conductor runs, not how graphs are shaped. | `conductor_problems` |
| `src/ictus/interfaces/conductor/mapping.py` | Lowering map groups to Conductor's top-level ``for_each:`` list. | `for_each_block`, `mapping_problems` |
| `src/ictus/interfaces/conductor/mcp.py` | MCP servers: emitting them, and checking the environment can supply them. | `mcp_servers_block`, `preflight_issues` |
| `src/ictus/interfaces/conductor/parallel.py` | Lowering parallel groups to Conductor's top-level ``parallel:`` list. | `parallel_block`, `parallel_problems` |
| `src/ictus/interfaces/conductor/resume.py` | Reading a Conductor checkpoint, and saying what resuming it will repeat. | `SESSION_RESTORING_PROVIDERS`, `CheckpointError`, `checkpoint_dir`, `latest_checkpoint`, `resume_plan` |
| `src/ictus/interfaces/conductor/serialize.py` | Rendering a compiled document to YAML text. | `dump_yaml` |
| `src/ictus/interfaces/conductor/status.py` | A script step whose result a route can trust, on an engine that does not give one. | `REPORTER`, `REPORTER_INTERPRETER`, `TIMEOUT_GRACE`, `reported_args`, `reported_timeout`, `reporter_requirement`, `result_ports`, `trusted_status_problems` |
| `src/ictus/interfaces/conductor/templates.py` | Rendering typed references into Conductor's Jinja dialect. | `reference_path`, `render` |
| `src/ictus/interfaces/conductor/trace.py` | What each step actually did, read back from the engine's event log. | `StepTrace` (investigated, at_the_cap, looked), `Trace` (capped, incurious), `find_logs`, `read_trace` |
| `src/ictus/interfaces/conductor/workflow.py` | Lowering a pipeline to Conductor's ``workflow:`` block. | `NOTHING_INHERITED`, `Inherited` (under), `workflow_block` |
| `src/ictus/interfaces/environment.py` | Environment checks that hold whatever executes the graph. | `executable_issues` |
| `src/ictus/lint/__init__.py` | Composition lints. | `check`, `lint_pipeline` |
| `src/ictus/lint/rules.py` | Generic composition rules — true of any graph, whatever executes it. | `describe`, `group_condition_problems`, `group_routing_problems`, `map_binding_problems`, `map_source_problems`, `node_problems`, `placeholder_problems`, `previous_pass_problems`, `reference_problems`, `stage_contract_problems` |
| `src/ictus/runspec.py` | The running contract: what a pipeline folder is, and how a run is specified. | `REPO_KEY`, `PipelineFolder` (module, input_file, config_file, build, name, at, find), `RunSpec`, `read_input_file`, `split_frontmatter` |
| `src/ictus/runstate.py` | Where a run keeps what it needs to be resumed — somewhere that survives. | `KEEP_FINISHED_RUNS`, `STATE_ENV`, `RunManifest` (to_json, from_json), `RunRecord` (name, engine_dir), `RunStateError`, `changed_files`, `fingerprint`, `prune_runs`, `runs_of`, `start_run`, `state_root` |
| `src/ictus/scaffold.py` | What `ictus init` writes into a new pipeline folder. | `STARTER_INPUT`, `STARTER_PIPELINE` |
| `src/ictus/stdlib/__init__.py` | Ready-made nodes and stages for the shapes that recur in every pipeline. | `AGREED`, `APPROVE_OR_REJECT`, `CONVERGED`, `DONE`, `EXHAUSTED`, `FAILED`, `HALTED`, `OK`, `READY`, `UNCLEAR`, `UNRESOLVED`, `Attempt`, `Choice`, `ReviewOption`, `ScriptStep`, `Speaker`, `Tier`, `Voice`, `approval_gate`, `ask_human`, `ask_human_for`, `bindings`, `briefing`, `briefing_gate`, `choice_gate`, `classify`, `constant`, `converge`, `council`, `counter`, `fail`, `map_stage`, `poll_until`, `remediate`, `resolve_unknowns`, `roundtable`, `save_text`, `script_sequence`, `shell`, `succeed`, `tiered`, `try_shell`, `validate_mcp`, `validate_mcps`, `verdict`, `voice`, `wait` |
| `src/ictus/stdlib/agents/__init__.py` | LLM steps for recurring shapes. | `SATISFIED`, `briefing`, `remediate`, `validate_mcp`, `verdict`, `voice` |
| `src/ictus/stdlib/agents/briefing.py` | Render structured data as prose for a human. | `briefing` |
| `src/ictus/stdlib/agents/remediate.py` | An in-flow helper that diagnoses a blocked run and talks the person through it. | `remediate` |
| `src/ictus/stdlib/agents/validate_mcp.py` | A visible, in-workflow check that one MCP server is actually connected. | `validate_mcp` |
| `src/ictus/stdlib/agents/verdict.py` | Answer a yes/no question as a typed boolean. | `verdict` |
| `src/ictus/stdlib/agents/voice.py` | A voice — one standpoint, assessing something on its own terms. | `SATISFIED`, `UNCHECKED`, `voice` |
| `src/ictus/stdlib/gates/__init__.py` | Human gates — the points where a run stops and waits for a person. | `approval_gate`, `ask_human`, `ask_human_for`, `choice_gate` |
| `src/ictus/stdlib/gates/approval.py` | Two-way approve/reject gate. | `approval_gate` |
| `src/ictus/stdlib/gates/ask.py` | Ask a person for values the run could not work out for itself. | `ask_human`, `ask_human_for` |
| `src/ictus/stdlib/gates/choice.py` | N-way human choice gate. | `choice_gate` |
| `src/ictus/stdlib/stages/__init__.py` | Reusable stages — collections of nodes worth naming as a unit. | `AGREED`, `APPROVE_OR_REJECT`, `CONVERGED`, `DONE`, `EXHAUSTED`, `FAILED`, `HALTED`, `OK`, `READY`, `UNCLEAR`, `UNRESOLVED`, `Attempt`, `Choice`, `ReviewOption`, `ScriptStep`, `Speaker`, `Tier`, `Voice`, `briefing_gate`, `classify`, `converge`, `council`, `map_stage`, `poll_until`, `resolve_unknowns`, `roundtable`, `script_sequence`, `tiered`, `try_shell`, `validate_mcps` |
| `src/ictus/stdlib/stages/briefing_gate.py` | Summarise something, then ask a human to decide about it. | `APPROVE_OR_REJECT`, `ReviewOption`, `briefing_gate` |
| `src/ictus/stdlib/stages/classify.py` | An N-way decision made by a model, with a vocabulary closed on both ends. | `UNCLEAR`, `Choice`, `classify` |
| `src/ictus/stdlib/stages/converge.py` | Try, judge, try again — bounded, with the exhaustion routable. | `CONVERGED`, `EXHAUSTED`, `Attempt`, `converge` |
| `src/ictus/stdlib/stages/council.py` | A council — several standpoints, deliberating until they agree or run out. | `AGREED`, `HALTED`, `UNRESOLVED`, `Voice`, `council` |
| `src/ictus/stdlib/stages/map_stage.py` | Run a whole stage once per item of an array resolved at run time. | `map_stage` |
| `src/ictus/stdlib/stages/poll_until.py` | Wait for something to come up, and report giving up rather than raising. | `EXHAUSTED`, `READY`, `poll_until` |
| `src/ictus/stdlib/stages/resolve_unknowns.py` | Work out what is missing, then ask a person only for what is actually missing. | `resolve_unknowns` |
| `src/ictus/stdlib/stages/roundtable.py` | A conversation — several people, taking turns, until they agree. | `AGREED`, `HALTED`, `UNRESOLVED`, `Speaker`, `roundtable` |
| `src/ictus/stdlib/stages/script_sequence.py` | A chain of shell steps with no model in the loop. | `ScriptStep`, `script_sequence` |
| `src/ictus/stdlib/stages/tiered.py` | Send the work to a tier chosen for it, and hand the caller one shape back. | `DONE`, `UNCLEAR`, `Tier`, `tiered` |
| `src/ictus/stdlib/stages/try_shell.py` | One command, whose failure the caller routes on instead of inheriting. | `FAILED`, `OK`, `try_shell` |
| `src/ictus/stdlib/stages/validate_mcps.py` | Check every MCP server a pipeline needs, at once, before the real work starts. | `validate_mcps` |
| `src/ictus/stdlib/steps/__init__.py` | Steps that cost no model call. | `bindings`, `constant`, `counter`, `save_text`, `shell`, `wait` |
| `src/ictus/stdlib/steps/bindings.py` | Several named values at once. Conductor ``type: set``. | `bindings` |
| `src/ictus/stdlib/steps/constant.py` | A single computed value. Conductor ``type: set`` — no model call. | `constant` |
| `src/ictus/stdlib/steps/counter.py` | A pass counter — the only thing that makes a loop's give-up routable. | `COUNT`, `counter` |
| `src/ictus/stdlib/steps/save_text.py` | Write a value another step produced to a file. | `save_text` |
| `src/ictus/stdlib/steps/shell.py` | A subprocess step. Conductor ``type: script`` — deterministic, no model. | `shell` |
| `src/ictus/stdlib/steps/wait.py` | A timed pause. Conductor ``type: wait``. | `wait` |
| `src/ictus/stdlib/terminals/__init__.py` | Terminal nodes — explicit, distinguishable exits. | `fail`, `succeed` |
| `src/ictus/stdlib/terminals/fail.py` | Explicit failed exit. | `fail` |
| `src/ictus/stdlib/terminals/succeed.py` | Explicit successful exit. | `succeed` |
