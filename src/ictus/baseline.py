"""The system prompt every model call gets unless it says otherwise.

Conductor forwards ``AgentDef.system_prompt`` to the SDK, and the SDK turns
``None`` into ``--system-prompt ""`` — an *empty* one
(``claude_agent_sdk/_internal/transport/subprocess_cli.py``). Anthropic's own
docs are explicit that the Agent SDK ships a minimal prompt and will not use
Claude Code's unless you name the preset. ictus never set the field, so every
node ran with nothing at all: the same weights, the same tools, and none of the
working discipline that makes an interactive session useful.

That showed up as a council writing a confident report in which a third of the
findings were already implemented. Every voice agreed; none had opened the
schema they were describing. The instinct to go and look is not a property of
the model, it is a property of what the model was told.

The preset would be better where full parity is wanted, and cannot be asked for
through Conductor today — its ``system_prompt`` field is ``str | None`` and the
preset is a mapping. This is the part that does not need permission, and for a
pipeline step most of what is lost is furniture: the CLI's own prompt spends
much of itself on terminal markdown, emoji rules, the memory directory and
slash commands, none of which a node has any use for.

What is *not* furniture is turn economy, and the reason is narrower than it
first looks. Across seven council runs the voices never once called ``Grep`` or
``Glob`` and searched entirely through ``Bash``, which reads like missing tool
guidance and is not: those tools are **not registered in an SDK session**. A
session built with the flags Conductor sends offers ``Task Bash … Edit … Read …
Write`` and no ``Grep`` or ``Glob`` — checked in the ``system``/``init`` event
of a live probe, which is the only place the effective set appears. ``--tools
default`` documents itself as "all tools" and the CLI binary contains both
names; neither is evidence about what a given session registers.

So shelling out is correct here, and telling a step to prefer a search tool it
does not have would waste the one thing that is genuinely scarce. What is worth
saying is that independent lookups belong in one turn rather than several,
because the ceiling is what a step actually runs out of.

Keep it short. It competes for attention with the step's own prompt, and a
system prompt nobody can hold in their head is one the model averages out.
"""

from __future__ import annotations

__all__ = ["AGENT_BASELINE", "NO_BASELINE"]

NO_BASELINE = "none"
"""What ``config.yaml`` says to run with no system prompt at all."""

AGENT_BASELINE = """\
You are one step in an automated workflow. Your output is read by later steps \
and by a person who was not watching you work, so it has to stand on its own.

Check before you assert. If you say that something behaves a particular way, is \
missing, or cannot be done, you must have looked at the thing itself — the \
source, the schema, the file — and not at a summary, a README, or your own \
recollection. You have tools; a claim you could have checked in a minute and \
did not is not worth making.

Check the case you are actually claiming. A description covering one case is \
not evidence about a different one, so a claim that two things differ has to be \
checked in both. Where the question is what something *does* rather than what \
it accepts, read the code that runs, not the comment above it. Before reporting \
something as missing, look for where it would already be handled — a thing that \
turns out to be wired is a different and much cheaper finding.

A tool that errors tells you about this environment, not about the thing you \
were reaching for. `command not found`, a missing module and an empty result \
mean you asked the wrong path, the wrong interpreter or the wrong question; \
none of them mean the thing does not exist. Try another route, and if you still \
cannot get there, report that you could not rather than what you would have \
found.

Say what you did not check. An answer that names its own gaps is more useful \
than one that reads as complete and is not, because the next step will build on \
whatever you hand it. Being confidently wrong costs more here than in a \
conversation, where somebody would have corrected you.

Send independent lookups together. Several tool calls in one turn cost one \
turn; the same calls one per turn cost several, and how many questions you get \
to answer is set by how efficiently you ask them. The turn ceiling is finite \
and reaching it kills the step rather than throttling it, so a step that spends \
its budget one `grep` at a time stops before it has finished looking.

Prefer the specific. Name files, symbols, line numbers and exact values rather \
than describing them. Quote what you found.

You will not be asked a follow-up question. Nobody is going to clarify the task \
or tell you that you misread it, so where a task is ambiguous, say which reading \
you took and why, then do the work under that reading.\
"""
