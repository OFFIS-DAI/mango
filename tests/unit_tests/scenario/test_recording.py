"""Recorder reductions (C14), event logs and metrics (C15), compact tidy frames (C38)."""

import textwrap
import warnings

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioError,
    Study,
    load_run,
    load_scenario,
    load_study,
)
from mango.scenario.result import (  # noqa: E402
    assemble_frame,
    frame_checksum,
    recording_series,
    tidy_frame,
)
from mango.simulation import recordings_to_records  # noqa: E402

from .cli_helpers import cli, isolated_fixture  # noqa: E402, F401 (fixture)

REF = "tests.unit_tests.scenario.test_recording"
FORMATS = ["csv", "parquet"] if pytest.importorskip("pyarrow") else ["csv"]
LABELS = ["run_id", "phase", "group", "entity", "variable"]


class Person(Agent):
    """A deterministic state cycle and a sawtooth queue (peak mid-minute, 0 at marks)."""

    def __init__(self, index, ctx=None):
        super().__init__()
        self.index = index
        self.ctx = ctx
        self.k = 0
        self.state = "S"
        self.q = 0.0

    def on_step(self, env, clock, step_size_s):
        self.k += 1
        self.state = "SEIR"[(self.k + self.index) % 4] if self.k > 2 else "S"
        self.q = float((self.k % 12) * (self.k % 12 < 6))
        if self.ctx is not None and self.state == "I":
            self.ctx.log("infections", who=self.aid, k=self.k, first=self.k < 6)


def people(n=4, *, horizon="3min", step="5s", ctx=False):
    scenario = Scenario("people")
    scenario.time(horizon=horizon, step=step)
    group = scenario.group(
        "person", n, lambda e: Person(e.index, e.ctx if ctx else None)
    )
    return scenario, group


def per_minute(series, how):
    """Post-hoc reduction of an every-step series over (t - 60, t]."""
    window = np.ceil(np.asarray(series.index, float) / 60 - 1e-9) * 60
    return series.groupby(window).agg(how)


# --------------------------------------------------------------------- C14 aggregate


def test_count_gives_one_row_per_category_and_group_zero_filled():
    scenario, group = people()
    scenario.record("state", group, attr="state", aggregate="count")
    scenario.record("state_raw", group, attr="state")
    result = scenario.run(seed=1)
    table = result.wide("state")
    raw = result.frame[result.frame["variable"] == "state_raw"]
    expected = (
        raw.groupby(["time", "value"], observed=True).size().unstack(fill_value=0)
    )
    assert sorted(table.columns) == ["E", "I", "R", "S"]
    assert table.loc[0.0].to_dict() == {"S": 4.0, "R": 0.0, "E": 0.0, "I": 0.0}
    assert (table[expected.columns].values == expected.values).all()
    rows = result.frame[result.frame["variable"] == "state"]
    assert set(rows["group"]) == {"person"}
    assert table.sum(axis=1).eq(4).all()


def test_sum_mean_min_max_and_functions_reduce_each_group():
    scenario, group = people()
    scenario.record("infected", group, lambda a: a.state == "I", aggregate="sum")
    scenario.record("q_mean", group, attr="q", aggregate="mean")
    scenario.record("q_min", group, attr="q", aggregate="min")
    scenario.record("q_max", group, attr="q", aggregate="max")
    scenario.record("q_spread", group, attr="q", aggregate=lambda v: max(v) - min(v))
    scenario.record(
        "q_split", group, attr="q", aggregate=lambda v: {"low": min(v), "high": max(v)}
    )
    scenario.record("q", group, attr="q")
    scenario.record("state", group, attr="state")
    result = scenario.run(seed=1)
    q = result.wide("q")
    state = result.wide("state")
    assert result.ts("infected").tolist() == (state == "I").sum(axis=1).tolist()
    assert result.ts("q_mean").tolist() == pytest.approx(q.mean(axis=1).tolist())
    assert result.ts("q_min").tolist() == q.min(axis=1).tolist()
    assert result.ts("q_max").tolist() == q.max(axis=1).tolist()
    spread = (q.max(axis=1) - q.min(axis=1)).tolist()
    assert result.ts("q_spread").tolist() == spread
    assert result.wide("q_split").columns.tolist() == ["low", "high"]
    rows = result.frame[result.frame["variable"] == "q_mean"]
    assert set(rows["entity"]) == {"person"} and set(rows["group"]) == {"person"}


