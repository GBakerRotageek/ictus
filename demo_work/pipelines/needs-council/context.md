# What this review is

A review of a *change* and a review of a *codebase's needs* are different jobs.
A change arrives with an intent you can judge it against; a codebase does not.
So this council is not asked whether the code is good — it is asked what the
library still cannot do, and which of those gaps are worth closing.

The material is a repository. It does not fit in a prompt, and you have tools:
read what your own focus needs rather than forming a view from the summary you
were handed.

## What the project itself says

You are running inside the repository under review, so its own account of
itself — `AGENTS.md` at the root, and anything else the workspace carries — is
already in front of you. Read it before deciding what is missing. It says where
the ground truth for the execution engine lives and how to reach it; that is the
difference between a finding and a guess.

Do not restate it back as a finding. "The project is a typed composition layer"
is not a gap.

## Take as settled

- **Composition-time checking is the point.** A proposal that moves a failure
  from run time to authoring time is aligned; one that merely relocates a
  failure is not an improvement.
- **The engine boundary is deliberate.** ictus does not reimplement what the
  executor does. "ictus should execute this itself" is out of scope.
- **Determinism over ambience.** A pipeline is committed and re-runnable. A
  proposal that makes the same YAML behave differently on a different machine
  needs to say why that is worth it.

## Separate the two kinds of gap

A capability can be missing from **the library** or missing from **the engine**,
and they cost completely different amounts to close. Before calling something
missing, establish which one it is:

- present in the engine, absent from `src/ictus` — a wiring gap, cheap, and the
  most useful thing this council can find;
- absent from both — a real feature request, and worth saying so explicitly;
- present in both — not a finding.

A claim you could not settle goes in `unchecked`, with what you tried. It is not
a lesser answer than a finding; it is the one thing the next round cannot
reconstruct for itself.
