import datetime as dt
import importlib
import json
import re
import sys
import textwrap
import types
from typing import Any, Literal

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

import yaml  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from mango import SeedTree  # noqa: E402
from mango.scenario import (  # noqa: E402
    CommConfig,
    Constant,
    Dist,
    Normal,
    ParamsError,
    ScenarioError,
    ScenarioParams,
    Uniform,
    build_communication,
    check_path,
    deep_merge,
    dump_params,
    get_path,
    load_params,
    params_schema,
    params_to_yaml,
    parse_overrides,
    read_config_file,
    set_path,
)
from mango.simulation import (  # noqa: E402
    DelayProviderCommunicationSimulation,
    MessagePackage,
    SimpleCommunicationSimulation,
)

# ----------------------------------------------------------------- models


class EVParams(BaseModel, extra="forbid"):
    n: int = 10
    capacity_kwh: float = 60.0
    max_power_kw: float = 11.0
    soc: Uniform = Uniform(0.2, 0.8)
    arrival_h: Normal = Normal(16.0, 1.5)


class Greedy(BaseModel, extra="forbid"):
    kind: Literal["greedy"] = "greedy"
    threshold_kw: float = 0.3


class Market(BaseModel, extra="forbid"):
    kind: Literal["market"] = "market"
    rounds: int = 5


class Profile(BaseModel, extra="forbid"):
    column: str = "h0"
    scale: float = 1.0


class Params(ScenarioParams):
    ev: EVParams = EVParams()
    algorithm: Greedy | Market = Field(default_factory=Greedy, discriminator="kind")
    profiles: dict[str, Profile] = {}
    battery: EVParams | None = None
    tags: list[str] = []
    noise: Dist = Constant(0.0)
    label: Literal["a", "b"] = "a"


class StubScenario:
    """The duck-typed scenario protocol used by load_params."""

    params_model = Params

    def __init__(self, defaults=None):
        self._defaults = defaults if defaults is not None else {}

    def param_defaults(self):
        return self._defaults


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


# ----------------------------------------------------------------- deep_merge


def test_deep_merge_recurses_and_replaces():
    base = {"ev": {"n": 3, "soc": [0.1, 0.9]}, "x": 1}
    update = {"ev": {"n": 5, "soc": [0.2]}, "y": 2}
    assert deep_merge(base, update) == {"ev": {"n": 5, "soc": [0.2]}, "x": 1, "y": 2}
    assert base == {"ev": {"n": 3, "soc": [0.1, 0.9]}, "x": 1}  # unchanged


def test_deep_merge_append_and_delete():
    base = {"events": [{"at": "1h"}], "ev": {"n": 3, "tags": ["a"]}}
    merged = deep_merge(
        base,
        {"+events": [{"at": "2h"}], "ev": {"-n": None, "+tags": "b"}, "+new": [1]},
    )
    assert merged == {
        "events": [{"at": "1h"}, {"at": "2h"}],
        "ev": {"tags": ["a", "b"]},
        "new": [1],
    }
    assert base["events"] == [{"at": "1h"}]
    assert deep_merge({"a": 1}, {"-missing": True}) == {"a": 1}


def test_deep_merge_append_to_non_list_fails_with_path():
    with pytest.raises(
        ParamsError, match=r"Cannot append to 'ev.n' with '\+n'"
    ) as info:
        deep_merge({"ev": {"n": 3}}, {"ev": {"+n": [1]}})
    assert info.value.path == "ev.n"


def test_deep_merge_kind_switch_replaces():
    base = {"algorithm": {"kind": "greedy", "threshold_kw": 1.0}}
    assert deep_merge(base, {"algorithm": {"kind": "market"}}) == {
        "algorithm": {"kind": "market"}
    }
    assert deep_merge(base, {"algorithm": {"kind": "greedy", "threshold_kw": 2}}) == {
        "algorithm": {"kind": "greedy", "threshold_kw": 2}
    }
    assert deep_merge(base, {"algorithm": {"threshold_kw": 2}}) == {
        "algorithm": {"kind": "greedy", "threshold_kw": 2}
    }


def test_deep_merge_dict_over_scalar_and_markers_in_new_subtrees():
    assert deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
    assert deep_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}
    assert deep_merge({}, {"a": {"+items": [1], "-x": 0}}) == {"a": {"items": [1]}}


# ----------------------------------------------------------------- files


def test_read_config_file_yaml_and_json(tmp_path):
    yml = write(tmp_path / "a.yaml", "ev: {n: 3}\ntime: {horizon: 2d}\n")
    assert read_config_file(yml) == {"ev": {"n": 3}, "time": {"horizon": "2d"}}
    js = tmp_path / "a.json"
    js.write_text(json.dumps({"ev": {"n": 4}}))
    assert read_config_file(str(js)) == {"ev": {"n": 4}}
    assert read_config_file(write(tmp_path / "empty.yml", "")) == {}


