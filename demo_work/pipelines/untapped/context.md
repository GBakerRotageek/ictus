# What this brainstorm is

Not a review of the library. A review asks what is wrong or missing; this asks a
narrower question with a cheaper answer: **of what the engine already does, what
have we never reached for?**

The two produce different work. A need has to be designed. An unused capability
only has to be wired, and wiring is the cheapest progress available — three
rounds of the sister council turned up engine fields that nobody had connected,
each found by reading the engine rather than by inventing a feature.

You are handed both halves of a ledger: what the engine offers, and what this
library already reaches. Your job is the difference between them.

## How this runs

You read on your own first, then you talk. The reading is private and
simultaneous; the conversation is in turns, and by the time it reaches you some
of the others have already spoken **this** round — their words are in front of
you, not a summary of them.

So argue. Answer people by name, say what would change your mind, and say
plainly when somebody has changed yours. Where a claim of theirs looks wrong,
go and check it while you have the turn: you all have the same tools and the
same installed package, and a claim that survives four people who could look it
up is worth more than one nobody tested. Where you agree with something already
said properly, say so once rather than restating it — four people making the
same point is not four findings.

The table finishes when everyone would be content to stop, which is not the
same as everyone agreeing. A disagreement that has been aired, checked and
recorded is a finished piece of work.

## The engine, precisely

`conductor-cli`, from **github.com/microsoft/conductor**, a CLI for defining and
running multi-agent workflows on the GitHub Copilot SDK. It is installed on this
machine and readable; the repository's own `AGENTS.md` says how to find it.

**Several unrelated products are called Conductor.** Netflix ships a well-known
workflow engine by that name; there are others. A capability you find on the web
belongs to this project only if you can also find it in the installed package or
in that GitHub repository. Anything else is a different tool wearing the same
word, and reporting it would send someone to build against something that does
not exist here.

Note the installed version. The project ships releases; what has landed since,
or is open as an issue, is a legitimate finding as long as you say which it is —
"available now" and "announced" and "someone asked for it" are three different
things and only the first is wirable today.

## Take as settled

- **A wrapper that exposed everything underneath it would be a worse tool.**
  Some of the engine's surface is deliberately not reached: ambient settings,
  loose MCP config, anything that makes the same committed pipeline behave
  differently on another machine. Finding one of those is useful — say it is
  deliberate, and say whether the reasoning still holds.
- **Composition-time checking is the point.** A capability worth taking up is
  one that lets a mistake be caught while a pipeline is written rather than
  while it runs.
- **The provider matters.** This project runs one provider, and the engine's
  schema accepts fields that provider ignores. A capability the chosen provider
  drops is a roadmap note, not a piece of work — say which you have found.

## Three kinds of finding, and say which

- **Unused and wirable** — the engine does it, the provider honours it, nothing
  here reaches it. The most valuable thing you can report.
- **Unused and blocked** — real, but the provider ignores it or it needs a
  change upstream. Worth knowing, not worth starting.
- **Unused on purpose** — reached for deliberately and declined. Worth
  re-examining only if the reason has expired.

Say out loud what you could not confirm, with what you tried. For this
brainstorm that matters more than usual: the web is half your material and the
name is ambiguous, so "I found this but could not tie it to the installed
package" is a genuinely useful thing to say and a bad thing to quietly upgrade
into a finding. Somebody else at the table may be able to make the lookup you
could not — that is most of the point of saying it rather than filing it.