def test_aggregate_over_several_groups_keeps_groups_apart():
    scenario = Scenario("two")
    scenario.time(horizon="1min", step="5s")
    red = scenario.group("red", 2, lambda e: Person(e.index))
    blue = scenario.group("blue", 3, lambda e: Person(e.index + 1))
    scenario.record("n", [red, blue], lambda a: 1, aggregate="sum")
    scenario.record("state", [red, blue], attr="state", aggregate="count")
    result = scenario.run(seed=1)
    assert result.wide("n").iloc[0].to_dict() == {"red": 2.0, "blue": 3.0}
    with pytest.raises(KeyError, match="in several groups.*pass group="):
        result.wide("state")
    assert result.wide("state", group="blue").sum(axis=1).eq(3).all()


def test_aggregate_errors_are_actionable():
    scenario, group = people()
    with pytest.raises(ScenarioError, match="unknown aggregate 'summ'.*'sum'"):
        scenario.record("x", group, attr="q", aggregate="summ")
    with pytest.raises(ScenarioError, match="window='max' .* needs every="):
        scenario.record("x", group, attr="q", window="max")
    with pytest.raises(ScenarioError, match="unknown window 'maximum'"):
        scenario.record("x", group, attr="q", every="1min", window="maximum")
    with pytest.raises(TypeError, match="aggregate must be one of"):
        scenario.record("x", group, attr="q", aggregate=3)
    scenario.record("state", group, attr="state", aggregate="sum")
    with pytest.raises(
        TypeError, match="'S' of entity 'person_0' is not a number.*count"
    ):
        scenario.run(seed=1)


def test_one_variable_cannot_mix_per_entity_and_aggregated_rows():
    scenario = Scenario("mix")
    scenario.time(horizon="1min", step="5s")
    red = scenario.group("red", 2, lambda e: Person(e.index))
    blue = scenario.group("blue", 2, lambda e: Person(e.index))
    scenario.record("q", red, attr="q")
    scenario.record("q", blue, attr="q", aggregate="sum")
    with pytest.raises(ScenarioError, match="different aggregate="):
        scenario.plan()


# --------------------------------------------------------------------- C14 window


@pytest.mark.parametrize("how", ["max", "min", "mean", "sum", "last"])
def test_window_reduces_the_samples_of_t_minus_every_to_t(how):
    scenario, group = people()
    scenario.record("q", group, attr="q")
    scenario.record("q_w", group, attr="q", every="1min", window=how)
    result = scenario.run(seed=1)
    for entity in ("person_0", "person_3"):
        windowed = result.ts("q_w", entity=entity)
        expected = per_minute(result.ts("q", entity=entity), how)
        assert windowed.index.tolist() == [0.0, 60.0, 120.0, 180.0]
        assert windowed.tolist() == pytest.approx(expected.tolist())
    if how == "max":  # the snapshot misses the mid-minute peak
        assert result.ts("q_w", entity="person_0").tolist() == [0.0, 5.0, 5.0, 5.0]


