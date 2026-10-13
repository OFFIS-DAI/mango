"""Scenario-file parameters: long form (bounds, choices, help, type) and pattern specs."""

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from pydantic import BaseModel  # noqa: E402

from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioError,
    ScenarioParams,
    load_scenario,
)
from mango.scenario.configfiles import deep_merge  # noqa: E402
from mango.scenario.params import kind_switch_rule  # noqa: E402

from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

NODES = """
from mango import Agent


def node(e):
    return Agent()
"""

OVERLAY = """
name: overlay
time: {horizon: 4h, step: 1h}
parameters:
  net:
    n: 8
    topology: {default: ring, type: pattern, help: overlay topology}
  epsilon: {default: 0.25, min: 0, max: 1, help: confidence bound}
  interaction: {default: pairwise, choices: [pairwise, messages]}
groups:
  node: {count: net.n, factory: "nodes:node"}
links:
  - {name: overlay, from: node, pattern: {param: net.topology}}
"""


@pytest.fixture(name="overlay")
def overlay_fixture(isolated, monkeypatch):
    monkeypatch.syspath_prepend(str(isolated))
    write(isolated, "nodes.py", NODES)
    write(isolated, "overlay.yaml", OVERLAY)
    write(
        isolated, "presets/sw.yaml", "net: {topology: {small_world: {k: 4, p: 0.1}}}\n"
    )
    return isolated


def test_a_pattern_parameter_takes_names_and_specs(capsys, overlay):
    code, out, err = cli(capsys, "plan", "overlay.yaml")
    assert code == 0, err and "(ring); 8 edges" in out
    code, out, err = cli(
        capsys,
        "plan",
        "overlay.yaml",
        "-s",
        "net.topology={small_world: {k: 4, p: 0.1}}",
    )
    assert code == 0, err
    assert "small_world(k=4, p=0.1)" in out


def test_one_key_specs_replace_each_other_across_layers(capsys, overlay):
    code, out, err = cli(
        capsys,
        "plan",
        "overlay.yaml",
        "-p",
        "presets/sw.yaml",
        "-s",
        "net.topology={random: {p: 0.2}}",
    )
    assert code == 0, err
    assert "(random(p=0.2))" in out
    code, out, err = cli(
        capsys,
        "plan",
        "overlay.yaml",
        "-p",
        "presets/sw.yaml",
        "-s",
        "net.topology=ring",
    )
    assert code == 0, err
    assert "(ring); 8 edges" in out


def test_choices_and_bounds_are_checked(capsys, overlay):
    code, _, err = cli(capsys, "validate", "overlay.yaml", "-s", "interaction=pairwse")
    assert code != 0
    assert "interaction: Input should be 'pairwise' or 'messages'" in err
    code, _, err = cli(capsys, "validate", "overlay.yaml", "-s", "epsilon=1.7")
    assert code != 0
    assert "less than or equal to 1" in err and "epsilon: confidence bound" in err


def test_help_texts_reach_the_model(overlay):
    scenario = load_scenario("overlay.yaml")
    fields = scenario.params_model.model_fields
    assert fields["epsilon"].description == "confidence bound"
    net = fields["net"].annotation.model_fields
    assert net["topology"].description == "overlay topology"
    defaults = scenario.resolve_params()
    assert defaults.net.topology == "ring" and defaults.interaction == "pairwise"


def load_scenario_text(text):
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.mkdtemp())
    path = folder / "s.yaml"
    path.write_text(text, encoding="utf-8")
    return load_scenario(path)


@pytest.mark.parametrize(
    "declaration, message",
    [
        ("{default: 0.25, mni: 0}", "'mni' is not a key of a parameter declaration"),
        ("{default: 1.5, max: 1}", "does not satisfy its own declaration"),
        ("{default: fast, choices: [slow, medium]}", "does not satisfy"),
        ("{default: x, min: 0}", "min/max/gt/lt bound numbers"),
        ("{default: ring, type: patern}", "type 'patern' is unknown. Did you mean"),
        ("{default: {small_world: {k: 4}}}", "type: pattern"),
        ("{default: 1, choices: []}", "choices must be a non-empty list"),
    ],
)
def test_long_form_errors(declaration, message):
    text = (
        f"name: s\ntime: {{horizon: 1h, step: 1h}}\nparameters:\n  x: {declaration}\n"
    )
    with pytest.raises(ScenarioError, match=message):
        load_scenario_text(text)


