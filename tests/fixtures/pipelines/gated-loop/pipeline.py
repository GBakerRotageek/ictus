"""A gate that can send the work back — the smallest pipeline with a cycle.

    brief -> draft -> review -+-- approved --> done
               ^              |
               +-- rejected --+

It is a fixture rather than a demo: the point is not what it does but that the
CLI can find it, apply its `config.yaml`, emit it and hand the result to
Conductor. It carries a loop, a gate, a forward reference across the loop, an
MCP requirement and an exposed output because each of those reaches the emitted
YAML by a different path, and a fixture that exercised none of them would make
`make soundcheck` green without checking the parts that break.
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
from ictus.stdlib import approval_gate, succeed

STRING = PortType.STRING

gated_loop = Pipeline(
    pipeline_id="gated-loop",
    description="Draft something, gate it on a person, revise until approved.",
    # Two passes through draft -> review -> draft. Without a declared bound
    # Conductor's default of ten total steps ends the run inside the second
    # review rather than at a terminal.
    loop_passes=2,
    budget_usd=1.0,
    budget_mode="enforce",
    metadata={"generator": "ictus", "pipeline": "gated-loop"},
)

gated_loop.require_mcp(
    McpServer(
        name="github",
        purpose="Read the surrounding code while drafting",
        transport=McpTransport.HTTP,
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer ${GITHUB_TOKEN:-}"},
        env=(EnvVar("GITHUB_TOKEN", "a GitHub token with repo scope"),),
        setup_hint="export GITHUB_TOKEN=$(gh auth token)",
    )
)

brief = gated_loop.declare_input("brief", STRING, description="What to draft")

draft = gated_loop.add(
    AgentNode(
        node_id="draft",
        description="Write a draft against the brief",
        inputs=(
            InputPort("brief", STRING),
            InputPort("notes", STRING, "Notes from a previous rejection", optional=True),
        ),
        prompt=tpl(
            "Draft a response to this brief.\n\n",
            brief.ref(),
            "\n\n",
            # Resolved against the finished graph: `review` is declared below.
            # The guard the first pass needs is emitted by the compiler.
            optional(
                "A previous draft was rejected with these notes — address them:\n",
                ref_to("review", "notes", STRING),
            ),
        ),
        declared_outputs=(OutputPort("draft", STRING, "The draft"),),
    )
)

review = gated_loop.add(
    approval_gate(
        node_id="review",
        description="Human review of the draft",
        inputs=(InputPort("draft", STRING),),
        prompt=tpl("Approve this draft?\n\n", draft.ref("draft")),
        reject_label="Reject and revise",
    )
)

done = gated_loop.add(
    succeed(
        node_id="done",
        description="Draft approved",
        inputs=(InputPort("draft", STRING),),
        reason="A person approved the draft.",
        result={"draft": "{{ draft.output.draft }}"},
    )
)

gated_loop.set_entry(draft)
gated_loop.connect_input(brief, draft, "brief")
gated_loop.connect(draft, "draft", review, "draft")
gated_loop.branch(review, {"approved": done, "rejected": draft})

# Control goes through the gate; the data does not. The rejection notes reach
# the next draft only because this says so.
gated_loop.feed(review, "notes", draft, "notes")
gated_loop.feed(draft, "draft", done, "draft")

gated_loop.expose_output("draft", draft, "draft")
