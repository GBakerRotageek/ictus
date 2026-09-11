# `tests/` — the gate

`make soundcheck` must pass on a fresh clone. While the corpus it read lived
only in gitignored `demo_work/`, a clone ran 36 fewer tests than the machine
they were written on, failed three, and every command in the root `AGENTS.md`
errored on a missing directory.

## Two pipeline roots, and only one of them ships

| Root | Committed | Role |
| --- | --- | --- |
| `tests/fixtures/pipelines/` | yes | the gate's own corpus |
| `demo_work/pipelines/` | no — gitignored | local work, checked as a bonus when present |

`conftest.pipeline_roots()` returns both and asserts the committed one exists.
Everything that globs for pipelines goes through it and **asserts the result is
non-empty** — a parametrized test that silently collects zero cases is the
failure this arrangement exists to prevent.

`conftest.REPO_ROOT` is the one definition of the repo root; import it rather
than recomputing `parent.parent`.

`conductor` missing from PATH is a hard failure rather than a skip: a suite that
cannot check its own output has verified nothing.