def test_read_config_file_extends_relative_to_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "presets/base.yaml", "ev: {n: 3, capacity_kwh: 40}\ntags: [a]\n")
    write(tmp_path / "presets/mid.yaml", "extends: base.yaml\nev: {n: 4}\n")
    child = write(
        tmp_path / "presets/winter.yaml",
        "extends: mid.yaml\nev: {capacity_kwh: 50}\n+tags: [b]\n",
    )
    assert read_config_file(child) == {
        "ev": {"n": 4, "capacity_kwh": 50},
        "tags": ["a", "b"],
    }


def test_read_config_file_extends_prefers_cwd_and_accepts_lists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "studies/base.yaml", "source: cwd\nx: 1\n")
    write(tmp_path / "studies/studies/base.yaml", "source: file-dir\n")
    write(tmp_path / "studies/extra.yaml", "y: 2\nsource: extra\n")
    child = write(
        tmp_path / "studies/child.yaml",
        "extends: [studies/base.yaml, extra.yaml]\nz: 3\n",
    )
    assert read_config_file(child) == {"source": "extra", "x": 1, "y": 2, "z": 3}


def test_read_config_file_extends_absolute(tmp_path):
    parent = write(tmp_path / "p.yaml", "a: 1\n")
    child = write(tmp_path / "sub/c.yaml", f"extends: {parent}\nb: 2\n")
    assert read_config_file(child) == {"a": 1, "b": 2}


def test_read_config_file_detects_cycles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "a.yaml", "extends: b.yaml\n")
    write(tmp_path / "b.yaml", "extends: a.yaml\n")
    with pytest.raises(
        ParamsError, match=r"Cycle in 'extends': a.yaml -> b.yaml -> a.yaml"
    ):
        read_config_file(tmp_path / "a.yaml")
    write(tmp_path / "self.yaml", "extends: self.yaml\n")
    with pytest.raises(ParamsError, match="Cycle"):
        read_config_file("self.yaml")


class MarkerParams(ScenarioParams):
    tags: list[str] = ["a"]
    d: dict[str, int] = {"x": 1, "y": 2}


def test_preset_file_markers_apply_to_the_layers_below(tmp_path, monkeypatch):
    # '-key' and '+list' in a preset file act on the model defaults and the
    # sources below, exactly as the same content passed as a dict
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "minus.yaml", '"+tags": [b]\nd: {"-x": null}\n')
    from_file = load_params(MarkerParams, "minus.yaml")
    from_dict = load_params(MarkerParams, {"+tags": ["b"], "d": {"-x": None}})
    assert from_file.tags == from_dict.tags == ["a", "b"]
    assert from_file.d == from_dict.d == {"y": 2}


def test_preset_file_list_applies_markers_in_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "pa.yaml", "tags: [p]\nd: {z: 3}\n")
    write(tmp_path / "pb.yaml", '"+tags": [q]\nd: {"-z": null, "-x": null}\n')
    params = load_params(MarkerParams, ["pa.yaml", "pb.yaml"])
    assert params.tags == ["p", "q"]
    assert params.d == {"y": 2}


def test_extends_keeps_markers_for_the_layers_below(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "parent.yaml", '"+tags": [b]\nd: {"-x": null}\n')
    write(tmp_path / "child.yaml", 'extends: parent.yaml\n"+tags": [c]\n')
    assert read_config_file("child.yaml") == {"+tags": ["b", "c"], "d": {"-x": None}}
    params = load_params(MarkerParams, "child.yaml")
    assert params.tags == ["a", "b", "c"] and params.d == {"y": 2}
    # a plain value in the child replaces the parent's append; a deletion in
    # the child removes what the parent set and the value below
    write(tmp_path / "plain.yaml", "extends: parent.yaml\ntags: [z]\n")
    assert load_params(MarkerParams, "plain.yaml").tags == ["z"]
    write(tmp_path / "parent2.yaml", "d: {w: 4}\n")
    write(tmp_path / "drop.yaml", 'extends: parent2.yaml\n"-d": null\n"-tags": 0\n')
    params = load_params(MarkerParams, "drop.yaml")
    assert params.d == {"x": 1, "y": 2} and params.tags == ["a"]
    # an append after a deletion starts a new list
    write(tmp_path / "reset.yaml", 'extends: drop.yaml\n"+tags": [n]\n')
    assert load_params(MarkerParams, "reset.yaml").tags == ["n"]


def test_read_config_file_errors_name_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ParamsError, match="Config file not found: 'missing.yaml'"):
        read_config_file("missing.yaml")
    write(tmp_path / "list.yaml", "- 1\n- 2\n")
    with pytest.raises(ParamsError, match="'list.yaml' must contain a mapping"):
        read_config_file("list.yaml")
    write(tmp_path / "bad.yaml", "a: [1\n")
    with pytest.raises(ParamsError, match="Cannot parse 'bad.yaml'") as info:
        read_config_file("bad.yaml")
    assert info.value.source == "bad.yaml"
    write(tmp_path / "x.toml", "a = 1\n")
    with pytest.raises(ParamsError, match=r"Unsupported config file 'x.toml'.*\.yaml"):
        read_config_file("x.toml")
    write(tmp_path / "dangling.yaml", "extends: nowhere.yaml\n")
    with pytest.raises(
        ParamsError, match="extends 'nowhere.yaml', which was not found"
    ):
        read_config_file("dangling.yaml")
    write(tmp_path / "weird.yaml", "extends: {a: 1}\n")
    with pytest.raises(ParamsError, match="'extends' must be a file path"):
        read_config_file("weird.yaml")


