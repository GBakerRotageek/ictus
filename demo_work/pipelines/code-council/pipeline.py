"""Pull the change → work out what it is for → convene a council → report.

The council is the point. A single agent asked to "review this thoroughly"
writes one perspective wearing several hats, and the hats agree. Four voices,
each given a different thing to care about and each shown the others'
conclusions, disagree — and a recorded disagreement about a tradeoff is worth
more than a consensus nobody actually held.

Note what the pipeline does with a council that does *not* converge: it routes
it somewhere different. That is the whole argument for outcomes over
exceptions — "four people looked at this and could not agree" is a result, and
a useful one, not a failure.
"""

from __future__ import annotations

from ictus import Pipeline, PortType
from ictus.graph.node import AgentNode
from ictus.graph.ports import InputPort, OutputPort
from ictus.graph.ref import tpl
from ictus.stdlib import AGREED, HALTED, UNRESOLVED, Voice, council, save_text, succeed

STR = PortType.STRING

VOICES = (
    Voice(
        node_id="correctness",
        persona=(
            "You have spent years on the receiving end of incident reviews and you "
            "have learned that most outages trace back to a case someone decided was "
            "too unlikely to handle."
        ),
        focus="whether this is correct, including the inputs nobody wrote a test for",
    ),
    Voice(
        node_id="performance",
        persona=(
            "You have been paged at 3am for a regression that profiling later showed "
            "was one allocation in a loop. You do not object to slow code; you object "
            "to code whose cost is invisible at the call site."
        ),
        focus="allocation, IO in loops, and work that grows faster than the input",
    ),
    Voice(
        node_id="patterns",
        persona=(
            "You maintain this codebase. You care less about whether a thing is good "
            "in the abstract than about whether it is the same as its neighbours, "
            "because the cost of a second way to do something is paid by everyone "
            "who reads it afterwards."
        ),
        focus="consistency with the surrounding code, and whether a new idea earns itself",
    ),
    Voice(
        node_id="blast_radius",
        persona=(
            "You have watched a two-line change take down a service because of what "
            "depended on it. You read a diff by asking who else is standing on the "
            "part being moved."
        ),
        focus="what else depends on this, and what happens on the way to deploying it",
    ),
)

review = council(
    stage_id="code-council",
    voices=VOICES,
    subject="The change under review",
    rounds=3,
    interject=True,
    charge="What this review is for, and any constraint the voices must respect",
    synthesis=(
        "Where a voice objects on grounds another voice's suggestion would fix, say "
        "so explicitly — that pairing is usually the actual finding."
    ),
    description="Four standpoints on one change",
)

code_council = Pipeline(
    pipeline_id="code-council-run",
    description="Assess a change from four standpoints and report",
)
target = code_council.declare_input(
    "target", STR, description="What to assess — a git ref, a path, or a diff"
)
charge = code_council.declare_input(
    "charge",
    STR,
    required=False,
    prose=True,
    description="Standing instruction for every voice, e.g. 'we ship this Friday'",
)

pull = code_council.add(
    AgentNode(
        node_id="pull",
        description="Fetch the change and read enough around it to judge it",
        inputs=(InputPort("target", STR),),
        prompt=tpl(
            "Fetch the change described below and read enough of the surrounding code "
            "to judge it. Return the change itself in `diff`, and in `context` whatever "
            "a reader would need that is not in the diff — what calls this, what it "
            "used to do, which tests cover it.\n\n",
            target.ref(),
        ),
        declared_outputs=(
            OutputPort("diff", STR, "The change"),
            OutputPort("context", STR, "What surrounds it"),
        ),
    )
)
purpose = code_council.add(
    AgentNode(
        node_id="purpose",
        description="Work out what the change is meant to achieve",
        inputs=(InputPort("diff", STR), InputPort("context", STR)),
        prompt=tpl(
            "State what this change is meant to achieve, in a few sentences. Judge it "
            "later against this, not against taste — a change that does something "
            "different from what it set out to do is the finding, and you cannot see "
            "that without writing down the intent first.\n\n--- change ---\n",
            pull.ref("diff"),
            "\n\n--- context ---\n",
            pull.ref("context"),
        ),
        declared_outputs=(OutputPort("intent", STR, "What this is for"),),
    )
)

seat = review.instantiate(code_council, node_id="council")

# A review whose report only exists in terminal scrollback is a review nobody
# reads twice. It lands in the project being assessed, next to the code.
write_report = code_council.add(
    save_text(
        node_id="write_report",
        description="Write the council's report into the project",
        text=seat.ref("report"),
        to="council-review.md",
        inputs=(InputPort("report", STR),),
    )
)

agreed = code_council.add(
    succeed(
        node_id="reported",
        reason="The council agreed",
        inputs=(InputPort("written", STR), InputPort("rounds", PortType.NUMBER)),
        result={"report": tpl(write_report.ref("path")), "rounds": tpl(seat.ref("rounds"))},
    )
)
split = code_council.add(
    succeed(
        node_id="contested",
        reason="The council did not converge; a person should read the disagreement",
        inputs=(InputPort("report", STR), InputPort("dissent", STR)),
        result={"report": tpl(seat.ref("report")), "dissent": tpl(seat.ref("dissent"))},
    )
)

code_council.set_entry(pull)
code_council.connect_input(target, pull, "target")
code_council.connect(pull, "diff", purpose, "diff")
code_council.feed(pull, "context", purpose, "context")
code_council.route(purpose, seat)
# The council assesses what `pull` fetched, not the string that named it — wiring
# the raw target here would hand four voices the text "HEAD~1" to review.
code_council.feed(pull, "diff", seat, "subject")
code_council.connect_input(charge, seat, "charge")
code_council.feed(purpose, "intent", seat, "intent")

code_council.feed(seat, "report", write_report, "report")
code_council.connect(write_report, "path", agreed, "written")
code_council.feed(seat, "rounds", agreed, "rounds")
code_council.feed(seat, "report", split, "report")
code_council.feed(seat, "dissent", split, "dissent")
code_council.branch_on_outcome(seat, {AGREED: write_report, UNRESOLVED: split, HALTED: split})
