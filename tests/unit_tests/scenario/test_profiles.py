import datetime as dt
import os
import random
import shutil
import sys

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")

import numpy as np  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Constant,
    Dist,
    FromColumn,
    ParamsError,
    Profile,
    ProfileSpec,
    Scenario,
    ScenarioError,
    ScenarioParams,
    Uniform,
    _util,  # noqa: E402
    clear_profile_cache,
    distribution_kinds,
    load_params,
    load_profile,
    read_column,
)
from mango.scenario import profiles as profiles_module  # noqa: E402
from mango.util.clock import ExternalClock  # noqa: E402
from mango.util.seeding import SeedTree, name_key  # noqa: E402

H = 3600.0
START = dt.datetime(2024, 6, 1)


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_profile_cache()
    yield
    clear_profile_cache()
    _util.set_project_root(None)


def inline(values=(0.0, 10.0, 20.0, 30.0), **extra):
    return ProfileSpec(source="inline", values=list(values), resolution="1h", **extra)


def write_csv(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


# ----------------------------------------------------------------- spec


def test_profile_spec_validation_and_inference():
    with pytest.raises(ValidationError, match="needs values"):
        ProfileSpec(source="inline", resolution="1h")
    with pytest.raises(ValidationError, match="needs path"):
        ProfileSpec(source="csv", resolution="1h")
    with pytest.raises(ValidationError, match="resolution is required"):
        ProfileSpec(source="inline", values=[1.0])
    with pytest.raises(ValidationError, match="not column"):
        ProfileSpec(source="inline", values=[1.0], column="a", resolution="1h")
    with pytest.raises(ValidationError, match="drop values"):
        ProfileSpec(source="csv", path="x.csv", values=[1.0], resolution="1h")
    with pytest.raises(ValidationError, match="finite"):
        ProfileSpec(source="inline", values=[1.0, float("nan")], resolution="1h")
    with pytest.raises(ValidationError, match="noise"):
        inline(noise=-0.1)
    with pytest.raises(ValidationError, match="twice"):
        ProfileSpec(path="x.csv", column=["a", "a"], resolution="1h")
    with pytest.raises(ValidationError, match="also listed"):
        ProfileSpec(path="x.csv", column=["t", "a"], time_column="t")
    with pytest.raises(ValidationError, match="extra"):
        ProfileSpec(path="x.csv", resolution="1h", colum="a")
    assert ProfileSpec(values=[1.0], resolution="1h").source == "inline"
    assert ProfileSpec(path="d/x.parquet", resolution="1h").source == "parquet"
    assert ProfileSpec(path="d/x.csv", time_column="t").source == "csv"
    spec = ProfileSpec(path=profiles_module.Path("d") / "x.csv", resolution="15min")
    assert spec.path == "d/x.csv"
    data = spec.model_dump(mode="json")
    assert data["resolution"] == "15min" and data["path"] == "d/x.csv"
    assert ProfileSpec.model_validate(data) == spec


def test_scenario_params_profiles_field():
    params = load_params(
        ScenarioParams,
        {
            "profiles": {
                "h0": {"source": "inline", "values": [1, 2], "resolution": "1h"}
            }
        },
    )
    assert isinstance(params.profiles["h0"], ProfileSpec)
    with pytest.raises(ParamsError, match="profile name"):
        load_params(
            ScenarioParams, {"profiles": {"a.b": {"values": [1], "resolution": "1h"}}}
        )
    with pytest.raises(ParamsError, match="colum"):
        load_params(
            ScenarioParams,
            {"profiles": {"h0": {"path": "x.csv", "resolution": "1h", "colum": "a"}}},
        )
    params = load_params(
        ScenarioParams,
        {"profiles": {"h0": {"values": [1, 2], "resolution": "1h"}}},
        overrides=["profiles.h0.scale=2.5"],
    )
    assert params.profiles["h0"].scale == 2.5
    # a later layer switching the source deletes the other source's keys
    inline_layer = {"profiles": {"h0": {"values": [1, 2], "resolution": "1h"}}}
    csv_layer = {"profiles": {"h0": {"source": "csv", "path": "x.csv", "column": "a"}}}
    with pytest.raises(ParamsError, match="'-values'"):
        load_params(ScenarioParams, inline_layer, csv_layer)
    csv_layer["profiles"]["h0"]["-values"] = None
    params = load_params(ScenarioParams, inline_layer, csv_layer)
    assert (
        params.profiles["h0"].path == "x.csv" and params.profiles["h0"].values is None
    )


# ----------------------------------------------------------------- accessor


def test_step_and_linear_interpolation_with_wrap_and_scale():
    step = load_profile(inline(), name="load")
    assert isinstance(step, Profile) and len(step) == 4
    assert [step.at(t) for t in (0, 1800, H, 2 * H + 1, 4 * H, 5.5 * H, -H)] == [
        0.0,
        0.0,
        10.0,
        20.0,
        0.0,
        10.0,
        30.0,
    ]
    assert step(H) == 10.0
    assert step.resolution_s == H and step.period_s == 4 * H
    # 15 min steps accumulate float error; the grid must still hit exact samples
    t = 0.0
    for _ in range(4):
        t += 900.0
    assert step.at(t) == 10.0
    linear = load_profile(inline(interpolation="linear"), name="load")
    assert linear.at(1800) == pytest.approx(5.0)
    assert linear.at(3.5 * H) == pytest.approx(15.0)  # wraps toward the first sample
    scaled = load_profile(inline(scale=2.0), name="load")
    assert scaled.values.tolist() == [0.0, 20.0, 40.0, 60.0]
    with pytest.raises(ValueError):
        scaled.values[0] = 1.0


def test_no_wrap_raises_outside_the_profile():
    profile = load_profile(inline(wrap=False, interpolation="linear"), name="load")
    assert profile.at(4 * H) == 30.0
    assert profile.at(3.5 * H) == 30.0
    with pytest.raises(ScenarioError, match="does not wrap"):
        profile.at(4 * H + 60)
    with pytest.raises(ScenarioError, match="does not wrap"):
        profile.at(-60)


def test_at_accepts_clocks_datetimes_strings_and_seconds():
    profile = load_profile(inline(), name="load", scenario_start=START)
    assert profile.start == START
    assert profile.at(dt.datetime(2024, 6, 1, 1)) == 10.0
    assert profile.at("2024-06-01T02:00") == 20.0
    assert profile.at("1h") == 10.0
    clock = ExternalClock(start_datetime="2024-06-01T00:00")
    clock.set_time(2 * H)
    assert profile.at(clock) == 20.0
    plain = ExternalClock()
    plain.set_time(3 * H)
    assert profile.at(plain) == 30.0
    with pytest.raises(TypeError, match="clock"):
        profile.at(object())
    anchorless = load_profile(inline(), name="load")
    with pytest.raises(ScenarioError, match="no calendar start"):
        anchorless.at(dt.datetime(2024, 6, 1, 1))
    assert anchorless.at(clock) == 20.0  # no calendar on the profile: clock.time


def test_profile_start_differs_from_scenario_start():
    spec = inline(start="2024-06-01T01:00")
    profile = load_profile(spec, name="load", scenario_start=START)
    assert profile.at(H) == 0.0  # scenario offset 1 h = first sample
    assert profile.at(0.0) == 30.0  # one hour before the profile: wraps
    clock = ExternalClock(start_datetime=START)
    clock.set_time(2 * H)
    assert profile.at(clock) == 10.0
    aware = inline(start="2024-06-01T00:00+02:00")
    berlin = load_profile(
        aware,
        name="load",
        scenario_start=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
    )
    assert berlin.at(0.0) == 20.0  # 00:00 UTC is 02:00 at +02:00


def test_window_returns_samples_at_the_resolution():
    profile = load_profile(inline(), name="load", scenario_start=START)
    assert profile.window(0, 3 * H).tolist() == [0.0, 10.0, 20.0]
    assert profile.window("2024-06-01T02:00", "2024-06-01T06:00").tolist() == [
        20.0,
        30.0,
        0.0,
        10.0,
    ]
    assert profile.window(H, H).tolist() == []
    with pytest.raises(ValueError, match="before"):
        profile.window(2 * H, H)


# ----------------------------------------------------------------- files


def test_csv_profiles_with_columns_keys_and_errors(tmp_path):
    path = write_csv(
        tmp_path / "p.csv",
        pd.DataFrame({"h0": [1.0, 2.0], "pv": [0.0, 1.0], "west": [5.0, 6.0]}),
    )
    one = load_profile(
        ProfileSpec(path=str(path), column="pv", resolution="1h"), name="pv"
    )
    assert one.values.tolist() == [0.0, 1.0] and one.column == "pv"
    spec = ProfileSpec(path=str(path), column=["pv", "west"], resolution="1h")
    assert load_profile(spec, name="pv", key=3).column == "west"
    assert load_profile(spec, name="pv", key="pv").column == "pv"
    assert load_profile(spec, name="pv").column == "pv"
    every = load_profile(
        ProfileSpec(path=str(path), resolution="1h"), name="all", key=2
    )
    assert every.column == "west"
    with pytest.raises(ScenarioError, match="no column 'east'"):
        load_profile(spec, name="pv", key="east")
    with pytest.raises(TypeError, match="column name or an int"):
        load_profile(spec, name="pv", key=1.5)
    with pytest.raises(ScenarioError, match="Did you mean 'pv'"):
        load_profile(ProfileSpec(path=str(path), column="pvv", resolution="1h"))
    bad = write_csv(
        tmp_path / "bad.csv", pd.DataFrame({"a": [1.0, None], "b": ["x", "y"]})
    )
    with pytest.raises(ScenarioError, match="non-finite value in data row 1"):
        load_profile(ProfileSpec(path=str(bad), column="a", resolution="1h"))
    with pytest.raises(ScenarioError, match="not numeric"):
        load_profile(ProfileSpec(path=str(bad), column="b", resolution="1h"))
    with pytest.raises(ScenarioError, match="not found"):
        load_profile(ProfileSpec(path=str(tmp_path / "nope.csv"), resolution="1h"))


def test_csv_with_datetime_time_column(tmp_path):
    times = pd.date_range("2024-06-01T00:00", periods=4, freq="15min")
    path = write_csv(
        tmp_path / "t.csv",
        pd.DataFrame(
            {"time": times.strftime("%Y-%m-%dT%H:%M"), "load": [1.0, 2.0, 3.0, 4.0]}
        ),
    )
    spec = ProfileSpec(path=str(path), column="load", time_column="time")
    profile = load_profile(
        spec, name="load", scenario_start=dt.datetime(2024, 6, 1, 0, 30)
    )
    assert profile.start == START
    assert profile.resolution_s == 900.0 and profile.period_s == 3600.0
    assert profile.at(0.0) == 3.0  # scenario start 00:30 -> third sample
    clock = ExternalClock(start_datetime="2024-06-01T00:00")
    clock.set_time(900.0)
    assert profile.at(clock) == 2.0  # calendar time of the clock
    # dated profiles do not wrap by default: reading after their dates fails
    with pytest.raises(ScenarioError, match="does not wrap .*dated by its time_column"):
        profile.at(dt.datetime(2024, 6, 1, 1, 15))
    wrapping = load_profile(
        spec.model_copy(update={"wrap": True}),
        name="load",
        scenario_start=dt.datetime(2024, 6, 1, 0, 30),
    )
    assert wrapping.at(dt.datetime(2024, 6, 1, 1, 15)) == 2.0  # wrapped
    with pytest.raises(ScenarioError, match="start is only used"):
        load_profile(spec.model_copy(update={"start": START}), name="load")


def test_numeric_irregular_time_column_and_linear_interpolation(tmp_path):
    path = write_csv(
        tmp_path / "irregular.csv",
        pd.DataFrame({"t": [0.0, 900.0, 3600.0], "v": [0.0, 9.0, 36.0]}),
    )
    spec = ProfileSpec(path=str(path), column="v", time_column="t", resolution="15min")
    step = load_profile(spec, name="v")
    assert step.at(1000.0) == 9.0 and step.at(3599.0) == 9.0 and step.at(3600.0) == 36.0
    assert step.period_s == 4500.0
    linear = load_profile(spec.model_copy(update={"interpolation": "linear"}), name="v")
    assert linear.at(2250.0) == pytest.approx(22.5)
    assert linear.at(4050.0) == pytest.approx(18.0)  # halfway back to the first sample
    unsorted = write_csv(
        tmp_path / "u.csv", pd.DataFrame({"t": [0, 10, 5], "v": [1, 2, 3]})
    )
    with pytest.raises(ScenarioError, match="strictly increasing"):
        load_profile(ProfileSpec(path=str(unsorted), column="v", time_column="t"))
    single = write_csv(tmp_path / "s.csv", pd.DataFrame({"t": [0.0], "v": [1.0]}))
    with pytest.raises(ScenarioError, match="single sample"):
        load_profile(ProfileSpec(path=str(single), column="v", time_column="t"))


def test_parquet_profiles(tmp_path):
    pytest.importorskip("pyarrow")
    path = tmp_path / "p.parquet"
    pd.DataFrame({"pv": [0.0, 0.5, 1.0]}).to_parquet(path, index=False)
    profile = load_profile(ProfileSpec(path=str(path), column="pv", resolution="1h"))
    assert profile.values.tolist() == [0.0, 0.5, 1.0]
    with pytest.raises(ScenarioError, match="no column"):
        load_profile(ProfileSpec(path=str(path), column="h0", resolution="1h"))
    assert read_column(path, "pv").tolist() == [0.0, 0.5, 1.0]


def test_parquet_without_pyarrow_names_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    path = tmp_path / "p.parquet"
    path.write_bytes(b"")
    with pytest.raises(ImportError, match="needs pyarrow") as info:
        load_profile(ProfileSpec(path=str(path), column="pv", resolution="1h"))
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_tables_are_read_once_per_process(tmp_path, monkeypatch):
    path = write_csv(tmp_path / "p.csv", pd.DataFrame({"a": [1.0, 2.0]}))
    reads = []
    original = profiles_module._read_table

    def counting(*args):
        reads.append(args[1])
        return original(*args)

    monkeypatch.setattr(profiles_module, "_read_table", counting)
    spec = ProfileSpec(path=str(path), column="a", resolution="1h")
    first = load_profile(spec, name="a")
    second = load_profile(spec, name="a", key=0)
    assert len(reads) == 1
    assert first.values.tolist() == second.values.tolist()
    write_csv(path, pd.DataFrame({"a": [3.0, 4.0, 5.0]}))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert load_profile(spec, name="a").values.tolist() == [3.0, 4.0, 5.0]
    assert len(reads) == 2
    clear_profile_cache()
    load_profile(spec, name="a")
    assert len(reads) == 3


# ----------------------------------------------------------------- scenarios


class Holder(Agent):
    def __init__(self, profile, first_draw):
        super().__init__()
        self.profile = profile
        self.first_draw = first_draw
        self.seen = []

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        if self.profile is not None:
            self.seen.append(self.profile.at(clock))


class HolderParams(ScenarioParams):
    n: int = 3


def holder_scenario(**profile):
    scenario = Scenario("profiles", params=HolderParams)
    scenario.time(start="2024-06-01T00:00", horizon="2h", step="1h")
    if profile:
        scenario.defaults({"profiles": {"load": profile}})
    scenario.group("hh", "n", lambda e: Holder(e.profile("load"), e.rng.random()))
    return scenario


VALUES = [1.0, 2.0, 3.0, 4.0]


def test_entity_noise_is_reproducible_per_entity_and_independent():
    noisy = holder_scenario(values=VALUES, resolution="1h", noise=0.1)
    a = noisy.run(seed=1)
    b = noisy.run(seed=1)
    four = noisy.run(seed=1, overrides=["n=4"])
    for name in ("hh_0", "hh_1", "hh_2"):
        assert (
            a.agent(name).profile.values.tolist()
            == b.agent(name).profile.values.tolist()
        )
        assert (
            a.agent(name).profile.values.tolist()
            == four.agent(name).profile.values.tolist()
        )
    assert (
        a.agent("hh_0").profile.values.tolist()
        != a.agent("hh_1").profile.values.tolist()
    )
    expected = np.array(VALUES) * (
        1.0
        + 0.1
        * SeedTree(1)
        .child("agent", name_key("hh_0"), "profile", "load")
        .generator()
        .standard_normal(4)
    )
    assert a.agent("hh_0").profile.values == pytest.approx(expected)
    assert a.agent("hh_0").seen == pytest.approx(expected[:2].tolist())
    # the noise never consumes the entity stream
    clean = holder_scenario(values=VALUES, resolution="1h").run(seed=1)
    assert clean.agent("hh_0").first_draw == a.agent("hh_0").first_draw
    assert clean.agent("hh_0").profile is clean.agent("hh_1").profile
    assert noisy.run(seed=2).agent("hh_0").profile.values.tolist() != (
        a.agent("hh_0").profile.values.tolist()
    )


def test_entity_profiles_pick_columns_by_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_csv(
        tmp_path / "data" / "p.csv", pd.DataFrame({"a": [1.0, 1.0], "b": [2.0, 2.0]})
    )
    scenario = holder_scenario(path="data/p.csv", column=["a", "b"], resolution="1h")
    result = scenario.run(seed=1)
    assert [result.agent(f"hh_{i}").profile.column for i in range(3)] == ["a", "b", "a"]
    assert result.agent("hh_0").profile is result.agent("hh_2").profile


def test_ctx_profile_is_shared_and_unknown_profiles_fail():
    seen = []
    scenario = Scenario("p")
    scenario.time(horizon="1h", step="1h")
    scenario.defaults(
        {"profiles": {"h0": {"values": VALUES, "resolution": "1h", "noise": 0.5}}}
    )
    scenario.group(
        "g", 2, lambda e: (seen.append(e.ctx.profile("h0")), Holder(None, 0.0))[1]
    )
    scenario.run(seed=1)
    assert seen[0] is seen[1]
    assert seen[0].values.tolist() == VALUES  # ctx.profile is noise-free
    typo = Scenario("p")
    typo.time(horizon="1h", step="1h")
    typo.defaults({"profiles": {"load": {"values": VALUES, "resolution": "1h"}}})
    typo.group("g", 1, lambda e: Holder(e.profile("lod"), 0.0))
    with pytest.raises(
        ScenarioError, match="Unknown profile 'lod'. Did you mean 'load'"
    ):
        typo.run(seed=1)


# ----------------------------------------------------------------- relative paths


def make_project(root):
    write_csv(root / "data" / "p.csv", pd.DataFrame({"load": VALUES}))
    presets = root / "presets"
    presets.mkdir(parents=True)
    (presets / "base.yaml").write_text(
        "profiles:\n  load: {source: csv, path: ../data/p.csv, column: load, "
        "resolution: 1h}\n"
    )
    (presets / "cwd.yaml").write_text(
        "profiles:\n  load: {source: csv, path: data/p.csv, column: load, "
        "resolution: 1h}\n"
    )
    (presets / "child.yaml").write_text("extends: base.yaml\nn: 2\n")
    return root


def test_preset_relative_paths_are_rebased_to_the_project_root(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    monkeypatch.chdir(project)
    scenario = holder_scenario()
    for preset in ("presets/base.yaml", "presets/cwd.yaml", "presets/child.yaml"):
        params = load_params(scenario, preset)
        assert params.profiles["load"].path == "data/p.csv"
    assert scenario.run("presets/base.yaml", seed=1).agent("hh_0").seen == [1.0, 2.0]
    # a path that resolves against cwd is never rewritten; overrides are kept as given
    params = load_params(
        scenario, "presets/base.yaml", overrides=["profiles.load.path=x.csv"]
    )
    assert params.profiles["load"].path == "x.csv"


def test_run_id_is_unchanged_when_the_project_folder_moves(tmp_path, monkeypatch):
    first = make_project(tmp_path / "a")
    scenario = holder_scenario()
    monkeypatch.chdir(first)
    run_a = scenario.build("presets/base.yaml", seed=3)
    second = tmp_path / "elsewhere" / "b"
    shutil.copytree(first, second)
    shutil.rmtree(first)
    monkeypatch.chdir(second)
    clear_profile_cache()
    run_b = scenario.build("presets/base.yaml", seed=3)
    assert run_a.run_id == run_b.run_id
    assert scenario.run("presets/base.yaml", seed=3).agent("hh_1").seen == [1.0, 2.0]


def test_relative_paths_fall_back_to_the_study_project_root(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec = ProfileSpec(path="data/p.csv", column="load", resolution="1h")
    with pytest.raises(ScenarioError, match="not found \\(tried .*elsewhere"):
        load_profile(spec, name="load")
    _util.set_project_root(str(project))
    assert load_profile(spec, name="load").values.tolist() == VALUES
    absolute = ProfileSpec(path=str(project / "data" / "p.csv"), resolution="1h")
    assert load_profile(absolute).values.tolist() == VALUES


# ----------------------------------------------------------------- FromColumn


@pytest.fixture
def table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_csv(
        tmp_path / "data" / "hh.csv",
        pd.DataFrame({"kwh": [100.0, 200.0, 300.0], "kind": ["a", "b", "c"]}),
    )
    return "data/hh.csv"


def test_from_column_index_mode(table):
    dist = FromColumn(table, "kwh", wrap=True)  # rows are reused below on purpose
    assert dist.mode == "index" and "from_column" in distribution_kinds()
    rng = np.random.default_rng(1)
    assert dist.draw(rng, index=4) == 200.0
    assert isinstance(dist.draw(rng, index=0), float)
    assert dist.draw(rng, size=2, index=2).tolist() == [300.0, 100.0]
    assert dist.draw(rng, size=(2, 2), index=0).shape == (2, 2)
    with pytest.raises(ValueError, match="e.draw"):
        dist.draw(rng)
    with pytest.raises(TypeError, match="index"):
        dist.draw(rng, index=True)
    assert dist.expected_value == 200.0
    assert FromColumn(table, "kind").draw(rng, index=1) == "b"
    with pytest.raises(TypeError, match="not numeric"):
        FromColumn(table, "kind").expected_value  # noqa: B018


def test_from_column_sample_mode(table):
    dist = FromColumn(table, "kwh", "sample")
    draws = [dist.draw(np.random.default_rng(7)) for _ in range(2)]
    assert draws[0] == draws[1] and draws[0] in (100.0, 200.0, 300.0)
    many = dist.draw(np.random.default_rng(7), size=50)
    assert set(many.tolist()) == {100.0, 200.0, 300.0}
    assert dist.draw(random.Random(3)) in (100.0, 200.0, 300.0)
    assert dist.draw(random.Random(3), size=3).shape == (3,)
    with pytest.raises(TypeError):
        dist.draw("not an rng")
    with pytest.raises(ScenarioError, match="Did you mean 'kwh'"):
        FromColumn(table, "kw").draw(np.random.default_rng(1), index=0)


def test_from_column_as_parameter_and_entity_draws(table, monkeypatch):
    from mango.scenario import distributions

    monkeypatch.setattr(distributions, "_WRAP_WARNED", set())

    class HHParams(BaseModel, extra="forbid"):
        annual: Dist = Constant(0.0)
        peak: Uniform = Uniform(1.0, 2.0)

    class P(ScenarioParams):
        hh: HHParams = HHParams()

    params = load_params(P, {"hh": {"annual": {"from_column": [table, "kwh"]}}})
    assert params.hh.annual == FromColumn(table, "kwh")
    assert params.hh.annual.model_dump() == {
        "kind": "from_column",
        "path": table,
        "column": "kwh",
        "mode": "index",
    }
    scenario = Scenario("hh", params=P)
    scenario.time(horizon="1h", step="1h")
    scenario.group(
        "hh",
        4,
        lambda e: Holder(None, (e.draw(e.p.hh.annual), e.draw(e.p.hh.peak))),
    )
    with pytest.warns(UserWarning, match="rows are reused modulo 3"):
        result = scenario.run(params, seed=5)
    assert [result.agent(f"hh_{i}").first_draw[0] for i in range(4)] == [
        100.0,
        200.0,
        300.0,
        100.0,
    ]
    expected = Uniform(1.0, 2.0).draw(SeedTree(5).rng("agent", name_key("hh_1")))
    assert result.agent("hh_1").first_draw[1] == expected


def test_from_column_paths_in_presets_are_rebased(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    (project / "presets" / "hh.yaml").write_text(
        "hh: {annual: {from_column: [../data/p.csv, load]}}\n"
    )

    class HHParams(BaseModel, extra="forbid"):
        annual: Dist = Constant(0.0)

    class P(ScenarioParams):
        hh: HHParams = HHParams()

    monkeypatch.chdir(project)
    params = load_params(P, "presets/hh.yaml")
    assert params.hh.annual.path == "data/p.csv"
    assert read_column("data/p.csv", "load").tolist() == VALUES


# ----------------------------------------------------------------- daylight saving


def _berlin(text):
    from mango.scenario.timecfg import _parse_datetime

    return _parse_datetime(f"{text}[Europe/Berlin]")


def _write(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text("time,v\n" + "\n".join(rows) + "\n")
    return str(path)


def _all_readings(profile, start, hours):
    clock = ExternalClock(start_datetime=start)
    readings = []
    for h in hours:
        clock.set_time(h * H)
        readings.append(
            (profile.at(clock), profile.at(h * H), profile.at(clock.datetime))
        )
    return readings


def test_naive_local_times_follow_the_zoned_scenario_across_spring_forward(tmp_path):
    # local hours of 2024-03-31: 02:00 does not exist; the value is the local hour
    path = _write(
        tmp_path, "p.csv", [f"2024-03-31T{h:02d}:00,{h}" for h in (0, 1, 3, 4)]
    )
    start = _berlin("2024-03-31T00:00")
    profile = load_profile(
        {"path": path, "time_column": "time", "wrap": False},
        name="p",
        scenario_start=start,
    )
    assert profile.resolution_s == H  # elapsed, not wall-clock, spacing
    readings = _all_readings(profile, start, [0, 1, 2, 3])
    assert readings == [(v, v, v) for v in (0.0, 1.0, 3.0, 4.0)]


def test_naive_local_times_with_the_repeated_hour_of_fall_back(tmp_path):
    hours = ["00", "01", "02", "02", "03"]
    path = _write(
        tmp_path, "f.csv", [f"2024-10-27T{h}:00,{i}" for i, h in enumerate(hours)]
    )
    start = _berlin("2024-10-27T00:00")
    spec = {"path": path, "time_column": "time", "wrap": False}
    profile = load_profile(spec, name="f", scenario_start=start)
    readings = _all_readings(profile, start, range(5))
    assert readings == [(float(i),) * 3 for i in range(5)]
    with pytest.raises(ScenarioError, match="repeat an hour when clocks go back"):
        load_profile(spec, name="f", scenario_start=dt.datetime(2024, 10, 27))


def test_dst_errors_name_the_row_in_mangos_own_words(tmp_path):
    start = _berlin("2024-03-31T00:00")
    gap = _write(tmp_path, "gap.csv", ["2024-03-31T01:00,1", "2024-03-31T02:00,2"])
    spec = {"path": gap, "time_column": "time", "wrap": False}
    with pytest.raises(ScenarioError) as missing:
        load_profile(spec, name="g", scenario_start=start)
    text = str(missing.value)
    assert "contains 2024-03-31 02:00, which does not exist in Europe/Berlin" in text
    assert "'nonexistent'" not in text and "Try using" not in text
    # one 02:30 on the fall-back day cannot be ordered into either hour
    amb = _write(tmp_path, "amb.csv", ["2024-10-27T01:00,1", "2024-10-27T02:30,2"])
    spec = {"path": amb, "time_column": "time", "wrap": False}
    with pytest.raises(ScenarioError) as twice:
        load_profile(spec, name="a", scenario_start=_berlin("2024-10-27T00:00"))
    text = str(twice.value)
    assert "contains 2024-10-27 02:30, which occurs twice in Europe/Berlin" in text
    assert "2024-10-27T02:30+02:00 or 2024-10-27T02:30+01:00" in text
    assert "infer" not in text


def test_offset_qualified_times_across_a_dst_change(tmp_path):
    rows = [
        "2024-03-31T01:00+01:00,1",
        "2024-03-31T03:00+02:00,3",
        "2024-03-31T04:00+02:00,4",
    ]
    path = _write(tmp_path, "q.csv", rows)
    start = _berlin("2024-03-31T01:00")
    profile = load_profile(
        {"path": path, "time_column": "time", "wrap": False},
        name="q",
        scenario_start=start,
    )
    assert _all_readings(profile, start, [0, 1, 2]) == [
        (1.0,) * 3,
        (3.0,) * 3,
        (4.0,) * 3,
    ]


def test_a_naive_start_of_a_regular_profile_is_wall_time_of_the_scenario_zone():
    start = _berlin("2024-03-31T00:00")
    profile = load_profile(
        inline(values=range(6), start=dt.datetime(2024, 3, 31)),
        name="r",
        scenario_start=start,
    )
    assert profile.start.tzinfo is not None
    assert _all_readings(profile, start, range(4)) == [
        (float(h),) * 3 for h in range(4)
    ]


def test_from_column_rows_reused_warn_once_unless_wrap_is_set(table, monkeypatch):
    from mango.scenario import distributions

    monkeypatch.setattr(distributions, "_WRAP_WARNED", set())
    rng = np.random.default_rng(1)
    dist = FromColumn(table, "kwh")
    assert dist.wrap is None and "wrap" not in dist.model_dump()
    assert dist.draw(rng, index=2) == 300.0  # within the table: silent
    with pytest.warns(
        UserWarning, match="3 rows, but the group is larger .* wrap=True"
    ):
        assert dist.draw(rng, index=3) == 100.0
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dist.draw(rng, index=4)  # warned once per table and column
        FromColumn(table, "kwh", wrap=True).draw(rng, index=5)
    strict = FromColumn(table, "kwh", wrap=False)
    assert strict.model_dump()["wrap"] is False
    with pytest.raises(ValueError, match="group is larger than the table"):
        strict.draw(rng, index=3)
    with pytest.raises(ValueError, match="group is larger than the table"):
        strict.draw(rng, size=2, index=2)


def dated_csv(tmp_path):
    times = pd.date_range("2024-01-15T00:00", periods=24 * 7, freq="1h")
    return write_csv(
        tmp_path / "jan.csv",
        pd.DataFrame({"time": times.strftime("%Y-%m-%dT%H:%M"), "load": range(168)}),
    )


def test_dated_profiles_do_not_wrap_by_default_and_plan_warns(tmp_path):
    path = dated_csv(tmp_path)
    spec = ProfileSpec(path=str(path), column="load", time_column="time")
    assert spec.wrap is None and "wrap" not in spec.model_dump()
    july = load_profile(spec, name="load", scenario_start=dt.datetime(2023, 7, 1))
    with pytest.raises(
        ScenarioError, match="covers 2024-01-15T00:00:00 to .* at 2023-07-01"
    ):
        july.at(0.0)
    wrapping = spec.model_copy(update={"wrap": True})
    assert load_profile(
        wrapping, name="load", scenario_start=dt.datetime(2023, 7, 1)
    ).at(0.0) in range(168)
    inline = load_profile(ProfileSpec(values=[1, 2], resolution="1h"), name="x")
    assert inline.wrap is True and inline.at(7200.0) == 1.0

    class P(ScenarioParams):
        pass

    scenario = Scenario("dated", params=P)
    scenario.group("g", 1, lambda e: Plain())
    preset = {
        "time": {"start": "2023-07-01T00:00", "horizon": "3h", "step": "1h"},
        "profiles": {"load": {"path": str(path), "time_column": "time"}},
    }
    with pytest.warns(UserWarning, match="profile 'load' has data from 2024-01-15"):
        plan = scenario.plan(preset)
    assert any("outside its dates will fail" in w for w in plan.warnings)
    inside = {**preset, "time": {**preset["time"], "start": "2024-01-16T00:00"}}
    assert not scenario.plan(inside).warnings
    on_purpose = {
        **preset,
        "profiles": {"load": {**preset["profiles"]["load"], "wrap": True}},
    }
    assert not scenario.plan(on_purpose).warnings


def test_warm_up_warns_about_profiles_anchored_at_time_start():
    class P(ScenarioParams):
        pass

    scenario = Scenario("warm", params=P)
    scenario.group("g", 1, lambda e: Plain(load=e.profile("demand")))
    preset = {
        "time": {"start": "2024-03-12T06:45", "horizon": "2h15min", "step": "15min"},
        "phases": [{"name": "warmup", "until": "2024-03-12T07:00", "record": False}],
        "profiles": {"demand": {"values": [1, 2, 3, 4], "resolution": "15min"}},
    }
    with pytest.warns(UserWarning, match="'demand' has no start, so it is anchored"):
        plan = scenario.plan(preset)
    assert any("warm-up phase 'warmup'" in w for w in plan.warnings)
    with pytest.warns(UserWarning, match="anchored at time.start"):
        scenario.run(preset, seed=1)
    pinned = {
        **preset,
        "profiles": {
            "demand": {**preset["profiles"]["demand"], "start": "2024-03-12T07:00"}
        },
    }
    assert not scenario.plan(pinned).warnings
    no_warm_up = {key: value for key, value in preset.items() if key != "phases"}
    assert not scenario.plan(no_warm_up).warnings


class Plain(Agent):
    def __init__(self, **attrs):
        super().__init__()
        self.__dict__.update(attrs)

    def handle_message(self, content, meta):
        pass