# ----------------------------------------------------------------- overrides and paths


def test_parse_overrides_uses_yaml_values():
    parsed = parse_overrides(
        [
            "ev.n=50",
            "algorithm=market",
            "ev.soc={uniform: [0.1, 0.9]}",
            "comm.loss_percent=0.1",
            "flag=true",
            "name='no'",
            "empty=",
            "expr=a=b",
            " spaced.key = 3 ",
        ]
    )
    assert parsed == {
        "ev.n": 50,
        "algorithm": "market",
        "ev.soc": {"uniform": [0.1, 0.9]},
        "comm.loss_percent": 0.1,
        "flag": True,
        "name": "no",
        "empty": None,
        "expr": "a=b",
        "spaced.key": 3,
    }


def test_parse_overrides_reads_scalars_with_yaml_1_2_rules():
    parsed = parse_overrides(
        ["a=010", "b=1:30", "c=on", "d=no", "e=True", "f=1e3", "g=0x10", "h=-2"]
    )
    assert parsed == {
        "a": 10,
        "b": "1:30",
        "c": "on",
        "d": "no",
        "e": True,
        "f": 1000.0,
        "g": 16,
        "h": -2,
    }
    assert parse_overrides(["x={n: 010, t: 1:30}"]) == {"x": {"n": 10, "t": "1:30"}}


def test_parse_overrides_other_forms():
    assert parse_overrides(None) == {}
    assert parse_overrides("ev.n=5") == {"ev.n": 5}
    assert parse_overrides({"ev.n": "5"}) == {"ev.n": "5"}
    assert list(parse_overrides(["a=1", "b=2", "a=3"]).items()) == [("b", 2), ("a", 3)]


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        (["ev.n"], "expected 'path=value'"),
        (["=5"], "Invalid parameter path"),
        (["ev..n=5"], "Invalid parameter path"),
        (["a={b: [1"], "not valid YAML"),
        ([5], "'path=value' strings"),
        ({1: 2}, "dotted path strings"),
        (5, "list of 'path=value' strings or a dict"),
    ],
)
def test_parse_overrides_errors(overrides, match):
    with pytest.raises(ParamsError, match=match):
        parse_overrides(overrides)


def test_set_path_and_get_path():
    data = {}
    set_path(data, "ev.n", 5)
    set_path(data, "ev.soc.low", 0.1)
    set_path(data, "seed", 3)
    assert data == {"ev": {"n": 5, "soc": {"low": 0.1}}, "seed": 3}
    assert get_path(data, "ev.soc.low") == 0.1
    with pytest.raises(ParamsError, match="'ev.n' is 5, not a mapping"):
        set_path(data, "ev.n.x", 1)

    params = Params()
    assert get_path(params, "ev.n") == 10
    assert get_path(params, "time.step_s") == 3600.0
    assert get_path({"a": [{"b": 1}]}, "a.0.b") == 1
    with pytest.raises(KeyError, match="No 'm' in 'ev'"):
        get_path(params, "ev.m")
    with pytest.raises(KeyError, match="No 'x' in 'the root'"):
        get_path({}, "x")


@pytest.mark.parametrize(
    "path",
    [
        "seed",
        "time.step",
        "comm.delay_s",
        "ev.capacity_kwh",
        "ev.soc.low",
        "algorithm.kind",
        "algorithm.threshold_kw",
        "algorithm.rounds",
        "profiles.h0.scale",
        "profiles.anything.column",
        "battery.capacity_kwh",
        "noise.mean",
        "noise.values",
        "comm.options.whatever.deep",
        "tags.0",
        "+tags",
        "-ev",
    ],
)
def test_check_path_accepts_known_paths(path):
    check_path(Params, path)
    check_path(StubScenario(), path)


@pytest.mark.parametrize(
    ("path", "match"),
    [
        (
            "ev.capactiy_kwh",
            "EVParams has no field 'capactiy_kwh'; did you mean 'ev.capacity_kwh'",
        ),
        (
            "algorithm.roundz",
            "Greedy | Market has no field 'roundz'; did you mean 'algorithm.rounds'",
        ),
        ("profiles.h0.scal", "did you mean 'profiles.h0.scale'"),
        ("battery.capactiy_kwh", "did you mean 'battery.capacity_kwh'"),
        ("ev.soc.lo", "did you mean 'ev.soc.low'"),
        ("evv.n", "did you mean 'ev'"),
        ("ev.n.x", "'ev.n' has type int and no sub-parameters"),
        ("tags.x", "'tags' is a list; address an item by its index ('tags.0')"),
        ("zzz", "known: algorithm, battery, comm"),
    ],
)
def test_check_path_rejects_unknown_with_suggestions(path, match):
    with pytest.raises(ParamsError, match=re.escape(match)) as info:
        check_path(Params, path)
    assert info.value.path == path
    assert str(info.value).startswith(f"Unknown parameter {path!r}")


