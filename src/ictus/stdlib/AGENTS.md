# `stdlib/` — what the root instructions do not already say

## Adding a constructor is two files, not one

`STDLIB.md` is the catalogue and `tests/test_docs.py` checks it against
`__all__`. Exported without a row fails; a row naming a parameter the function
does not have fails; deleted but left in the table fails. So the row goes in the
same change — the test exists because the README once listed two constructors
that had been deleted and a class that never existed under that name.

## The outcome constants are exported; use them, not the string

`converge` → `CONVERGED` / `EXHAUSTED`. `council` and `roundtable` → `AGREED` /
`UNRESOLVED` / `HALTED`. Routing on a literal makes a rename a silent unrouted
exit instead of a type error.

**Never `fail` inside a stage or scope.** A failed terminal raises past every
route its caller declared, which is the one thing a scope exists to prevent.
