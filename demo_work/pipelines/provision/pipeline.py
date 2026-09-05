"""A pipeline assembled almost entirely from stdlib stages.

    [check-connections] -> [reset-db] -> [confirm-reset] -+- approved -> live
                                                          |
                                                          +- else ----> aborted

Two stages, so three workflow files: the parent plus one per stage. Each stage
costs the parent a single iteration however many steps it contains.

The branch lives here, not inside the review stage. That is what makes
``briefing_gate`` reusable: it obtains a decision and reports it, and each
caller decides what the decision means.
"""

from __future__ import annotations

from ictus import EnvVar, McpServer, McpTransport, Pipeline, PortType, equals
from ictus.stdlib import (
    ReviewOption,
    ScriptStep,
    briefing_gate,
    script_sequence,
    succeed,
    validate_mcps,
)

STRING = PortType.STRING

reset_db = script_sequence(
    stage_id="reset-db",
    description="Drop, migrate and seed a database",
    parameter="environment",
    steps=(
        # Relative to the emitted workflow's directory (demo_work/build/),
        # not the repo root.
        ScriptStep("drop", "./db.sh", ("drop",), "dropped", "Drop the schema"),
        ScriptStep("migrate", "./db.sh", ("migrate",), "revision", "Apply migrations"),
        ScriptStep("seed", "./db.sh", ("seed",), "status", "Load seed data"),
    ),
    # A script command resolves against working_dir, which otherwise defaults to
    # wherever the run was launched from. Saying it here is the difference between
    # a pipeline that works and one that works on one machine.
    working_dir="demo_work/scripts",
)

confirm = briefing_gate(
    stage_id="confirm-reset",
    subject="database reset",
    question="The database was reset. Take this environment live?",
    data_type=STRING,
    # A rejection that cannot say why makes the next attempt a guess, and a
    # reviewer who can only say "no" cannot say *where* it should go back to.
    options=(
        ReviewOption("approved", "Take it live"),
        ReviewOption("redo", "Reset it again — with notes", ask_for_notes=True),
        ReviewOption("abort", "Abort — do not go live", ask_for_notes=True),
    ),
)

provision = Pipeline(
    pipeline_id="provision",
    description="Reset a database, then gate going live on a human.",
    # The review can send the work back, so the graph loops and needs a bound.
    loop_passes=3,
    metadata={"generator": "ictus", "pipeline": "provision"},
)

# Declared where the pipeline is written, checked before it launches. Neither
# of these is configured on a fresh machine, which is the point: `ictus run`
# refuses rather than failing halfway through a database reset.
github = provision.require_mcp(
    McpServer(
        name="github",
        purpose="Read the change set being provisioned and comment on its PR",
        transport=McpTransport.HTTP,
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer ${GITHUB_TOKEN:-}"},
        env=(EnvVar("GITHUB_TOKEN", "a token with repo:read and pull_request:write"),),
        setup_hint="export GITHUB_TOKEN=$(gh auth token)",
    )
)
workspace = provision.require_mcp(
    McpServer(
        name="filesystem",
        purpose="Read migration files and seed data from the workspace",
        transport=McpTransport.STDIO,
        command="npx",
        # Relative to the emitted workflow's directory, like every other path.
        args=("-y", "@modelcontextprotocol/server-filesystem", "."),
        setup_hint="Install Node so `npx` is on PATH",
    )
)

environment = provision.declare_input("environment", STRING, description="Environment to provision")

# The same servers declared above, checked from inside the run so whoever is
# watching sees which one is missing — and can connect it and retry without
# losing the run. `ictus preflight` is the cheap offline gate before launch;
# this is the visible one, and the only check that proves a server actually
# reached the model.
preflight_stage = validate_mcps(
    stage_id="check-connections",
    servers=(github, workspace),
    description="Confirm every MCP server answers before touching a database",
)
checks = preflight_stage.instantiate(provision, node_id="check_connections")

reset = reset_db.instantiate(provision, node_id="reset", description="Reset the database")
review = confirm.instantiate(provision, node_id="confirm", description="Confirm before going live")

live = provision.add(succeed(node_id="live", reason="Reset confirmed; environment is live."))
aborted = provision.add(succeed(node_id="aborted", reason="Operator declined the reset."))

provision.set_entry(checks)
provision.route(checks, reset)
provision.connect_input(environment, reset, "environment")
provision.connect(reset, "result", review, "data")

# Branch on the stage's reported decision. The catch-all is emitted last
# regardless of the order written here, so it cannot shadow the condition.
provision.route(review, live, when=equals(review.ref("decision"), "approved"))
provision.route(review, reset, when=equals(review.ref("decision"), "redo"))
provision.route(review, aborted)

# The reviewer's notes are part of the result, not something read once on screen
# and lost when the run ends.
provision.expose_output("review_notes", review, "notes")
