"""What of the engine are we not using? Four standpoints, deliberating.

`needs-council` asks what this library still needs. This asks the inverse and
narrower question: of what the engine already offers, what have we never
reached for — and of that, what is worth reaching for.

The distinction matters because the two produce different work. A need is
something to design; an unused capability is something to *wire*, and wiring is
the cheapest kind of progress there is. Three rounds of `needs-council` in a row
turned up engine fields nobody had connected, each one found by reading
Conductor rather than by inventing a feature.

The voices carry tools and the web, because half the answer is not in the
installed package: what the project has shipped since the pinned version, what
its issues and releases say is coming, and how anyone else drives it.

Run it from this repository, so the survey can see what we already use:

    cd ~/…/ictus
    ictus run demo_work/pipelines/untapped
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

# Every voice reads the installed package and searches the web, so every voice
# needs a ceiling: the engine's fifty is a kill rather than a throttle, and a
# search that goes one page too far would take the whole council down with it.
LOOKING = 200

VOICES = (
    Voice(
        node_id="engine",
        persona=(
            "You read execution engines for a living and you have learned that the "
            "interesting capability is rarely the one on the front page. It is the "
            "field in the schema nobody connected, the lifecycle hook with one caller, "
            "the subsystem that exists because someone needed it once and then "
            "documented it badly."
        ),
        focus=(
            "machinery the installed engine already has that no pipeline here can ask "
            "for — lifecycle, recovery, concurrency, the shapes a workflow may take"
        ),
        tools=None,
        max_turns=LOOKING,
    ),
    Voice(
        node_id="field",
        persona=(
            "You have watched a dozen orchestration tools arrive and you judge them by "
            "what people actually build once they have them, not by the feature list. "
            "You read release notes, issues and other people's workflows before you "
            "form a view, because a capability nobody uses is usually telling you "
            "something."
        ),
        focus=(
            "what the project has shipped, announced or been asked for beyond the "
            "installed version, and which of it would change what this library can do"
        ),
        tools=None,
        max_turns=LOOKING,
    ),
    Voice(
        node_id="operator",
        persona=(
            "You run other people's pipelines at three in the morning. What you value "
            "is not expressiveness but the ability to see what is happening, stop it, "
            "steer it, and pick it back up — and you know that a wrapper which hides "
            "the engine's own controls has taken something away."
        ),
        focus=(
            "what a person driving the engine directly can do that this library's "
            "command line does not let them do"
        ),
        tools=None,
        max_turns=LOOKING,
    ),
    Voice(
        node_id="extension",
        persona=(
            "You build on top of other people's systems and you look first for the "
            "seams they left deliberately — the plugin loader, the registry, the "
            "protocol, the server mode. You would rather extend a system through a "
            "hook it documents than around it through one it does not."
        ),
        focus=(
            "where the engine is extensible rather than merely featureful, and what "
            "this library could build on those seams instead of beside them"
        ),
        tools=None,
        max_turns=LOOKING,
    ),
)

review = council(
    stage_id="untapped-review",
    voices=VOICES,
    subject="What we already use of the engine, and what the engine is",
    charge="What this brainstorm is for, and anything the voices must take as settled",
    rounds=3,
    interject=True,
    verify=(
        "You are checking a brainstorm about an engine that is installed on this "
        "machine and readable. Two failure modes matter more than the rest, and both "
        "produce claims that read perfectly well.\n\n"
        "First: the wrong product. The engine here is `conductor-cli` from "
        "github.com/microsoft/conductor, built on the GitHub Copilot SDK. Several "
        "unrelated products are also called Conductor — Netflix's workflow engine "
        "most prominently. A capability that turns out to belong to one of those is "
        "not a finding, it is a different tool. Anything sourced from the web needs "
        "checking against the installed package before it survives.\n\n"
        "Second: already done. A capability this library reaches today is not "
        "untapped. Check `src/ictus/` before agreeing that something is unused — "
        "`interfaces/conductor/` is where every engine-facing field is emitted, and "
        "`lints.py` keeps three tables saying which fields are wired and which are "
        "deliberately refused.\n\n"
        "For anything that survives both, say whether the provider this project "
        "actually runs would honour it. A capability the chosen provider ignores is "
        "worth knowing about and is not worth wiring."
    ),
    synthesis=(
        "Sort what you find by what it would cost to take up: a field to emit, a "
        "command to wrap, a stdlib constructor to write, a change upstream, or a "
        "decision nobody has made. Say plainly which items are *deliberately* not "
        "taken up rather than merely unused — a wrapper that exposed everything "
        "underneath it would be a worse tool than one that chose.\n\n"
        "Where a capability is real but the provider this project runs ignores it, "
        "keep it and mark it: that is a finding about the roadmap, not about today."
    ),
    description="Four standpoints on the engine surface this library does not use",
)

untapped = Pipeline(
    pipeline_id="untapped",
    description="Work out what the engine offers, then ask four voices what we are not using",
)
# The whole brainstorm is a claim about an installed package. Without the CLI
# there is nothing to resolve its path from, and a voice that cannot read the
# engine does not stop — it reports whatever the web told it, which for a name
# this common is usually a different product.
untapped.require_executable(
    Executable(
        name="conductor",
        purpose="the engine whose unused surface this brainstorm is about",
        probe=("--version",),
        setup_hint="uv tool install conductor-cli",
    )
)

focus = untapped.declare_input(
    "focus",
    STR,
    required=False,
    description="Narrow it, e.g. 'recovery and resumption'. Empty means the whole surface.",
)
charge = untapped.declare_input(
    "charge",
    STR,
    required=False,
    prose=True,
    description="Standing instruction every voice receives",
)

# Both halves of the ledger in one step. A council given only the engine's
# surface proposes things that are already wired — which is exactly what the
# last three rounds of `needs-council` did until someone put the other half in
# front of it.
survey = untapped.add(
    AgentNode(
        node_id="survey",
        description="What the engine offers, and which of it this library already uses",
        inputs=(InputPort("focus", STR, optional=True),),
        # It reads two trees and the engine's CLI, which is more looking than a
        # voice does; the ceiling is raised to match.
        max_turns=250,
        prompt=tpl(
            "You are establishing both halves of a ledger, for people who will then "
            "be asked what is missing from the right-hand side.\n\n"
            "The engine is `conductor-cli`, installed as a uv tool. This repository's "
            "own instructions say how to resolve its source; follow them rather than "
            "guessing, and note the installed version from `conductor --version`.\n\n"
            "In `surface`, write what the engine offers: its top-level packages and "
            "what each is for, its CLI subcommands, and the shapes a workflow may "
            "take. Breadth over depth — you are drawing a map, not surveying a field. "
            "Name things concretely enough that someone can go and read them.\n\n"
            "In `used`, write which of that this library already reaches. "
            "`src/ictus/interfaces/conductor/` is the only package permitted to know "
            "the engine's spelling, so it is where the answer is; `lints.py` holds "
            "three tables naming fields that are wired, fields refused because the "
            "provider ignores them, and fields the engine checks itself. Read the CLI "
            "too — `src/ictus/cli.py` — for which of the engine's commands are "
            "wrapped. Be exact about what is already done. Someone proposing work "
            "that exists is the failure this step exists to prevent.\n",
            optional(
                "\nNarrow both halves to this, and say so if the narrowing hides "
                "something the reviewers would need: ",
                focus.ref(),
            ),
        ),
        declared_outputs=(
            OutputPort("surface", STR, "What the engine offers, and its version"),
            OutputPort("used", STR, "Which of it this library already reaches"),
        ),
    )
)

seat = review.instantiate(untapped, node_id="council")

write_report = untapped.add(
    save_text(
        node_id="write_report",
        description="Write the brainstorm into the repository",
        text=tpl(
            seat.ref("report"),
            "\n\n---\n\n## Still contested\n\n",
            seat.ref("dissent"),
            "\n\n## Could not be checked\n\n",
            seat.ref("unverified"),
            "\n\n## Did not survive checking\n\n",
            seat.ref("corrections"),
        ),
        to="untapped.md",
        inputs=(
            InputPort("report", STR),
            InputPort("dissent", STR),
            InputPort("unverified", STR),
            InputPort("corrections", STR),
        ),
    )
)

agreed = untapped.add(
    succeed(
        node_id="reported",
        reason="The council agreed on what we are leaving unused",
        inputs=(InputPort("written", STR), InputPort("rounds", PortType.NUMBER)),
        result={"report": tpl(write_report.ref("path")), "rounds": tpl(seat.ref("rounds"))},
    )
)
split = untapped.add(
    succeed(
        node_id="contested",
        reason="The council did not converge; a person should read the disagreement",
        inputs=(InputPort("written", STR), InputPort("dissent", STR)),
        result={"report": tpl(write_report.ref("path")), "dissent": tpl(seat.ref("dissent"))},
    )
)

untapped.set_entry(survey)
untapped.connect_input(focus, survey, "focus")
untapped.route(survey, seat)
# `surface` is what the council assesses; `used` is what it must not re-propose.
untapped.feed(survey, "surface", seat, "subject")
untapped.feed(survey, "used", seat, "intent")
untapped.connect_input(charge, seat, "charge")

untapped.feed(seat, "report", write_report, "report")
untapped.feed(seat, "dissent", write_report, "dissent")
untapped.feed(seat, "unverified", write_report, "unverified")
untapped.feed(seat, "corrections", write_report, "corrections")
untapped.connect(write_report, "path", agreed, "written")
untapped.feed(seat, "rounds", agreed, "rounds")
untapped.feed(write_report, "path", split, "written")
untapped.feed(seat, "dissent", split, "dissent")
# Every way out writes the file first; the outcome only decides where it lands.
untapped.route(write_report, split, when=not_equals(seat.ref("outcome"), AGREED))
untapped.branch_on_outcome(
    seat, {AGREED: write_report, UNRESOLVED: write_report, HALTED: write_report}
)
