"""What does this library still need? Four standpoints, deliberating.

A review of a *change* and a review of a *codebase's needs* are different jobs
and want different prompts. A change arrives with an intent you can judge it
against; a codebase does not, so the first step here is to work out what is
actually there before anyone is asked what is missing.

The voices carry tools, unlike the ones on a change review. That inverts on
purpose: for a diff the material fits in the prompt and four agents each going
to look at the same file is waste, but a repository does not fit — `src/` alone
is around eighty thousand tokens — so each voice reads what its own focus needs
instead of every voice carrying everything, every round.

Run it from the repository you want reviewed:

    cd ~/work/the-project
    ictus run ~/…/demo_work/pipelines/needs-council
"""

from __future__ import annotations

from ictus import (
    AgentNode,
    Executable,
    InputPort,
    OutputPort,
    Pipeline,
    PortType,
    not_equals,
    optional,
    tpl,
)
from ictus.stdlib import AGREED, HALTED, UNRESOLVED, Voice, council, save_text, succeed

STR = PortType.STRING

VOICES = (
    Voice(
        node_id="capability",
        persona=(
            "You have built orchestration systems before, and you have learned that the "
            "interesting failures are not bugs — they are the things an author could not "
            "say. When a library cannot express a shape, people build it out of the "
            "shapes that do exist, and the result works until it does not."
        ),
        focus="what a pipeline needs to express that this library cannot",
        tools=None,
        # These read the repository and Conductor's own source. The default
        # fifty is a kill, not a throttle, and it lands after the earlier
        # rounds have been paid for.
        max_turns=200,
    ),
    Voice(
        node_id="experience",
        persona=(
            "You have watched people use tools you built, which is a different thing from "
            "using them yourself. You know that a run nobody can understand while it is "
            "happening gets killed and started again, and that the second most common "
            "reason people abandon a tool is that they could not tell whether it was "
            "working."
        ),
        focus="the person launching a run, watching it, and answering it mid-flight",
        tools=None,
        # These read the repository and Conductor's own source. The default
        # fifty is a kill, not a throttle, and it lands after the earlier
        # rounds have been paid for.
        max_turns=200,
    ),
    Voice(
        node_id="authoring",
        persona=(
            "You onboard people onto codebases for a living. You judge a library by how "
            "much someone has to hold in their head before their first attempt runs, and "
            "you know the first pipeline a person writes decides whether they write a "
            "second one."
        ),
        focus="what a developer must learn, decide and get right to author a new pipeline",
        tools=None,
        # These read the repository and Conductor's own source. The default
        # fifty is a kill, not a throttle, and it lands after the earlier
        # rounds have been paid for.
        max_turns=200,
    ),
    Voice(
        node_id="agent_power",
        persona=(
            "You use Claude Code every day and know precisely which of its abilities do "
            "the work — reading and editing files, running commands, searching the web, "
            "sub-agents, skills, MCP servers, planning before acting. You also know that "
            "switching all of them on by default is how an agent wanders off and burns a "
            "budget, so what you argue for is opt-in, per step, declared where the step is."
        ),
        focus=(
            "which agent capabilities a node should be able to opt into, and what it "
            "costs to leave them switched on by default"
        ),
        tools=None,
        # These read the repository and Conductor's own source. The default
        # fifty is a kill, not a throttle, and it lands after the earlier
        # rounds have been paid for.
        max_turns=200,
    ),
)

review = council(
    stage_id="needs-review",
    voices=VOICES,
    subject="What the library can do today",
    charge="What this review is for, and anything the voices must take as settled",
    rounds=3,
    interject=True,
    verify=(
        "This project compiles to Conductor workflow YAML, and the installed engine is "
        "the ground truth for every claim about what can and cannot be expressed. Read "
        "its schema and source before agreeing with anything: "
        '`python -c "import conductor, pathlib; print(pathlib.Path(conductor.__file__).parent)"` '
        "finds it; `config/schema.py` is the field-by-field contract and `engine/` is "
        "what actually runs. A claim that something is impossible, missing, or works a "
        "certain way is checkable there in under a minute, and most such claims in a "
        "report like this are wrong because nobody looked."
    ),
    synthesis=(
        "Group what you find by what it would take to close it: a prompt, a new "
        "constructor, a change to the engine boundary, or a decision nobody has made "
        "yet. A need that cannot be sized is a need nobody will pick up."
    ),
    description="Four standpoints on what this library still needs",
)