def test_check_path_rejects_bad_targets():
    with pytest.raises(TypeError, match="params_model"):
        check_path(object(), "x")
    with pytest.raises(TypeError):
        check_path(Params(), "x")


# ----------------------------------------------------------------- load_params


def test_load_params_defaults_only():
    params = load_params(Params)
    assert params == Params()
    assert isinstance(params, Params)


def test_load_params_precedence(tmp_path):
    base = write(
        tmp_path / "base.yaml",
        """
        ev: {n: 8, capacity_kwh: 50}
        time: {horizon: 2d}
        tags: [base]
        """,
    )
    scenario = StubScenario(
        {"ev": {"n": 7, "max_power_kw": 7.0}, "time": {"step": "15min"}}
    )

    assert load_params(scenario).ev.n == 7
    assert load_params(scenario).ev.capacity_kwh == 60.0  # model default
    params = load_params(scenario, base)
    assert (params.ev.n, params.ev.capacity_kwh, params.ev.max_power_kw) == (8, 50, 7.0)
    assert params.time.horizon_s == 172800 and params.time.step_s == 900
    params = load_params(scenario, base, {"ev": {"n": 9}, "+tags": ["dict"]})
    assert params.ev.n == 9 and params.ev.capacity_kwh == 50
    assert params.tags == ["base", "dict"]
    params = load_params(scenario, base, {"ev": {"n": 9}}, overrides=["ev.n=11"])
    assert params.ev.n == 11
    params = load_params(scenario, [base, {"ev": {"n": 2}}], None)
    assert params.ev.n == 2


def test_load_params_model_instance_sources_only_set_fields(tmp_path):
    base = write(tmp_path / "base.yaml", "ev: {n: 8, capacity_kwh: 50}\nseed: 4\n")
    instance = Params(ev=EVParams(n=3))
    params = load_params(Params, base, instance)
    assert params.ev.n == 3
    assert params.ev.capacity_kwh == 50  # not reset by the instance's default
    assert params.seed == 4
    full = load_params(Params, base, overrides=["algorithm.kind=market"])
    assert load_params(Params, full) == full
    assert load_params(Params, dump_params(full)) == full


def test_load_params_kind_switch_via_overrides_and_instances(tmp_path):
    base = write(
        tmp_path / "base.yaml", "algorithm: {kind: greedy, threshold_kw: 2.0}\n"
    )
    params = load_params(Params, base, overrides=["algorithm.kind=market"])
    assert params.algorithm == Market()
    params = load_params(
        Params, base, overrides={"algorithm": {"kind": "market", "rounds": 2}}
    )
    assert params.algorithm == Market(rounds=2)
    params = load_params(Params, base, Params(algorithm=Market(rounds=3)))
    assert params.algorithm == Market(rounds=3)
    params = load_params(Params, base, overrides=["algorithm.threshold_kw=4"])
    assert params.algorithm == Greedy(threshold_kw=4)


def test_load_params_distribution_layers_merge(tmp_path):
    base = write(
        tmp_path / "base.yaml",
        "ev: {soc: {uniform: [0.1, 0.9]}}\nnoise: {uniform: [0, 1]}\n",
    )
    assert load_params(Params, base).ev.soc == Uniform(0.1, 0.9)
    params = load_params(
        Params, base, overrides=["ev.soc=[0.3, 0.4]", "noise={normal: [0, 2]}"]
    )
    assert params.ev.soc == Uniform(0.3, 0.4)
    assert params.noise == Normal(0, 2)
    params = load_params(Params, base, overrides=["ev.soc.low=0.2", "noise=0.5"])
    assert params.ev.soc == Uniform(0.2, 0.9)
    assert params.noise == Constant(0.5)
    params = load_params(
        Params, base, overrides=["ev.soc=0.5", "ev.arrival_h={normal: [17, 1]}"]
    )
    assert params.ev.soc == Uniform(0.5, 0.5)
    assert params.ev.arrival_h == Normal(17, 1)
    params = load_params(
        Params, base, {"noise": Normal(1, 1)}, overrides=["noise.std=3"]
    )
    assert params.noise == Normal(1, 3)


def test_load_params_typo_in_file_names_path_and_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "presets/base.yaml", "ev: {n: 3}\n")
    write(
        tmp_path / "presets/winter.yaml",
        "extends: base.yaml\nev: {capactiy_kwh: 50}\n",
    )
    with pytest.raises(ParamsError) as info:
        load_params(Params, "presets/winter.yaml")
    err = info.value
    assert isinstance(err, ScenarioError) and isinstance(err, ValueError)
    assert err.path == "ev.capactiy_kwh"
    assert err.source == "presets/winter.yaml"
    assert "did you mean 'ev.capacity_kwh'" in str(err)
    assert "[from presets/winter.yaml]" in str(err)


