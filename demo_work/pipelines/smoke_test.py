"""The smoke-test pipeline.

    ticket -> plan -> breakdown -> review gate -+-- approve --> execute -> test -> done
                          ^                     |
                          +----- reject --------+

The reject branch carries the reviewer's notes back into ``breakdown``, so a
second pass has something the first did not.

Every reference below is a typed ``Ref``, not template text: the port must exist
and its type must match at the point it is written. The guard the loop-back
reference needs is emitted by the compiler, not remembered here.
"""

from __future__ import annotations

from ictus import (
    AgentNode,
    EnvVar,
    InputPort,
    McpServer,
    McpTransport,
    OutputPort,
    Pipeline,
    PortType,
    optional,
    ref_to,
    tpl,
)
from ictus.stdlib import approval_gate, resolve_unknowns, succeed

STRING, ARRAY, OBJECT = PortType.STRING, PortType.ARRAY, PortType.OBJECT

smoke_test = Pipeline(
    pipeline_id="smoke-test",
    description="Plan a ticket, break it down, gate it on a human, execute and test.",
    # One loop (breakdown -> gate -> breakdown). Three passes is the declared
    # bound; without it Conductor's default of 10 total steps stops the run
    # midway through the second review.
    loop_passes=3,
    # Conductor's own default is copilot. Emitting the provider explicitly means
    # the choice is visible in the diff rather than discovered on a failed run.
    provider="claude-agent-sdk",
    budget_usd=5.0,
    budget_mode="enforce",
    metadata={"generator": "ictus", "pipeline": "smoke-test"},
)

# Declared here, checked before launch. `gh` keeps its token in the keyring
# rather than the environment, so this blocks on a fresh shell — which is the
# whole point: the run should not start and then discover it cannot read the
# repository.
smoke_test.require_mcp(
    McpServer(
        name="github",
        purpose="Read the ticket and surrounding code while planning and breaking it down",
        transport=McpTransport.HTTP,
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer ${GITHUB_TOKEN:-}"},
        env=(
            EnvVar(
                "GITHUB_TOKEN",
                "a GitHub token with repo scope; `gh auth token` prints yours",
            ),
        ),
        setup_hint="export GITHUB_TOKEN=$(gh auth token)",
    )
)

ticket = smoke_test.declare_input("ticket", STRING, description="The ticket to work")

# A ticket rarely says where the code lives. Work out what can be determined,
# ask a person only for what cannot, and refuse to guess a path — an unanswered
# question is recoverable, a fabricated checkout path is not.
locate = resolve_unknowns(
    stage_id="locate-work",
    subject="ticket",
    needs=(
        "the filesystem path of every repository this ticket requires changes in",
        "which repository owns the change, if more than one is involved",
    ),
)
context = locate.instantiate(smoke_test, node_id="locate", description="Locate the work")

plan = smoke_test.add(
    AgentNode(
        node_id="plan",
        description="Turn the raw ticket into a stated goal and constraints",
        inputs=(
            InputPort("ticket", STRING),
            InputPort("known", OBJECT),
            InputPort("answers", OBJECT),
        ),
        prompt=tpl(
            "Read this ticket and restate it as a goal with explicit constraints "
            "and acceptance criteria.\n\n",
            ticket.ref(),
            "\n\nWhat was already known about where the work lives:\n",
            context.ref("known"),
            "\n\nWhat a person supplied when it could not be determined:\n",
            context.ref("answers"),
        ),
        declared_outputs=(OutputPort("plan", STRING, "Goal, constraints, acceptance criteria"),),
    )
)

breakdown = smoke_test.add(
    AgentNode(
        node_id="breakdown",
        description="Decompose the plan into ordered steps",
        inputs=(
            InputPort("plan", STRING),
            InputPort("notes", STRING, "Reviewer notes from a previous rejection", optional=True),
        ),
        prompt=tpl(
            "Break this plan into ordered, individually verifiable steps.\n\n",
            plan.ref("plan"),
            "\n\n",
            # Forward reference across the loop; the compiler adds the guard the
            # first pass needs, because it knows the reference is deferred.
            optional(
                "A previous breakdown was rejected with these notes — address them:\n",
                ref_to("review_gate", "notes", STRING),
            ),
        ),
        declared_outputs=(OutputPort("steps", ARRAY, "Ordered steps"),),
    )
)

review_gate = smoke_test.add(
    approval_gate(
        node_id="review_gate",
        description="Human review of the breakdown",
        inputs=(InputPort("steps", ARRAY),),
        prompt=tpl("Approve this breakdown?\n\n", breakdown.ref("steps")),
        reject_label="Reject and revise",
    )
)

execution = smoke_test.add(
    AgentNode(
        node_id="execution",
        description="Carry out the approved steps",
        inputs=(InputPort("steps", ARRAY),),
        prompt=tpl("Carry out these approved steps, one at a time.\n\n", breakdown.ref("steps")),
        declared_outputs=(OutputPort("results", OBJECT, "What each step produced"),),
    )
)

testing = smoke_test.add(
    AgentNode(
        node_id="testing",
        description="Verify the results against the acceptance criteria",
        inputs=(InputPort("results", OBJECT), InputPort("plan", STRING)),
        prompt=tpl(
            "Check these results against the acceptance criteria and report what "
            "passed and what did not.\n\nCriteria:\n",
            plan.ref("plan"),
            "\n\nResults:\n",
            execution.ref("results"),
        ),
        declared_outputs=(OutputPort("report", STRING, "Pass/fail report"),),
    )
)

finish = smoke_test.add(
    succeed(
        node_id="finish",
        description="Ticket complete",
        inputs=(InputPort("report", STRING),),
        reason="Breakdown approved, executed and tested.",
        result={"report": "{{ testing.output.report }}"},
    )
)

smoke_test.set_entry(context)
smoke_test.connect_input(ticket, context, "brief")
smoke_test.route(context, plan)
smoke_test.connect_input(ticket, plan, "ticket")
smoke_test.feed(context, "known", plan, "known")
smoke_test.feed(context, "answers", plan, "answers")
smoke_test.connect(plan, "plan", breakdown, "plan")
smoke_test.connect(breakdown, "steps", review_gate, "steps")
smoke_test.branch(review_gate, {"approved": execution, "rejected": breakdown})

# Control goes through the gate; the data does not. Conductor keeps `routes` and
# `input` separate, so the values each branch target reads are declared here.
smoke_test.feed(review_gate, "notes", breakdown, "notes")
smoke_test.feed(breakdown, "steps", execution, "steps")
smoke_test.feed(plan, "plan", testing, "plan")

smoke_test.connect(execution, "results", testing, "results")
smoke_test.connect(testing, "report", finish, "report")

smoke_test.expose_output("report", testing, "report")