needs_council = Pipeline(
    pipeline_id="needs-council",
    description="Work out what a library can do, then ask four voices what it still needs",
)
# The target repository's own `AGENTS.md` sends every voice to Conductor's source
# to settle any claim about what the engine can express — the run picks it up
# because `ictus run` passes `--workspace-instructions`. Without the CLI there is
# nothing to resolve that path from, and a voice that cannot reach the schema
# does not stop: it reports the field as missing. Refuse the launch instead.
needs_council.require_executable(
    Executable(
        name="conductor",
        purpose="the schema every claim about the engine is checked against",
        probe=("--version",),
        setup_hint="uv tool install conductor-cli",
    )
)
scope = needs_council.declare_input(
    "scope",
    STR,
    required=False,
    description="Narrow the survey, e.g. 'the stdlib and the CLI'. Empty means everything.",
)
charge = needs_council.declare_input(
    "charge",
    STR,
    required=False,
    prose=True,
    description="Standing instruction every voice receives",
)

# One orientation step, because you cannot say what is missing without knowing
# what is there. Its output is what the council assesses — not the source, which
# would not fit in four prompts three times over.
survey = needs_council.add(
    AgentNode(
        node_id="survey",
        description="Work out what this library can do today",
        inputs=(InputPort("scope", STR, optional=True),),
        prompt=tpl(
            "You are looking at a codebase in the current working directory. Work out "
            "what it can do today, for someone who is about to be asked what it still "
            "needs.\n\n"
            "In `inventory`, write what exists: the concepts it models, what its "
            "standard library covers, what its command line offers, what it guarantees "
            "and refuses. Be concrete — name the constructs. Where you can see a gap "
            "plainly, say so, but do not editorialise; that is somebody else's job.\n\n"
            "In `purpose`, state what the project is for and who it serves, taken from "
            "its own README and code rather than from what you would like it to be. "
            "The voices judge needs against this, so a purpose you invented sends the "
            "whole review somewhere useless.\n\n",
            "Read the README, the package layout, the standard library and the CLI. "
            "You do not need to read every file.\n",
            optional(
                "\nNarrow it to this and say so if the narrowing hides something "
                "the reviewers would need: ",
                scope.ref(),
            ),
        ),
        declared_outputs=(
            OutputPort("inventory", STR, "What the library can do today"),
            OutputPort("purpose", STR, "What it is for, from its own account"),
        ),
    )
)

seat = review.instantiate(needs_council, node_id="council")

write_report = needs_council.add(
    save_text(
        node_id="write_report",
        description="Write the council's report into the repository",
        text=tpl(
            seat.ref("report"),
            "\n\n---\n\n## Still contested\n\n",
            seat.ref("dissent"),
            "\n\n## Did not survive checking\n\n",
            seat.ref("corrections"),
        ),
        to="needs-review.md",
        inputs=(
            InputPort("report", STR),
            InputPort("dissent", STR),
            InputPort("corrections", STR),
        ),
    )
)

agreed = needs_council.add(
    succeed(
        node_id="reported",
        reason="The council agreed on what this needs",
        inputs=(InputPort("written", STR), InputPort("rounds", PortType.NUMBER)),
        result={"report": tpl(write_report.ref("path")), "rounds": tpl(seat.ref("rounds"))},
    )
)
split = needs_council.add(
    succeed(
        node_id="contested",
        reason="The council did not converge; a person should read the disagreement",
        inputs=(InputPort("written", STR), InputPort("dissent", STR)),
        result={"report": tpl(write_report.ref("path")), "dissent": tpl(seat.ref("dissent"))},
    )
)

needs_council.set_entry(survey)
needs_council.connect_input(scope, survey, "scope")
needs_council.route(survey, seat)
needs_council.feed(survey, "inventory", seat, "subject")
needs_council.feed(survey, "purpose", seat, "intent")
needs_council.connect_input(charge, seat, "charge")

needs_council.feed(seat, "report", write_report, "report")
needs_council.feed(seat, "dissent", write_report, "dissent")
needs_council.feed(seat, "corrections", write_report, "corrections")
needs_council.connect(write_report, "path", agreed, "written")
needs_council.feed(seat, "rounds", agreed, "rounds")
needs_council.feed(write_report, "path", split, "written")
needs_council.feed(seat, "dissent", split, "dissent")
# Every way out writes the file first; the outcome decides where it lands after.
# Routing only `agreed` through here left the report of a council that could not
# converge — the one most worth reading — in the run's output and nowhere else.
needs_council.route(write_report, split, when=not_equals(seat.ref("outcome"), AGREED))
needs_council.branch_on_outcome(
    seat, {AGREED: write_report, UNRESOLVED: write_report, HALTED: write_report}
)