def test_load_params_value_errors_name_path_and_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "base.yaml", "ev: {n: three}\ntime: {horizon: 1d}\n")
    with pytest.raises(ParamsError) as info:
        load_params(
            Params,
            "base.yaml",
            overrides=["time.step=7min", "algorithm.kind=market", "algorithm.rounds=x"],
        )
    err = info.value
    paths = {entry["path"]: entry for entry in err.errors}
    assert set(paths) == {"ev.n", "time", "algorithm.rounds"}
    assert paths["ev.n"]["source"] == "base.yaml"
    assert "got 'three'" in paths["ev.n"]["message"]
    assert paths["time"]["source"] == "overrides"
    assert "horizon 1d is not a multiple of step 7min" in paths["time"]["message"]
    assert paths["algorithm.rounds"]["source"] == "overrides"
    text = str(err)
    assert text.startswith("Invalid parameters for Params (3 errors):")
    assert "  ev.n: Input should be a valid integer" in text


def test_load_params_override_typos_fail_before_validation():
    with pytest.raises(ParamsError) as info:
        load_params(
            Params, overrides=["ev.capactiy_kwh=3", "ev.n=4", "algoritm=market"]
        )
    err = info.value
    assert [entry["path"] for entry in err.errors] == ["ev.capactiy_kwh", "algoritm"]
    assert err.source == "overrides"
    assert "did you mean 'ev.capacity_kwh'" in str(err)
    assert "did you mean 'algorithm'" in str(err)


def test_load_params_errors_for_dict_and_instance_sources():
    with pytest.raises(ParamsError, match=r"\[from source 2 \(dict\)\]"):
        load_params(Params, {"seed": 1}, {"ev": {"nn": 1}})
    with pytest.raises(
        ParamsError, match="seed: Input should be greater than or equal to 0"
    ):
        load_params(Params, {"seed": -1})
    with pytest.raises(ParamsError, match=r"\[from scenario defaults\]"):
        load_params(StubScenario({"ev": {"n": "x"}}))
    with pytest.raises(
        ParamsError, match=r"Cannot append to 'seed' .* \(in source 2 \(dict\)\)"
    ) as info:
        load_params(Params, {"seed": 1}, {"+seed": [1]})
    assert info.value.source == "source 2 (dict)"


def test_load_params_appends_to_model_default_lists():
    class Home(BaseModel, extra="forbid"):
        kw: float

    class Fleet(BaseModel, extra="forbid"):
        tags: list[str] = ["base"]

    class ListParams(ScenarioParams):
        homes: list[Home] = [Home(kw=1.0), Home(kw=2.0)]
        fleet: Fleet = Fleet()
        spare: Fleet | None = None
        required: Fleet = Field(default_factory=Fleet)

    params = load_params(ListParams, overrides={"+homes": [{"kw": 3.0}]})
    assert [home.kw for home in params.homes] == [1.0, 2.0, 3.0]
    params = load_params(ListParams, {"fleet": {"+tags": "dict"}})
    assert params.fleet.tags == ["base", "dict"]
    assert params.spare is None
    params = load_params(ListParams, overrides=["required.+tags=x"])
    assert params.required.tags == ["base", "x"]
    # a lower layer that set the list wins over the model default
    params = load_params(ListParams, {"homes": []}, {"+homes": {"kw": 5.0}})
    assert [home.kw for home in params.homes] == [5.0]
    # the default itself is never mutated
    assert [home.kw for home in ListParams().homes] == [1.0, 2.0]
    assert ListParams().fleet.tags == ["base"]


def test_load_params_tunes_and_switches_the_default_variant_without_kind():
    # a field of the default variant needs no repeated kind tag
    params = load_params(Params, overrides=["algorithm.threshold_kw=1.0"])
    assert params.algorithm == Greedy(threshold_kw=1.0)
    preset = {"algorithm": {"threshold_kw": 1.0}}
    assert load_params(Params, preset).algorithm.threshold_kw == 1.0
    # switching the kind over such a layer replaces the variant's settings
    params = load_params(Params, preset, overrides=["algorithm.kind=market"])
    assert params.algorithm == Market()
    params = load_params(Params, preset, {"algorithm": {"kind": "greedy"}})
    assert params.algorithm.threshold_kw == 1.0
    # a field of another variant names the variant
    with pytest.raises(
        ParamsError, match="not a field of the 'greedy' variant"
    ) as info:
        load_params(Params, overrides=["algorithm.rounds=3"])
    assert info.value.path == "algorithm.rounds"
    # a plain model with a kind field is no tagged union: its layers still merge
    params = load_params(
        Params, {"comm": {"factory": "m:f"}}, overrides=["comm.kind=custom"]
    )
    assert (params.comm.kind, params.comm.factory) == ("custom", "m:f")


class Lenient(BaseModel):  # pydantic's default: unknown keys are dropped silently
    capacity_kwh: float = 60.0
    arrival: Dist = Normal(16.0, 1.5)


class Home(BaseModel, extra="forbid"):
    kw: float = 1.0
    pv: Uniform = Uniform(0.0, 1.0)


class IndexParams(ScenarioParams):
    homes: list[Home] = [Home(), Home(kw=2.0)]
    lenient: Lenient = Lenient()
    ignored: dict[str, Any] = {}


