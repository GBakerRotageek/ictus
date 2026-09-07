"""What each step actually did, read back from the engine's event log.

A step's output tells you what it concluded. It does not tell you whether it
looked at anything before concluding, and those are very different runs with
identical-looking results. A council once produced a confident report in which a
third of the findings were already implemented; the log said why in one line —
the survey step made twenty-five tool calls and every voice made one, which was
the call that emitted its answer.

That number is the cheapest quality signal available and it costs nothing to
collect: Conductor already writes ``agent_tool_start`` with the tool name and
its arguments. Nobody was reading it.

Conductor's own file naming and event vocabulary, so it lives here rather than
in the graph layer.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = ["StepTrace", "Trace", "find_logs", "read_trace"]

# Where `conductor run` writes them, per its own CLI output.
LOG_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "conductor"

# Emitting the answer is not looking something up. Counting it as activity would
# make every step look diligent, which is the one thing this must not do.
REPORTING_TOOLS = frozenset({"StructuredOutput"})

#: The phase of ``agent_turn_start`` that counts as one turn against the cap.
AWAITING_MODEL = "awaiting_model"

#: What Conductor allows a step before it raises, absent ``max_turns``.
DEFAULT_MAX_TURNS = 50


@dataclass(slots=True)
class StepTrace:
    """One step's activity in a run."""

    name: str
    tools: Counter[str] = field(default_factory=Counter)
    reads: list[str] = field(default_factory=list)
    turns: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    elapsed: float = 0.0
    ceiling: int = DEFAULT_MAX_TURNS
    """This step's own turn limit, not the engine's default.

    The log does not carry it — ``workflow_started`` lists each agent's name,
    type, model and provider and stops there — so it comes from the graph when
    a caller has one. Left at the default, a step that declared ``max_turns=200``
    and used 50 reads as having died at its ceiling, which is a false alarm
    about the one failure this module exists to make visible.
    """

    @property
    def investigated(self) -> int:
        """Tool calls that went and found something out."""
        return sum(n for tool, n in self.tools.items() if tool not in REPORTING_TOOLS)

    @property
    def at_the_cap(self) -> bool:
        """Whether this step was stopped for running out of turns.

        Not a slow step: the provider raises rather than returning what it had,
        and that error is not one a scope can turn into an outcome, so it takes
        the whole run down with it.
        """
        return self.turns >= self.ceiling

    @property
    def looked(self) -> bool:
        """Whether this step consulted anything before answering."""
        return self.investigated > 0


@dataclass(slots=True)
class Trace:
    """A whole run, step by step."""

    path: Path
    workflow: str
    steps: dict[str, StepTrace] = field(default_factory=dict)

    @property
    def capped(self) -> list[StepTrace]:
        """Steps that hit the turn ceiling, which is fatal rather than throttling."""
        return [s for s in self.steps.values() if s.at_the_cap]

    @property
    def incurious(self) -> list[StepTrace]:
        """Model steps that answered without consulting anything.

        Not automatically wrong — a step whose whole input is in its prompt has
        nothing to look up. It is wrong when the step was asked to assess
        something it was only shown a summary of.
        """
        return [s for s in self.steps.values() if not s.looked and s.turns]


def find_logs(workflow: str | None = None) -> list[Path]:
    """Event logs, newest first, optionally for one workflow."""
    if not LOG_DIR.is_dir():
        return []
    stem = f"conductor-{workflow}-*" if workflow else "*"
    found = {*LOG_DIR.glob(f"{stem}.events.jsonl"), *LOG_DIR.glob(f"{stem}.events.json")}
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def read_trace(path: Path, *, ceilings: Mapping[str, int] | None = None) -> Trace:
    """Read one event log into per-step activity.

    ``ceilings`` maps a step's name to the ``max_turns`` it declared. Without it
    every step is measured against the engine's default of fifty, which is right
    for a step that set nothing and wrong — loudly, in red — for one that raised
    its own.
    """
    trace = Trace(path=path, workflow=_workflow_name(path))
    limits = dict(ceilings or {})
    for event in _events(path):
        kind = event.get("type") or event.get("event")
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        name = data.get("agent_name")
        if not isinstance(name, str):
            continue
        step = trace.steps.setdefault(
            name, StepTrace(name=name, ceiling=limits.get(name, DEFAULT_MAX_TURNS))
        )
        if kind == "agent_tool_start":
            tool = data.get("tool_name")
            step.tools[str(tool)] += 1
            args = data.get("arguments")
            if isinstance(args, dict):
                target = args.get("file_path") or args.get("path") or args.get("pattern")
                if isinstance(target, str):
                    step.reads.append(target)
        elif kind == "agent_turn_start" and data.get("turn") == AWAITING_MODEL:
            # Two shapes share this event: the phase name below, and a numeric
            # index that advances about twice per round. Only the phase count
            # matches what the engine enforces — it reported a step dying
            # "after 51 turns" where the numeric index had reached 102 — and a
            # column meant to show a step nearing the cap it dies at has to
            # count in the same units as the cap.
            step.turns += 1
        elif kind in ("agent_completed", "parallel_agent_completed"):
            step.cost_usd += _number(data.get("cost_usd"))
            step.tokens += int(_number(data.get("tokens")))
            step.elapsed += _number(data.get("elapsed"))
    return trace


# conductor-<workflow>-<YYYYMMDD>-<HHMMSS>-<id>.events.jsonl, and a workflow id
# may itself contain hyphens — so anchor on the timestamp rather than counting.
_LOG_NAME = re.compile(r"\Aconductor-(?P<workflow>.+)-\d{8}-\d{6}-[0-9a-f]+(\.events)?\Z")


def _workflow_name(path: Path) -> str:
    match = _LOG_NAME.match(path.stem)
    return match.group("workflow") if match else path.stem


def _events(path: Path) -> Iterator[dict[str, object]]:
    """Every well-formed event, in either shape Conductor produces.

    A run writes one JSON object per line as it goes; the dashboard's download
    button hands you the same events as a single JSON array. Reading only the
    first meant a downloaded log parsed to nothing and reported an empty run,
    which looks exactly like a run that did nothing.
    """
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, list):
            for item in loaded:
                if isinstance(item, dict):
                    yield item
            return
    # One object per line. A truncated last line is normal on a live run.
    for line in text.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0
