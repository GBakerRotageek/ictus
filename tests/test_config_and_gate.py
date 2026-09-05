"""Run policy in `config.yaml`, and the start gate it switches on.

Conductor's dashboard is a view onto a live engine, not a launcher — its API has
stop, kill and resume but no start. So "load it and let me press go" has to be
built out of a gate, and a gate every pipeline gets by default is the only way
that is true of pipelines nobody remembered to add one to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ictus import END, AgentNode, InputPort, OutputPort, Pipeline, PortType, tpl
from ictus.config import MINIMAL, ConfigError, read_config
from ictus.errors import CompositionError
from ictus.gate import CANCELLED_ID, GATE_ID, add_start_gate
from ictus.interfaces.conductor import conductor
from ictus.lint import lint_pipeline

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ictus.graph.values import YamlDict

STR = PortType.STRING


def _pipeline() -> Pipeline:
    p = Pipeline(pipeline_id="demo", description="Does a thing")
    target = p.declare_input("target", STR, description="What to act on")
    charge = p.declare_input("charge", STR, required=False, prose=True)
    work = p.add(
        AgentNode(
            node_id="work",
            inputs=(InputPort("target", STR), InputPort("charge", STR, optional=True)),
            prompt=tpl("do ", target.ref()),
            declared_outputs=(OutputPort("out", STR),),
        )
    )
    p.set_entry(work)
    p.connect_input(target, work, "target")
    p.connect_input(charge, work, "charge")
    p.route(work, END)
    return p


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


def _agent(pipeline: Pipeline, name: str) -> YamlDict:
    agents = conductor.document(pipeline)["agents"]
    assert isinstance(agents, list)
    for candidate in agents:
        if isinstance(candidate, dict) and candidate.get("name") == name:
            return candidate
    raise AssertionError(f"no agent named {name!r}")


# --- config ----------------------------------------------------------------


def test_the_minimal_config_is_one_line(tmp_path: Path) -> None:
    settings = read_config(_config(tmp_path, MINIMAL))
    assert settings.provider == "claude-agent-sdk"
    assert settings.start_gate is True


def test_a_missing_config_says_what_to_write(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="provider: claude-agent-sdk"):
        read_config(tmp_path / "config.yaml")


def test_provider_has_no_default(tmp_path: Path) -> None:
    """Conductor's own default is copilot; inheriting it silently already bit us."""
    with pytest.raises(ConfigError, match="needs a `provider`"):
        read_config(_config(tmp_path, "start_gate: false\n"))


def test_a_key_that_does_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"\['startgate'\] are not settings"):
        read_config(_config(tmp_path, "provider: claude\nstartgate: false\n"))


def test_policy_reaches_the_pipeline(tmp_path: Path) -> None:
    settings = read_config(
        _config(tmp_path, "provider: claude\ndefault_model: opus\nbudget_usd: 5\n")
    )
    pipeline = _pipeline()
    settings.apply(pipeline, where="config.yaml")
    assert pipeline.provider == "claude"
    assert pipeline.default_model == "opus"
    assert pipeline.budget_usd == 5.0


def test_two_sources_of_truth_that_disagree_are_refused(tmp_path: Path) -> None:
    """Whichever one you read would be the wrong one."""
    settings = read_config(_config(tmp_path, "provider: claude\n"))
    pipeline = _pipeline()
    pipeline.provider = "copilot"
    with pytest.raises(ConfigError, match="take it out of the composition"):
        settings.apply(pipeline, where="config.yaml")


def test_a_flag_must_be_a_flag(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be true or false"):
        read_config(_config(tmp_path, "provider: claude\nstart_gate: yes please\n"))


def test_the_backend_declares_which_providers_exist() -> None:
    """A closed set in Conductor's schema, so a typo is knowable at composition."""
    providers = conductor.capabilities().providers
    assert "claude-agent-sdk" in providers
    assert "claud" not in providers


# --- the start gate --------------------------------------------------------


def test_the_gate_becomes_the_entry_point() -> None:
    pipeline = add_start_gate(_pipeline())
    assert pipeline.entry().node_id == GATE_ID
    doc = conductor.document(pipeline)
    workflow = doc["workflow"]
    assert isinstance(workflow, dict)
    assert workflow["entry_point"] == GATE_ID


def test_it_shows_the_actual_input_values_not_that_some_exist() -> None:
    prompt = _agent(add_start_gate(_pipeline()), GATE_ID)["prompt"]
    assert isinstance(prompt, str)
    assert "{{ workflow.input.target }}" in prompt
    assert "{{ workflow.input.charge }}" in prompt


def test_an_absent_optional_input_renders_nothing() -> None:
    """The engine binds it to None, so a definedness guard would print that word."""
    prompt = _agent(add_start_gate(_pipeline()), GATE_ID)["prompt"]
    assert isinstance(prompt, str)
    assert "{% if workflow.input.charge %}" in prompt
    assert "{% if workflow.input.target %}" not in prompt


def test_starting_goes_to_what_used_to_be_the_entry() -> None:
    options = _agent(add_start_gate(_pipeline()), GATE_ID)["options"]
    assert isinstance(options, list)
    assert [(o["value"], o["route"]) for o in options if isinstance(o, dict)] == [
        ("start", "work"),
        ("cancel", CANCELLED_ID),
    ]


def test_start_is_first_because_skip_gates_takes_the_first_option() -> None:
    """An unattended run has made this decision by being unattended."""
    options = _agent(add_start_gate(_pipeline()), GATE_ID)["options"]
    assert isinstance(options, list)
    first = options[0]
    assert isinstance(first, dict)
    assert first["value"] == "start"


def test_declining_is_a_success_not_a_failure() -> None:
    """A person looking at it and saying no is not an error for a caller to handle."""
    stopped = _agent(add_start_gate(_pipeline()), CANCELLED_ID)
    assert stopped["status"] == "success"
    assert stopped["output_template"] == {"started": "false"}


def test_a_name_collision_is_refused_rather_than_silently_renamed() -> None:
    p = _pipeline()
    p.add(AgentNode(node_id=GATE_ID, prompt="x"))
    with pytest.raises(CompositionError, match="already has a node called"):
        add_start_gate(p)


def test_a_gated_pipeline_is_lint_clean_and_loads(
    validates: Callable[[Pipeline], None],
) -> None:
    pipeline = add_start_gate(_pipeline())
    assert lint_pipeline(pipeline, backend=conductor) == []
    validates(pipeline)


def test_it_costs_one_iteration_and_no_provider_call() -> None:
    plain = conductor.document(_pipeline())["workflow"]
    gated = conductor.document(add_start_gate(_pipeline()))["workflow"]
    assert isinstance(plain, dict) and isinstance(gated, dict)
    before, after = plain["limits"], gated["limits"]
    assert isinstance(before, dict) and isinstance(after, dict)
    # The gate and its terminal: two steps, neither of which calls a provider.
    assert after["max_iterations"] == before["max_iterations"] + 2  # type: ignore[operator]