def test_load_params_patches_list_items_by_index():
    check_path(IndexParams, "homes.1.kw")
    params = load_params(IndexParams, overrides=["homes.1.kw=5"])
    assert [home.kw for home in params.homes] == [1.0, 5.0]
    params = load_params(
        IndexParams, {"homes": [{"kw": 7.0}]}, overrides=["homes.0.kw=8"]
    )
    assert [home.kw for home in params.homes] == [8.0]
    assert load_params(IndexParams, overrides=["homes.0.pv.high=0.5"]).homes[0].pv == (
        Uniform(0.0, 0.5)
    )
    with pytest.raises(ParamsError, match=r"'homes' has 2 items \(indices 0\.\.1\)"):
        load_params(IndexParams, overrides=["homes.2.kw=1"])
    assert IndexParams().homes[1].kw == 2.0


def test_index_past_a_shorter_preset_list_is_a_params_error(tmp_path):
    class Three(ScenarioParams):
        homes: list[Home] = [Home(), Home(), Home()]

    preset = write(tmp_path / "two.yaml", "homes:\n  - {kw: 1}\n  - {kw: 2}\n")
    for overrides in (["homes.2.kw=9"], {"homes.2.kw": 9}):
        with pytest.raises(
            ParamsError, match=r"Cannot set 'homes\.2': 'homes' has 2 items"
        ) as info:
            load_params(Three, preset, overrides=overrides)
        assert info.value.source == "overrides"
    # the model default's third item is reachable without the shorter preset
    assert load_params(Three, overrides=["homes.2.kw=9"]).homes[2].kw == 9.0


class GreedyLenient(BaseModel):  # no extra="forbid": pydantic would drop 'rounds'
    kind: Literal["greedy"] = "greedy"
    threshold_kw: float = 0.0


class MarketLenient(BaseModel):
    kind: Literal["market"] = "market"
    rounds: int = 1


class MarketIgnoring(BaseModel, extra="ignore"):
    kind: Literal["ignoring"] = "ignoring"


class LenientUnion(ScenarioParams):
    algorithm: GreedyLenient | MarketLenient | MarketIgnoring = Field(
        GreedyLenient(), discriminator="kind"
    )
    queue: list[GreedyLenient | MarketLenient] = []


def test_a_field_of_another_variant_is_unknown_without_extra_forbid(tmp_path):
    variant = "it is not a field of the 'market' variant"
    for sources, overrides in (
        ((), ["algorithm.kind=market", "algorithm.threshold_kw=3"]),
        (({"algorithm": {"kind": "market", "threshold_kw": 3}},), None),
        (({"algorithm": {"kind": "market"}},), ["algorithm.threshold_kw=3"]),
    ):
        with pytest.raises(ParamsError, match=variant) as info:
            load_params(LenientUnion, *sources, overrides=overrides)
        assert info.value.path == "algorithm.threshold_kw"
    # the kind of a lower layer is named with the layer of the stray key
    preset = write(tmp_path / "market.yaml", "algorithm: {kind: market}\n")
    with pytest.raises(ParamsError, match=r"\[from overrides\]"):
        load_params(LenientUnion, preset, overrides=["algorithm.threshold_kw=3"])
    # without a kind, the default variant decides
    with pytest.raises(ParamsError, match="not a field of the 'greedy' variant"):
        load_params(LenientUnion, overrides=["algorithm.rounds=3"])
    with pytest.raises(ParamsError, match="queue.0.threshold_kw: unknown parameter"):
        load_params(LenientUnion, {"queue": [{"kind": "market", "threshold_kw": 1}]})
    # valid switches, and a variant that explicitly ignores extra keys
    params = load_params(LenientUnion, overrides=["algorithm.kind=market"])
    assert params.algorithm == MarketLenient()
    params = load_params(
        LenientUnion,
        {"algorithm": {"threshold_kw": 2}},
        overrides=["algorithm.kind=market"],
    )
    assert params.algorithm == MarketLenient()
    params = load_params(
        LenientUnion, overrides=["algorithm.kind=ignoring", "algorithm.rounds=3"]
    )
    assert params.algorithm == MarketIgnoring()


def test_load_params_partial_distribution_keeps_the_model_default():
    params = load_params(Params, overrides=["ev.soc.high=0.9"])
    assert params.ev.soc == Uniform(0.2, 0.9)
    assert load_params(
        IndexParams, overrides=["lenient.arrival.std=2"]
    ).lenient.arrival == (Normal(16.0, 2.0))
    # a distribution of another kind still replaces the default
    params = load_params(IndexParams, overrides=["lenient.arrival={uniform: [1, 2]}"])
    assert params.lenient.arrival == Uniform(1, 2)


