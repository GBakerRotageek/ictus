"""Finding a run that is happening, and getting permission to talk to it.

Conductor writes one record per run under ``~/.conductor/runs/`` and *moves* it
into ``terminal/`` when the run reaps — but only on a graceful exit. A killed
run, a crash or a closed laptop leaves its record behind, so the pid is checked
rather than the directory trusted. Nothing filters on *age*: a run parked at a
gate overnight is still a run, and a heuristic that aged it out would discard
exactly the one somebody still has to answer.

The path is not configurable — ``conductor.rundir.runs_dir`` builds it from
``Path.home()`` with no environment override — so neither is this.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LOOPBACK", "RUNS_DIR", "TOKEN_ENV", "LiveRun", "alive", "live_runs", "token_for"]

RUNS_DIR = Path.home() / ".conductor" / "runs"

#: Overrides the per-run minted token, and is what the engine checks first.
TOKEN_ENV = "CONDUCTOR_GATE_TOKEN"

#: How anything here reaches a dashboard: directly, never through a proxy.
#:
#: urllib's default opener honours ``$http_proxy`` for 127.0.0.1 unless
#: ``no_proxy`` lists that literal address, so on a machine with a proxy
#: configured every call went to the proxy — the run looked unreachable, and a
#: gate answer carried the dashboard's bearer token to it.
LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True, slots=True)
class LiveRun:
    """A run serving a dashboard, as its own record describes it."""

    run_id: str
    workflow: str
    port: int
    pid: int
    started_at: str
    event_log: Path | None = None
    """Where the engine is writing this run's JSONL, per the record.

    Read from the record rather than reconstructed: the filename carries a
    timestamp minted a moment after the one in ``started_at``, so building it
    from the parts gives a path that is right most of the time.
    """

    @property
    def dashboard(self) -> str:
        """The dashboard's base URL. Loopback — the engine binds 127.0.0.1."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def socket_url(self) -> str:
        """Where the live event stream is served."""
        return f"ws://127.0.0.1:{self.port}/ws"


def alive(pid: int) -> bool:
    """Whether a process is still there. Signal 0 checks without delivering one."""
    if pid <= 0:
        return True  # nothing recorded, so nothing to disprove
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # someone else's process, but a process
    return True


def live_runs(*, runs_dir: Path | None = None) -> list[LiveRun]:
    """Every run currently serving a dashboard, oldest first.

    A record with no port is a foreground run: it is executing, but there is no
    socket to attach to and no way to answer its gates from outside, so there is
    nothing a watcher could do with it.

    The pid is checked rather than trusted. The engine *moves* a record into
    ``terminal/`` when a run finishes, which is why nothing here filters on age —
    but that only happens on a graceful exit. A killed run, a crash, or a closed
    laptop leaves the record behind, and five of them accumulated here in an
    afternoon's testing. Without this a watcher would keep dialling ports that
    stopped answering, and ``ictus watch`` would report runs nobody is running.
    """
    found: list[LiveRun] = []
    for path in sorted((runs_dir or RUNS_DIR).glob("*.json")):
        record = _read(path)
        if record is None:
            continue
        port = record.get("port")
        run_id = record.get("run_id")
        if not isinstance(port, int) or not isinstance(run_id, str):
            continue
        log = record.get("event_log_path")
        pid = record.get("pid")
        if not alive(pid if isinstance(pid, int) else 0):
            continue
        found.append(
            LiveRun(
                run_id=run_id,
                workflow=str(record.get("workflow_name", "")),
                port=port,
                pid=pid if isinstance(pid, int) else 0,
                started_at=str(record.get("started_at", "")),
                event_log=Path(log) if isinstance(log, str) and log else None,
            )
        )
    return sorted(found, key=lambda run: run.started_at)


def token_for(port: int, *, runs_dir: Path | None = None) -> str | None:
    """The token a dashboard will accept, or ``None`` if none can be found.

    Needed for the socket handshake and for anything that changes the run.
    Reading state needs none, so a missing token is not fatal to a watcher that
    only listens — which is why this returns rather than raises.
    """
    override = os.environ.get(TOKEN_ENV)
    if override:
        return override
    path = (runs_dir or RUNS_DIR) / f"dashboard-{port}.token"
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _read(path: Path) -> dict[str, object] | None:
    """One run record, or ``None`` if it cannot be read.

    A record being written while this reads it is normal rather than
    exceptional, and a watcher that died on one would die on a busy machine.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None
