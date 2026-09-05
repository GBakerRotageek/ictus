"""``config.yaml`` — how a pipeline runs, as against what it is.

Three files, three questions, and keeping them apart is the point::

    pipeline.py     what the graph is          (composition)
    config.yaml     how it runs                (policy)
    input.md        what to run it on          (this run's values)

Policy is what you change without touching the graph: which provider answers the
model calls, what it may spend, whether a person confirms before anything starts.
Putting it in Python meant editing a composition to move a pipeline between
providers, and meant the answer was invisible unless you read the code.

``provider`` is required and has no default. Conductor's own default is
``copilot``, and a pipeline that silently took it is how four emitted workflows
once ran on the wrong provider for a whole session — the sort of thing that is
obvious in a config file and invisible in an omission.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ictus.errors import IctusError

if TYPE_CHECKING:
    from pathlib import Path

    from ictus.graph.pipeline import Pipeline

__all__ = ["CONFIG_FILE", "MINIMAL", "ConfigError", "PipelineConfig", "read_config"]

CONFIG_FILE = "config.yaml"

MINIMAL = "provider: claude-agent-sdk\n"

_BUDGET_MODES = frozenset({"audit", "enforce"})

_KNOWN = frozenset(
    {
        "provider",
        "default_model",
        "start_gate",
        "budget_usd",
        "budget_mode",
        "max_iterations",
        "dashboard",
    }
)


class ConfigError(IctusError):
    """``config.yaml`` is missing, malformed, or says something impossible."""


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """A pipeline's run policy."""

    provider: str
    default_model: str | None = None
    start_gate: bool = True
    """Whether a person confirms before anything runs. See ``ictus.gate``."""

    budget_usd: float | None = None
    budget_mode: str = "audit"
    max_iterations: int | None = None
    dashboard: bool = True
    """Whether ``ictus run`` serves the dashboard. A gated run needs one."""

    def apply(self, pipeline: Pipeline, *, where: str) -> None:
        """Put this policy on ``pipeline``.

        A value set in both places and set *differently* is refused rather than
        silently resolved: two sources of truth that disagree is exactly the
        state where whichever one you read is the wrong one.
        """
        for field, value in (
            ("provider", self.provider),
            ("default_model", self.default_model),
            ("budget_usd", self.budget_usd),
            ("max_iterations", self.max_iterations),
        ):
            if value is None:
                continue
            current = getattr(pipeline, field)
            if current is not None and current != value:
                raise ConfigError(
                    f"{where}: {field} is {value!r} here but {current!r} in the pipeline. "
                    "Policy belongs in config.yaml; take it out of the composition."
                )
            setattr(pipeline, field, value)
        pipeline.budget_mode = self.budget_mode  # type: ignore[assignment]


def read_config(path: Path) -> PipelineConfig:
    """Read a pipeline folder's ``config.yaml``."""
    where = str(path)
    if not path.is_file():
        raise ConfigError(
            f"{path} does not exist. Every pipeline folder needs one; the minimal "
            f"version is a single line:\n\n    {MINIMAL.strip()}\n\n"
            "Or run `ictus init` on the folder."
        )
    yaml = YAML(typ="safe")
    try:
        loaded = yaml.load(io.StringIO(path.read_text(encoding="utf-8")))
    except YAMLError as exc:
        raise ConfigError(f"{where}: not valid YAML: {exc}") from exc
    if loaded is None:
        raise ConfigError(f"{where}: is empty; it needs at least `{MINIMAL.strip()}`")
    if not isinstance(loaded, dict):
        raise ConfigError(f"{where}: must be a mapping, got {type(loaded).__name__}")

    unknown = sorted(set(loaded) - _KNOWN)
    if unknown:
        raise ConfigError(
            f"{where}: {unknown} are not settings; known are {sorted(_KNOWN)}. "
            "A key that does nothing quietly is worse than one that fails."
        )
    provider = loaded.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ConfigError(
            f"{where}: needs a `provider`. There is no default on purpose — Conductor's "
            "own is copilot, and inheriting it silently is how a pipeline ends up running "
            "somewhere nobody chose."
        )
    mode = loaded.get("budget_mode", "audit")
    if mode not in _BUDGET_MODES:
        raise ConfigError(f"{where}: budget_mode must be one of {sorted(_BUDGET_MODES)}")

    return PipelineConfig(
        provider=provider,
        default_model=_optional_str(loaded, "default_model", where),
        start_gate=_flag(loaded, "start_gate", where, default=True),
        budget_usd=_optional_number(loaded, "budget_usd", where),
        budget_mode=mode,
        max_iterations=_optional_int(loaded, "max_iterations", where),
        dashboard=_flag(loaded, "dashboard", where, default=True),
    )


def _flag(data: dict[str, object], key: str, where: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: {key} must be true or false, got {value!r}")
    return value


def _optional_str(data: dict[str, object], key: str, where: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be text, got {type(value).__name__}")
    return value


def _optional_int(data: dict[str, object], key: str, where: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{where}: {key} must be a whole number, got {value!r}")
    return value


def _optional_number(data: dict[str, object], key: str, where: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{where}: {key} must be a number, got {value!r}")
    return float(value)