def test_load_params_reports_typos_in_nested_models_without_extra_forbid(tmp_path):
    preset = write(tmp_path / "typo.yaml", "lenient: {capactiy_kwh: 100}\n")
    with pytest.raises(
        ParamsError, match="did you mean 'lenient.capacity_kwh'"
    ) as info:
        load_params(IndexParams, preset)
    assert info.value.source == str(preset)
    with pytest.raises(ParamsError, match="lenient.capactiy_kwh: unknown parameter"):
        load_params(IndexParams, overrides={"lenient": {"capactiy_kwh": 1}})
    # unknown keys and invalid values are reported together
    with pytest.raises(ParamsError, match=r"\(2 errors\)") as info:
        load_params(IndexParams, {"lenient": {"capacity_kwh": "x", "capactiy_kwh": 1}})
    assert [entry["path"] for entry in info.value.errors] == [
        "lenient.capacity_kwh",
        "lenient.capactiy_kwh",
    ]
    assert load_params(IndexParams, {"ignored": {"anything": 1}}).ignored == {
        "anything": 1
    }


def test_load_params_value_error_hints():
    with pytest.raises(ParamsError, match="set algorithm.kind=market"):
        load_params(Params, overrides=["algorithm=market"])

    class Labeled(ScenarioParams):
        name: str = "x"

    with pytest.raises(
        ParamsError, match="quote the value to keep it text, e.g. name='2024'"
    ):
        load_params(Labeled, overrides=["name=2024"])
    # the hint quotes what was typed, not the parsed value
    for typed in ("null", "~", "1e3", "true"):
        with pytest.raises(ParamsError, match=re.escape(f"e.g. name='{typed}'")):
            load_params(Labeled, overrides=[f"name={typed}"])
    with pytest.raises(ParamsError, match=re.escape("e.g. name=''")):
        load_params(Labeled, overrides=["name="])
    with pytest.raises(ParamsError) as info:
        load_params(Labeled, overrides={"name": 2024})
    assert "quote" not in str(info.value)

    class Nested(ScenarioParams):
        inner: Labeled = Labeled()

    with pytest.raises(ParamsError) as info:
        load_params(Nested, overrides=["inner={name: 5}"])
    assert str(info.value).count("quote the value to keep it text") == 1
    assert "e.g." not in str(info.value)


def test_load_params_rejects_invalid_targets_and_sources():
    with pytest.raises(TypeError, match="params_model"):
        load_params({"ev": 1})
    with pytest.raises(TypeError, match="must be a path, a dict or a model instance"):
        load_params(Params, 42)

    class BadScenario(StubScenario):
        def param_defaults(self):
            return ["not", "a", "dict"]

    with pytest.raises(TypeError, match="param_defaults"):
        load_params(BadScenario())


def test_load_params_missing_required_field():
    class Strict(ScenarioParams):
        must: int

    with pytest.raises(ParamsError, match="must: Field required"):
        load_params(Strict)
    assert load_params(Strict, overrides=["must=3"]).must == 3


def test_load_params_reads_calendar_values(tmp_path):
    base = write(
        tmp_path / "base.yaml",
        "time: {start: 2024-06-01T00:00, horizon: 2d, step: 15min}\n",
    )
    params = load_params(Params, base)
    assert params.time.start == dt.datetime(2024, 6, 1)
    assert params.time.n_steps == 192
    params = load_params(Params, base, overrides=["time.start=2024-01-15"])
    assert params.time.start == dt.datetime(2024, 1, 15)


# ----------------------------------------------------------------- output


def test_params_schema_dump_and_yaml():
    schema = params_schema(Params)
    assert schema["additionalProperties"] is False
    assert {"seed", "time", "comm", "ev", "algorithm"} <= set(schema["properties"])
    assert params_schema(StubScenario()) == schema

    params = load_params(
        Params,
        {"time": {"start": "2024-06-01T00:00", "horizon": "2d", "step": "15min"}},
    )
    dumped = dump_params(params)
    assert dumped["time"] == {
        "start": "2024-06-01T00:00:00",
        "horizon": "2d",
        "step": "15min",
    }
    assert dumped["ev"]["soc"] == {"kind": "uniform", "low": 0.2, "high": 0.8}
    json.dumps(dumped)
    text = params_to_yaml(params)
    assert "step: 15min" in text
    assert text.index("seed") < text.index("time") < text.index("ev")
    assert load_params(Params, yaml.safe_load(text)) == params
    with pytest.raises(TypeError):
        dump_params({"a": 1})


# ----------------------------------------------------------------- communication


def test_comm_config_validation():
    assert CommConfig().kind == "simple"
    assert CommConfig(delay_s=0.1).delay_s == 0.1
    assert CommConfig(delay_s={"normal": [0.04, 0.008]}).delay_s == Normal(0.04, 0.008)
    assert CommConfig(delay_s={"constant": 0.2}).delay_s == 0.2
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        CommConfig(loss_percent=10)
    with pytest.raises(ValidationError, match="comm.delay_s must be >= 0"):
        CommConfig(delay_s=-1)
    with pytest.raises(ValidationError, match="seconds, a duration or a distribution"):
        CommConfig(delay_s="fast")
    # durations, also inside distributions
    assert CommConfig(delay_s="2h").delay_s == 7200.0
    assert CommConfig(delay_s={"uniform": ["1h", "2h"]}).delay_s == Uniform(
        3600.0, 7200.0
    )
    with pytest.raises(ValidationError, match="needs comm.factory"):
        CommConfig(kind="custom")
    with pytest.raises(ValidationError, match="Extra inputs"):
        CommConfig(los_percent=0.1)