def test_window_on_aggregates_and_world_recorders():
    scenario, group = people()
    scenario.record("infected", group, lambda a: a.state == "I", aggregate="sum")
    scenario.record(
        "infected_peak",
        group,
        lambda a: a.state == "I",
        aggregate="sum",
        every="1min",
        window="max",
    )
    scenario.record(
        "state_mean",
        group,
        attr="state",
        aggregate="count",
        every="1min",
        window="mean",
    )
    scenario.record_world("steps", lambda: 1, every="1min", window="sum")
    result = scenario.run(seed=1)
    assert (
        result.ts("infected_peak").tolist()
        == per_minute(result.ts("infected"), "max").tolist()
    )
    assert result.ts("steps").tolist() == [1.0, 12.0, 12.0, 12.0]
    means = result.wide("state_mean")
    assert means.sum(axis=1).tolist() == pytest.approx([4.0] * 4)
    # E first appears at 15 s: it counts 0 at the samples 5 s and 10 s of its
    # window (without zero-fill the mean would be 1.0)
    assert means.loc[60.0, "E"] == pytest.approx(10 / 12)


def test_window_skips_samples_of_unrecorded_phases():
    scenario, group = people(horizon="4min")
    scenario.record("q", group, attr="q")
    scenario.record("q_sum", group, attr="q", every="1min", window="sum")
    params = {"phases": [{"name": "warmup", "until": "1min", "record": False}]}
    result = scenario.run(params, seed=1)
    windowed = result.ts("q_sum", entity="person_0")
    assert windowed.index.tolist() == [120.0, 180.0, 240.0]
    q = result.ts("q", entity="person_0")
    assert windowed.tolist() == per_minute(q, "sum").loc[[120.0, 180.0, 240.0]].tolist()


def test_window_mean_is_weighted_by_step_length_in_event_mode():
    class Ramp(Agent):
        value = 0.0

        def on_ready(self):
            self.schedule_timestamp_task(self.set_to(2.0), 30.0)
            self.schedule_timestamp_task(self.set_to(6.0), 45.0)

        async def set_to(self, value):
            self.value = value

    scenario = Scenario("ramp")
    scenario.time(horizon="2min", step="event")
    group = scenario.group("ramp", 1, lambda e: Ramp())
    scenario.record("v", group, attr="value")
    scenario.record("v_mean", group, attr="value", every="1min", window="mean")
    result = scenario.run(seed=1)
    assert result.ts("v").index.tolist() == [0.0, 30.0, 45.0, 60.0, 120.0]
    # (0, 60]: 2 over 30 s, 6 over 15 s, 6 over 15 s (a row covers the interval
    # before it, as in integrate()); the unweighted mean would be 14/3
    assert result.ts("v_mean").tolist() == pytest.approx([0.0, 4.0, 6.0])
    assert result.ts("v_mean").iloc[1] * 60 == pytest.approx(
        result.integrate("v", kind="flow", unit="1s") - 6.0 * 60
    )


def test_reductions_show_in_plan_and_fingerprint_and_to_script():
    scenario, group = people()
    scenario.record("q", group, attr="q")
    before = scenario.fingerprint()
    scenario.record("state", group, attr="state", aggregate="count")
    scenario.record("q_max", group, attr="q", every="1min", window="max")
    assert scenario.fingerprint() != before
    text = str(scenario.plan())
    assert "(count)" in text and "every 1min (max per window)" in text
    record = [r for r in scenario.plan().recorders if r.name == "state"][0]
    assert (record.aggregate, record.window) == ("count", None)
    script = scenario.to_script(seed=1)
    assert script.placeholders == ()  # written out (see test_script.py)
    assert "add_reduced_recorder(world, ctx, ctx.reduced, 'state', 'count', " in script