def test_a_mapping_default_for_a_str_parameter_gets_a_pattern_hint(capsys, isolated):
    write(isolated, "nodes.py", NODES)
    write(
        isolated,
        "plain.yaml",
        OVERLAY.replace(
            "topology: {default: ring, type: pattern, help: overlay topology}",
            "topology: ring",
        ),
    )
    code, _, err = cli(
        capsys, "plan", "plain.yaml", "-s", "net.topology={small_world: {k: 2, p: 0.1}}"
    )
    assert code != 0
    assert "net.topology is a str parameter" in err
    assert "{default: ring, type: pattern}" in err
    assert "quote the value" not in err


class Net(BaseModel):
    n: int = 6
    topology: str | dict = "ring"
    options: dict = {}


class NetParams(ScenarioParams):
    net: Net = Net()


def test_python_str_or_dict_parameters_replace_one_key_specs():
    scenario = Scenario("net", NetParams)
    scenario.group("node", "net.n", lambda e: __import__("mango").Agent())
    scenario.link("overlay", "node", pattern="net.topology")
    params = scenario.resolve_params(
        {"net": {"topology": {"small_world": {"k": 2, "p": 0.1}}}},
        overrides=["net.topology={random: {p: 0.2}}"],
    )
    assert params.net.topology == {"random": {"p": 0.2}}


def test_plain_dict_parameters_still_merge():
    switch = kind_switch_rule(NetParams)
    merged = deep_merge(
        {"net": {"options": {"a": {"x": 1}}}},
        {"net": {"options": {"b": {"y": 2}}}},
        kind_switch=switch,
    )
    assert merged["net"]["options"] == {"a": {"x": 1}, "b": {"y": 2}}
    merged = deep_merge(
        {"net": {"topology": {"a": {"x": 1}}}},
        {"net": {"topology": {"b": {"y": 2}}}},
        kind_switch=switch,
    )
    assert merged["net"]["topology"] == {"b": {"y": 2}}
    # a nested group of a params model always merges
    merged = deep_merge({"net": {"n": 3}}, {"net": {"options": {}}}, kind_switch=switch)
    assert merged["net"] == {"n": 3, "options": {}}


def test_study_levels_over_a_mapping_default(overlay):
    from mango.scenario import Study

    study = Study(
        "overlay.yaml",
        base="presets/sw.yaml",
        levers={
            "net.topology": [
                {"random": {"p": 0.2}},
                {"small_world": {"k": 2, "p": 0.5}},
            ]
        },
        out="results",
    )
    topologies = [spec["params"]["net"]["topology"] for spec in study.plan()]
    assert {"random": {"p": 0.2}} in topologies
    assert {"small_world": {"k": 2, "p": 0.5}} in topologies


KIND_FIELD = """
name: social
time: {horizon: 2, step: 1}
parameters:
  net:
    kind: {default: small_world, choices: [small_world, random], help: network model}
    k: 6
  opinion: {normal: [0, 1]}
  shock: {kind: normal, mean: 0, std: 1}
"""


def test_a_long_form_field_named_kind_in_a_group(capsys, isolated):
    # a group with a field named 'kind' is not a distribution spec
    write(isolated, "social.yaml", KIND_FIELD)
    scenario = load_scenario("social.yaml")
    params = scenario.resolve_params(None)
    assert params.net.kind == "small_world" and params.net.k == 6
    assert type(params.opinion).__name__ == "Normal"
    assert type(params.shock).__name__ == "Normal"
    assert scenario.resolve_params(None, overrides=["net.kind=random"]).net.kind == (
        "random"
    )
    code, out, err = cli(capsys, "validate", "social.yaml", "-s", "net.kind=rand")
    assert code != 0
    assert "'small_world' or 'random'" in out + err


def test_long_form_keys_without_default_warn(isolated):
    write(
        isolated,
        "nodefault.yaml",
        "name: x\ntime: {horizon: 2, step: 1}\n"
        "parameters:\n  mu: {help: step size, min: 0}\n  range: {min: 0, max: 5}\n",
    )
    with pytest.warns(UserWarning, match="has no 'default'") as caught:
        scenario = load_scenario("nodefault.yaml")
    assert len(caught) == 1 and "parameters.mu" in str(caught[0].message)
    # a plain group with fields named min/max stays a group, without a warning
    assert scenario.resolve_params(None).range.max == 5