def _messages(n=50):
    return [MessagePackage("a", f"b{i % 3}", 0.0, ("x", {})) for i in range(n)]


def _outcomes(sim, n=50):
    result = sim.calculate_communication(0.0, _messages(n))
    return [(r.reached, r.delay_s) for r in result.package_results]


def test_build_communication_ideal():
    sim = build_communication(
        CommConfig(kind="ideal", loss_percent=0.5, delay_s=3), SeedTree(1).rng("comm")
    )
    assert type(sim) is SimpleCommunicationSimulation
    assert all(outcome == (True, 0.0) for outcome in _outcomes(sim))


def test_build_communication_simple_constant_is_deterministic():
    cfg = CommConfig(loss_percent=0.5, delay_s=0.25)
    sim = build_communication(cfg, SeedTree(7).rng("comm"))
    assert type(sim) is SimpleCommunicationSimulation
    assert sim.loss_percent == 0.5 and sim.default_delay_s == 0.25
    first = _outcomes(sim)
    assert {delay for _, delay in first} == {0.25}
    assert 0 < sum(reached for reached, _ in first) < len(first)
    assert _outcomes(build_communication(cfg, SeedTree(7).rng("comm"))) == first
    assert _outcomes(build_communication(cfg, SeedTree(8).rng("comm"))) != first
    assert build_communication({"delay_s": 0.5}, None).default_delay_s == 0.5


def test_build_communication_simple_distribution_is_deterministic():
    cfg = CommConfig(loss_percent=0.1, delay_s={"normal": [0.04, 0.02]})
    sim = build_communication(cfg, SeedTree(7).rng("comm"))
    assert isinstance(sim, DelayProviderCommunicationSimulation)
    first = _outcomes(sim, 200)
    delays = [delay for _, delay in first]
    assert min(delays) >= 0.0 and len(set(delays)) > 100
    assert abs(sum(delays) / len(delays) - 0.04) < 0.01
    assert 0 < sum(not reached for reached, _ in first) < 60
    assert _outcomes(build_communication(cfg, SeedTree(7).rng("comm")), 200) == first
    stdlib = build_communication(cfg, SeedTree(7).stdlib_random("comm"))
    assert all(delay >= 0 for _, delay in _outcomes(stdlib))


@pytest.fixture
def comm_module(tmp_path, monkeypatch):
    name = "scenario_params_comm_factories"
    (tmp_path / f"{name}.py").write_text(
        textwrap.dedent(
            """
            from mango.simulation import SimpleCommunicationSimulation

            def make(cfg, rng):
                return SimpleCommunicationSimulation(
                    0.0, cfg.options["delay"], rng=rng
                )

            def broken(cfg, rng):
                return "not a simulation"
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    sys.modules.pop(name, None)


def test_build_communication_custom(comm_module):
    rng = SeedTree(1).rng("comm")
    cfg = CommConfig(
        kind="custom", factory=f"{comm_module}:make", options={"delay": 0.3}
    )
    sim = build_communication(cfg, rng)
    assert sim.default_delay_s == 0.3 and sim.rng is rng
    bad = CommConfig(kind="custom", factory=f"{comm_module}:broken")
    with pytest.raises(TypeError, match="must return a CommunicationSimulation"):
        build_communication(bad, rng)
    missing = CommConfig(kind="custom", factory=f"{comm_module}:nope")
    with pytest.raises(AttributeError, match="nope"):
        build_communication(missing, rng)


def test_scenario_params_base():
    params = ScenarioParams()
    assert params.seed is None and params.time == params.time.__class__()
    assert ScenarioParams().time is not params.time  # no shared default instance
    with pytest.raises(ValidationError, match="Extra inputs"):
        ScenarioParams(sed=1)


# ----------------------------------------------------------------- import guard


def _fresh_import(monkeypatch):
    for name in list(sys.modules):
        if name == "mango.scenario" or name.startswith("mango.scenario."):
            monkeypatch.delitem(sys.modules, name)
    return importlib.import_module("mango.scenario")


def test_import_without_pydantic_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pydantic", None)
    with pytest.raises(ImportError) as info:
        _fresh_import(monkeypatch)
    assert str(info.value) == (
        "mango.scenario needs the optional dependencies: "
        "pip install 'mango-agents[scenario]'"
    )


def test_import_without_yaml_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(
        ImportError, match=re.escape("pip install 'mango-agents[scenario]'")
    ):
        _fresh_import(monkeypatch)


def test_import_with_pydantic_v1_raises_install_hint(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "pydantic", types.SimpleNamespace(VERSION="1.10.13")
    )
    with pytest.raises(ImportError, match=r"found pydantic 1\.10\.13, needs >= 2"):
        _fresh_import(monkeypatch)


def test_public_api_is_curated():
    import mango.scenario as scenario

    assert set(scenario.__all__) <= set(dir(scenario))
    assert "load_params" in scenario.__all__ and "Dist" in scenario.__all__
    assert not any(name.startswith("_") for name in scenario.__all__)
