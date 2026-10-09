# Choosing and tuning a deliberation scope

Read this when a `council` or a `roundtable` is in the graph and you are
deciding which, how many rounds, and what to pay for verification.
`STDLIB.md` lists them; `ictus stdlib council` has every parameter.

**`council` polls, `roundtable` talks.** A council's voices run at once, so none
has heard the others when it speaks and a synthesis step has to write each round
up for the next one; it converges on a *record*. A roundtable's speakers take
turns, so the second has heard the first *this* round; only the speakers yet to
take their turn are still heard from the round before. They answer each other
directly, and the minutes are written once at the end rather than once a round.
The cost is wall-clock: a round takes the sum of its turns rather than the
longest of them. Reach for `council` when the standpoints are independent and
you want breadth; reach for `roundtable` when you want them to actually argue.
Order is part of the design — whoever speaks last has heard everyone.

`deliberate=` (on by default) hands every voice the others' positions and
concerns from the last round, verbatim and attributed, and asks it to answer
them by name. Off, a voice sees only the synthesis — one more agent's
compression of what everybody said — so it can restate its position but cannot
disagree with anyone in particular, and the council discovers and asserts round
after round without converging. Costs prompt tokens and no extra model calls.

`verify_each=` puts a checker behind every voice, all running at once, before
the round is written up. Off by default — it doubles the model calls in a round.
It earns that when one checker facing the finished report would have to triage:
thirty claims and a fixed budget buys about a lookup each, which reaches the
docstring and not the code under it. A per-voice checker has the same budget for
a quarter of the material. With it on, a voice reads *its own* checker's
corrections next round rather than the group's — a voice can act on "this claim
of yours did not hold" and can only nod at one aimed at the synthesis.

`verify=` adds a step that tries to **refute** each round's report against the
thing it describes, and gates agreement on the result. Without it the exit
condition is "all voices satisfied", which measures convergence between them and
nothing else — four models given the same wrong material agree sooner, not later.

`judge=` belongs to `converge`, not to a council or a roundtable. It picks who
decides: `"model"` (an agent emits `approved` + `notes`), `"human"` (an
approval gate), `"self"` (the attempt declares the verdict itself — the polling
shape, usually with `pause_between`).