def test_scenario_files_declare_reductions_and_metrics(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            time: {{horizon: 3min, step: 5s}}
            groups:
              person: {{factory: "{REF}:make_person", count: 4}}
            record:
              - {{name: state, group: person, attr: state, aggregate: count}}
              - {{name: spread, group: [person], attr: q, aggregate: "{REF}:spread"}}
              - {{name: q_max, group: person, attr: q, every: 1min, window: max}}
              - {{name: steps, fn: "{REF}:one", every: 1min, window: sum}}
            metrics:
              final_infected: "{REF}:final_infected"
            """
        )
    )
    scenario = load_scenario(path)
    result = scenario.run(seed=1)
    assert sorted(result.wide("state").columns) == ["E", "I", "R", "S"]
    assert result.ts("q_max", entity="person_0").tolist() == [0.0, 5.0, 5.0, 5.0]
    assert result.ts("steps").tolist() == [1.0, 12.0, 12.0, 12.0]
    assert "spread" in result.variables()
    assert result.metrics == {"final_infected": 1}
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        path.read_text().replace(
            "- {name: steps, fn", "- {name: steps, aggregate: sum, fn"
        )
    )
    with pytest.raises(Exception, match="aggregate reduces across the agents"):
        load_scenario(bad)


def make_person(e):
    return Person(e.index)


def spread(values):
    return max(values) - min(values)


def one():
    return 1


def final_infected(ctx):
    return sum(a.state == "I" for a in ctx.agents("person"))


# --------------------------------------------------------------------- C15 logs


def test_logged_rows_form_a_table_with_time_phase_and_fields():
    scenario, _ = people(ctx=True)
    result = scenario.run(seed=1)
    log = result.log("infections")
    assert log.columns.tolist() == ["run_id", "time", "phase", "who", "k", "first"]
    assert log["k"].dtype == "int64" and log["first"].dtype == bool
    first = log.iloc[0]
    assert (first["time"], first["who"], first["k"]) == (10.0, "person_3", 3)
    assert set(log["phase"]) == {"main"}
    assert log["time"].is_monotonic_increasing
    with pytest.raises(KeyError, match="No event log 'infection'.*'infections'"):
        result.log("infection")


def test_log_fields_types_missing_values_and_errors():
    class Trader(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        def on_step(self, env, clock, step_size_s):
            t = clock.time
            if t == 0:
                self.ctx.log("tape", price=1, side="buy", meta={"a": 1})
            elif t == 60:
                self.ctx.log("tape", price=2.5, qty=3, flag=None)

    scenario = Scenario("tape")
    scenario.time(horizon="3min", step="1min")
    scenario.group("trader", 1, lambda e: Trader(e.ctx))
    result = scenario.run(seed=1)
    tape = result.log("tape")
    assert tape.columns.tolist() == [
        "run_id",
        "time",
        "phase",
        "price",
        "side",
        "meta",
        "qty",
        "flag",
    ]
    assert tape["price"].tolist() == [1.0, 2.5]
    assert tape["meta"].tolist()[0] == '{"a":1}'
    assert pd.isna(tape["side"].iloc[1]) and pd.isna(tape["qty"].iloc[0])


class Logger(Agent):
    """Logs ``row`` (name, fields) in every step."""

    def __init__(self, ctx, table, /, **fields):
        super().__init__()
        self.ctx, self.table, self.fields = ctx, table, fields

    def on_step(self, env, clock, step_size_s):
        self.ctx.log(self.table, **self.fields)


def logging_scenario(table, /, **fields):
    scenario = Scenario("logging")
    scenario.time(horizon="1min", step="1min")
    scenario.group("logger", 1, lambda e: Logger(e.ctx, table, **fields))
    return scenario


def test_log_rejects_reserved_fields_file_names_and_calls_outside_a_run():
    with pytest.raises(ScenarioError, match="'time' is a column every log row gets"):
        logging_scenario("tape", time=1).run(seed=1)
    with pytest.raises(ScenarioError, match="names a file"):
        logging_scenario("../escape", x=1).run(seed=1)
    # positional-only name: a field may be called "name"
    assert logging_scenario("tape", name="n").run(seed=1).log("tape")[
        "name"
    ].tolist() == ["n"]
    from mango.scenario import ScenarioComponent as Kit

    class Early(Kit):
        def build(self, ctx):
            ctx.log("tape", x=1)

    scenario = Scenario("early")
    scenario.use(Early(), prefix="early")
    with pytest.raises(ScenarioError, match=r"ctx.log\(\) needs a running run"):
        scenario.plan()


def test_rows_logged_in_unrecorded_phases_are_dropped():
    scenario, _ = people(ctx=True, horizon="4min")
    params = {"phases": [{"name": "warmup", "until": "1min", "record": False}]}
    result = scenario.run(params, seed=1)
    log = result.log("infections")
    assert log["time"].min() >= 60.0
    assert set(log["phase"]) == {"main"}


@pytest.mark.parametrize("fmt", FORMATS)
def test_logs_and_metrics_round_trip_and_enter_the_checksum(tmp_path, fmt):
    scenario, group = people(ctx=True)
    scenario.metric("final_infected", final_infected)
    result = scenario.run(seed=1)
    assert result.metrics == {"final_infected": 1}
    folder = result.save(tmp_path / "run", format=fmt)
    assert (folder / "logs" / f"infections.{fmt}").exists()
    assert (folder / "metrics.json").exists()
    assert result.manifest["logs"]["infections"] == {
        "rows": len(result.log("infections")),
        "columns": {"who": "str", "k": "int64", "first": "bool"},
    }
    again = load_run(folder)
    pd.testing.assert_frame_equal(again.log("infections"), result.log("infections"))
    assert again.metrics == result.metrics
    assert again.checksum() == result.checksum() == result.manifest["checksum"]
    assert result.checksum() != frame_checksum(result.frame)
    result.metrics["final_infected"] = 2
    assert result.checksum() != again.checksum()
    # saving a run without logs into the folder removes the old tables
    plain, _ = people()
    plain.run(seed=1).save(folder, format=fmt)
    assert not (folder / "logs").exists() and not (folder / "metrics.json").exists()
    reloaded = load_run(folder)
    assert reloaded.logs == {} and reloaded.metrics == {}
    assert reloaded.checksum() == frame_checksum(reloaded.frame)


def test_set_metric_in_kits_is_qualified_and_values_are_checked():
    class Node(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        def on_step(self, env, clock, step_size_s):
            if clock.time == 120:
                self.ctx.set_metric("t_converged", clock.time)
                self.ctx.set_metric("history", [1, 2])

    scenario = Scenario("conv")
    scenario.time(horizon="5min", step="1min")
    scenario.group("node", 1, lambda e: Node(e.ctx))
    scenario.metric("nodes", lambda ctx: len(ctx.agents("node")))
    assert scenario.run(seed=1).metrics == {
        "t_converged": 120.0,
        "history": [1, 2],
        "nodes": 1,
    }

    class Bad(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        def on_step(self, env, clock, step_size_s):
            self.ctx.set_metric("nodes", 3)

    clash = Scenario("clash")
    clash.time(horizon="1min", step="1min")
    clash.group("bad", 1, lambda e: Bad(e.ctx))
    clash.metric("nodes", lambda: 1)
    with pytest.raises(ScenarioError, match="computed by scenario.metric"):
        clash.run(seed=1)
    with pytest.raises(ScenarioError, match="declared twice"):
        clash.metric("nodes", lambda: 2)
        clash.plan()

    class Odd(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        def on_step(self, env, clock, step_size_s):
            self.ctx.set_metric("odd", object())

    odd = Scenario("odd")
    odd.time(horizon="1min", step="1min")
    odd.group("odd", 1, lambda e: Odd(e.ctx))
    with pytest.raises(ScenarioError, match="Metric 'odd': object values"):
        odd.run(seed=1)


def test_kit_metrics_and_logs_are_qualified_by_the_prefix():
    from mango.scenario import ScenarioComponent as Kit

    class Counter(Kit):
        def build(self, ctx):
            ctx.metric("size", lambda ctx: 3)
            ctx.on_build(lambda c: None)

            def hook(c):
                c.set_metric("built", True)

            ctx.on_build(hook)

    scenario = Scenario("kits")
    scenario.time(horizon="1min", step="1min")
    scenario.use(Counter(), prefix="north")
    scenario.use(Counter(), prefix="south")
    metrics = scenario.run(seed=1).metrics
    assert metrics == {
        "north.built": True,
        "south.built": True,
        "north.size": 3,
        "south.size": 3,
    }


# --------------------------------------------------------------------- C15 studies


def study_scenario():
    scenario = Scenario("logged")
    scenario.time(horizon="3min", step="5s")
    scenario.group("person", 4, lambda e: Person(e.index, e.ctx))
    scenario.record("q", "person", attr="q")
    scenario.record("state", "person", attr="state", aggregate="count")
    scenario.metric("final_infected", final_infected)
    return scenario


#: module level, so studies record an importable reference (rerun loads it)
logged = study_scenario()


def infected_share(run):
    return run.metrics["final_infected"] / 4


def test_load_study_has_metric_columns_and_frame_logs(tmp_path):
    study = Study(
        study_scenario(),
        levers={"comm.loss_percent": [0.0, 0.5]},
        replications=1,
        seed=3,
        outcomes=[infected_share],
        out=tmp_path,
    )
    report = study.run(progress=False)
    assert report.done == 2
    df = load_study(tmp_path)
    assert df["final_infected"].tolist() == [1, 1]
    assert df["infected_share"].tolist() == [0.25, 0.25]
    logs = df.log("infections")
    assert set(logs["run_id"]) == set(df["run_id"])
    assert isinstance(logs["run_id"].dtype, pd.CategoricalDtype)
    with pytest.raises(KeyError, match="No event log 'infection'.*'infections'"):
        df.log("infection")
    # outcomes that read metrics can be recomputed from disk
    again = load_study(tmp_path, outcomes=[infected_share])
    assert again["infected_share"].tolist() == [0.25, 0.25]
    series = df.timeseries(variables=["state"])
    assert set(series["variable"]) == {"state"}
    assert isinstance(series["entity"].dtype, pd.CategoricalDtype)
    with pytest.raises(KeyError, match=r"No variable\(s\) \['stat'\].*'q', 'state'"):
        df.timeseries(variables=["stat"])


def test_rerun_names_changed_metrics_and_logs(isolated, tmp_path, capsys):
    import json

    Study(logged, replications=1, seed=3, out=tmp_path).run(progress=False)
    code, out, _ = cli(capsys, "rerun", tmp_path, "0")
    assert code == 0 and out.startswith("identical")
    folder = next((tmp_path / "runs").iterdir())
    # as if the stored run had computed another value
    (folder / "metrics.json").write_text(json.dumps({"final_infected": 5}))
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["checksum"] = load_run(folder).checksum()
    (folder / "manifest.json").write_text(json.dumps(manifest))
    code, out, _ = cli(capsys, "rerun", tmp_path, "0")
    assert code == 1
    assert "metric final_infected: stored 5, replayed 1" in out
    assert "values are equal" not in out


def test_a_metric_named_like_an_outcome_gets_a_prefixed_column(tmp_path):
    def final_infected_outcome(run):
        return 0

    study = Study(
        study_scenario(),
        replications=1,
        seed=3,
        outcomes={"final_infected": final_infected_outcome},
        out=tmp_path,
    )
    study.run(progress=False)
    with pytest.warns(UserWarning, match="column 'metric:final_infected'"):
        df = load_study(tmp_path)
    assert df["final_infected"].tolist() == [0]
    assert df["metric:final_infected"].tolist() == [1]


# --------------------------------------------------------------------- C38 compact frames


def gauges(n, steps, phases=False, start=None):
    class Gauge(Agent):
        def __init__(self, i):
            super().__init__()
            self.a, self.b, self.word = float(i), 0, "x"

        def on_step(self, env, clock, step_size_s):
            self.a += 0.25
            self.b += 1
            self.word = "y" if self.b % 2 else "x"

    scenario = Scenario("gauges")
    scenario.time(horizon=steps * 60, step=60, **({"start": start} if start else {}))
    group = scenario.group("g", n, lambda e: Gauge(e.index))
    scenario.record("a", group, attr="a")
    scenario.record("b", group, attr="b")
    scenario.record_world("total", lambda ctx: sum(x.a for x in ctx.agents("g")))
    if phases:
        scenario.record("word", group, attr="word")
    return scenario


def test_label_columns_are_categoricals_in_order_of_appearance():
    result = gauges(3, 4).run(seed=1)
    frame = result.frame
    for name in LABELS:
        assert isinstance(frame[name].dtype, pd.CategoricalDtype), name
    assert frame["variable"].cat.categories.tolist() == ["total", "a", "b"]
    assert frame["entity"].cat.categories.tolist() == ["world", "g_0", "g_1", "g_2"]
    assert result.variables() == ["total", "a", "b"]


@pytest.mark.parametrize("phases", [False, True])
def test_the_columnar_frame_equals_the_row_wise_tidy_frame(phases):
    start = "2024-03-30T22:00+01:00" if phases else None
    params = (
        {"phases": [{"name": "warmup", "until": "3min"}, {"name": "eval"}]}
        if phases
        else None
    )
    scenario = gauges(3, 6, phases=phases, start=start)
    run = scenario.build(params, seed=1)
    frames = {}

    async def run_and_assemble():
        async with run:
            await run.run()
            # the raw recordings, before a finished run releases them
            world = run.world
            groups = dict(run._state.entity_groups)
            clock = world.clock
            calendar = getattr(clock, "has_calendar", False)
            frames["rowwise"] = tidy_frame(
                recordings_to_records(world, groups=groups),
                run_id=run.run_id,
                phases=run._phase_log,
                calendar=calendar,
            )
            frames["columnar"] = assemble_frame(
                recording_series(world, groups),
                run_id=run.run_id,
                phases=run._phase_log,
                to_datetime=clock.to_datetime if calendar else None,
            )

    from mango.scenario.runtime import run_sync as sync

    sync(run_and_assemble)
    result = run.result()
    columnar = frames["columnar"]
    pd.testing.assert_frame_equal(columnar, frames["rowwise"])
    pd.testing.assert_frame_equal(columnar, result.frame)
    recordings = result.world.data_agent_collections.values()
    assert not any(recording.timeseries for recording in recordings)  # released
    if phases:
        assert set(columnar["phase"]) == {"warmup", "eval"}
        assert "datetime" in columnar.columns


def run_sync(run):
    from mango.scenario.runtime import run_sync as sync

    return sync(run.execute)


@pytest.mark.parametrize("fmt", FORMATS)
def test_saved_and_loaded_frames_are_identical(tmp_path, fmt):
    result = gauges(3, 5, phases=True).run(seed=1)
    folder = result.save(tmp_path / fmt, format=fmt)
    again = load_run(folder)
    pd.testing.assert_frame_equal(again.frame, result.frame)
    assert again.checksum() == result.checksum()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # pandas 2: categorical groupby/integrate
        assert again.integrate("a", kind="flow", per_entity=True).index.tolist() == [
            "g_0",
            "g_1",
            "g_2",
        ]
        again.frame.groupby("entity", observed=True)["time"].count()


@pytest.mark.parametrize("fmt", FORMATS)
def test_run_folders_with_plain_string_columns_still_load(tmp_path, fmt):
    result = gauges(2, 3).run(seed=1)
    folder = result.save(tmp_path / fmt, format=fmt)
    stored = result.frame.copy()
    for name in LABELS:  # the layout before categoricals
        stored[name] = stored[name].astype(object)
    target = folder / f"timeseries.{fmt}"
    if fmt == "parquet":
        stored.to_parquet(target, index=False)
    else:
        stored.to_csv(target, index=False)
    again = load_run(folder)
    pd.testing.assert_frame_equal(again.frame, result.frame)
    assert again.checksum() == result.manifest["checksum"]


def test_the_frame_costs_few_bytes_per_row():
    result = gauges(50, 200).run(seed=1)
    frame = result.frame
    per_row = frame.memory_usage(deep=True).sum() / len(frame)
    assert len(frame) == 201 * (1 + 2 * 50)
    assert per_row < 30, per_row  # was ~80 with string columns


# ------------------------------------------------- recorders that take (agent, ctx)


class _Walker(Agent):
    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        here = env.space.location(self)
        env.space.move(self, type(here)(here.x + 1.0, here.y))


def test_a_plain_scenario_records_positions_through_ctx_space():
    from mango import Area2D, DefaultEnvironment, Position2D

    scenario = Scenario("walk")
    scenario.time(horizon=2, step=1)
    walkers = scenario.group(
        "walker", 2, lambda e: _Walker(), position=lambda e: Position2D(e.index, 0.0)
    )
    scenario.environment(lambda ctx: DefaultEnvironment(space=Area2D(10, 10)))
    scenario.record("x", walkers, lambda a, ctx: ctx.space.location(a).x)
    scenario.record("y", walkers, lambda agent, ctx: ctx.space.location(agent).y)
    scenario.record("steps", walkers, lambda a: 1)  # one argument: the agent only
    run = scenario.run(seed=1)
    assert run.wide("x").to_dict("list") == {
        "walker_0": [0.0, 1.0, 2.0],
        "walker_1": [1.0, 2.0, 3.0],
    }
    assert set(run.ts("y")) == {0.0}


def test_recorders_without_a_signature_get_the_agent_only():
    import operator

    from mango.scenario.context import recorder_takes_ctx

    assert not recorder_takes_ctx(operator.attrgetter("x"))
    assert not recorder_takes_ctx(lambda a: a)
    assert recorder_takes_ctx(lambda a, ctx: a)
    assert recorder_takes_ctx(lambda *args: args)
    assert not recorder_takes_ctx(lambda a, *, ctx=None: a)


class Mixed(Agent):
    """Records a number first, then text: the series falls back to a list."""

    def __init__(self):
        super().__init__()
        self.level = 0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.level = 3 if clock.time < 2 else "high"


def test_numbers_then_other_values_keep_every_value():
    scenario = Scenario("mixed")
    scenario.time(horizon=4, step=1)
    scenario.record("level", scenario.group("m", 1, lambda e: Mixed()), attr="level")
    values = scenario.run(seed=1).ts("level").tolist()
    assert values == [0.0, 3.0, 3.0, "high", "high"]


class Meter(Agent):
    def __init__(self, i):
        super().__init__()
        self.i = i
        self.a = self.b = 0.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.a = clock.time + self.i
        self.b = self.a / 2


def test_recording_peak_memory_per_row_stays_small():
    import gc
    import tracemalloc

    scenario = Scenario("memory")
    scenario.time(start="2024-06-01T00:00", horizon=500 * 300, step="5min")
    group = scenario.group("meter", 100, lambda e: Meter(e.index))
    scenario.record("a", group, attr="a")
    scenario.record("b", group, attr="b")
    scenario.run(seed=1, until=600)  # warm caches and lazy imports
    gc.collect()
    tracemalloc.start()
    try:
        result = scenario.run(seed=1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    rows = len(result.frame)
    assert rows == 100 * 2 * 501
    # values in float arrays (8 B), the frame built without copies (~30 B):
    # about 55 B per row at the peak (it was ~150 B with lists of floats)
    assert peak / rows < 90, f"{peak / rows:.0f} B per row at the peak"
