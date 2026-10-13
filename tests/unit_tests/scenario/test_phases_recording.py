"""Phases record independently, empty runs say why, run warnings and ctx.phase."""

import json
import warnings
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import RunWarning, Scenario, ScenarioError, load_run  # noqa: E402
from mango.scenario.events import describe_stop, empty_run_reason  # noqa: E402

from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

H = 3600.0

HEAT = """
from mango import Agent
from mango.scenario import Scenario


class Heater(Agent):
    def __init__(self):
        super().__init__()
        self.power_kw = 0.0

    def on_step(self, env, clock, step_size_s):
        self.power_kw = 1.0 + clock.time / 3600


scenario = Scenario("heating")
scenario.time(horizon="6h", step="1h")
heater = scenario.group("heater", 2, lambda e: Heater())
scenario.record("power_kw", heater, attr="power_kw")


@scenario.stop_when
def converged(ctx):
    return ctx.world.clock.time >= 3 * 3600 and "converged"
"""


class Heater(Agent):
    def __init__(self):
        super().__init__()
        self.power_kw = 0.0
        self.phases = []

    def on_step(self, env, clock, step_size_s):
        self.power_kw = 1.0 + clock.time / H


def heating():
    scenario = Scenario("heating")
    scenario.time(horizon="6h", step="1h")
    heater = scenario.group("heater", 2, lambda e: Heater())
    scenario.record("power_kw", heater, attr="power_kw")
    return scenario


WARMUP_THEN_PHASES = {
    "phases": [
        {"name": "warmup", "until": "2h", "record": False},
        {"name": "before", "until": "3h"},
        {"name": "closure", "until": "4h"},
        {"name": "after"},
    ]
}


def quietly(fn, *args, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = fn(*args, **kwargs)
    return value, [str(item.message) for item in caught]


# --------------------------------------------------------------------------- record per phase


def test_a_warm_up_does_not_silence_the_phases_after_it():
    scenario = heating()
    plan = scenario.plan(WARMUP_THEN_PHASES)
    assert [(p.name, p.record) for p in plan.phases] == [
        ("warmup", False),
        ("before", True),
        ("closure", True),
        ("after", True),
    ]
    result = scenario.run(WARMUP_THEN_PHASES, seed=1)
    assert list(dict.fromkeys(result.frame["phase"])) == ["before", "closure", "after"]
    assert sorted(set(result.frame["time"])) == [h * H for h in range(3, 7)]
    assert result.warnings == []


def test_plan_warns_when_no_phase_records():
    scenario = heating()
    params = {
        "phases": [
            {"name": "warmup", "until": "2h", "record": False},
            {"name": "rest", "record": False},
        ]
    }
    with pytest.warns(UserWarning, match="No phase records"):
        plan = scenario.plan(params)
    assert any("No phase records" in text for text in plan.warnings)
    result, caught = quietly(scenario.run, params, seed=1)
    assert len(result.frame) == 0
    (warning,) = result.warnings
    assert "recorded no values: no phase records" in warning
    assert any("recorded no values" in text for text in caught)


def test_the_empty_run_warning_points_at_the_users_call():
    scenario = heating()
    params = {"phases": [{"name": "rest", "record": False}]}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        scenario.run(params, seed=1)
    (empty,) = [w for w in caught if "recorded no values" in str(w.message)]
    assert empty.category is RunWarning
    assert Path(empty.filename).name == "test_phases_recording.py"


def test_a_run_without_recorders_does_not_warn():
    scenario = Scenario("bare")
    scenario.time(horizon="2h", step="1h")
    scenario.group("heater", 1, lambda e: Heater())
    result, caught = quietly(scenario.run, seed=1)
    assert result.warnings == [] and not caught
    assert result.stats["recorders"] == 0
    assert empty_run_reason(result.config, result.stats) == (
        "the scenario declares no recorders"
    )


# --------------------------------------------------------------------------- empty runs explained


def test_an_empty_run_says_it_stopped_inside_the_warm_up():
    scenario = heating()
    result, _ = quietly(scenario.run, WARMUP_THEN_PHASES, seed=1, until="1h")
    assert len(result.frame) == 0
    reason = empty_run_reason(result.config, result.stats)
    assert reason == (
        "the run stopped at 3600 s (until) inside phase 'warmup' (record: false); "
        "the first recorded phase 'before' starts at 2h"
    )
    assert result.warnings == [
        f"Run {result.run_id} of scenario 'heating' recorded no values: {reason}."
    ]
    with pytest.raises(KeyError, match="the run recorded no values \\(the run stopped"):
        result.ts("power_kw")


def test_describe_stop_names_reason_and_time():
    config = {"time": {"horizon": "1d", "step": "1h"}}
    stats = {"stopped_early": True, "stop_time_s": 54.6, "stop_reason": "converged"}
    assert describe_stop(config, stats) == "stopped early at 54.6 s (converged)"
    assert describe_stop(config, {"stopped_early": False}) is None
    calendar = {"time": {"start": "2024-01-01T00:00", "horizon": "2d", "step": "1h"}}
    stats = {"stopped_early": True, "stop_time_s": 7200.0, "stop_reason": "until"}
    assert describe_stop(calendar, stats) == (
        "stopped early at 2024-01-01T02:00 (7200 s) (until)"
    )


def test_cli_run_summary_explains_stops_and_empty_runs(capsys, isolated):
    write(isolated, "heat.py", HEAT)
    write(
        isolated,
        "presets/trap.yaml",
        """
        phases:
          - {name: warmup, until: 2h, record: false}
          - {name: rest, record: false}
        """,
    )
    code, out, err = cli(capsys, "run", "heat:scenario", "-p", "presets/trap.yaml")
    assert code == 0, err
    assert "nothing recorded: no phase records (every phase has record: false)" in out
    code, out, err = cli(capsys, "run", "heat:scenario", "--out", "res")
    assert code == 0, err
    assert "stopped early at 10800 s (converged)" in out
    assert "nothing recorded" not in out
    manifest = json.loads(Path("res/manifest.json").read_text())
    assert manifest["stats"]["recorders"] == 1


def test_validate_warns_when_no_phase_records(capsys, isolated):
    write(isolated, "heat.py", HEAT)
    write(isolated, "presets/off.yaml", "phases: [{name: idle, record: false}]\n")
    code, _, err = cli(capsys, "validate", "heat:scenario", "-p", "presets/off.yaml")
    assert code == 0
    assert "warning: No phase records" in err


def test_loaded_runs_explain_an_empty_frame(tmp_path):
    scenario = heating()
    result, _ = quietly(
        scenario.run, {"phases": [{"name": "off", "record": False}]}, seed=1
    )
    result.save(tmp_path / "run")
    loaded = load_run(tmp_path / "run")
    with pytest.raises(KeyError, match="no phase records"):
        loaded.ts("power_kw")


# --------------------------------------------------------------------------- ctx.phase, ctx.warn


class PhaseWatcher(Agent):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.seen = []

    def on_step(self, env, clock, step_size_s):
        self.seen.append((clock.time, self.ctx.phase, self.ctx.recording))


def test_ctx_phase_and_recording_follow_the_clock():
    scenario = Scenario("phases")
    scenario.time(horizon="4h", step="1h")
    scenario.group("w", 1, lambda e: PhaseWatcher(e.ctx))
    params = {"phases": [{"name": "warmup", "until": "2h", "record": False}]}
    result = scenario.run(params, seed=1)
    seen = result.agent("w").seen
    assert [phase for _, phase, _ in seen] == ["warmup", "warmup", "main", "main"]
    # a sample at t is recorded by the phase of the step that ended at t: at the
    # start of the step from 2h the sample of 2h (warm-up) is not recorded
    assert [recording for _, _, recording in seen] == [False, False, False, True]


def test_ctx_phase_needs_a_running_run():
    scenario = Scenario("phases")

    @scenario.on_build
    def probe(ctx):
        assert ctx.phase == "main"

    scenario.group("w", 1, lambda e: PhaseWatcher(e.ctx))
    scenario.run(seed=1)

    def declare(ctx):
        return ctx.phase

    other = Scenario("early")
    other._declare("build", declare)
    with pytest.raises(ScenarioError, match="ctx.phase needs a running run"):
        other.plan()


class Grumpy(Agent):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    def on_step(self, env, clock, step_size_s):
        self.ctx.warn("prices collapsed")  # every step: kept once


def test_ctx_warn_keeps_the_warning_with_the_result():
    scenario = Scenario("grumpy")
    scenario.time(horizon="3h", step="1h")
    scenario.group("g", 2, lambda e: Grumpy(e.ctx))

    def check(ctx):
        ctx.warn("declared at build time")

    scenario._declare("build", check)
    with pytest.warns(RunWarning, match="prices collapsed"):
        result = scenario.run(seed=1)
    assert result.warnings == ["declared at build time", "prices collapsed"]
    with pytest.warns(RunWarning):
        plan = scenario.plan()
    assert "declared at build time" in plan.warnings


def test_ctx_warn_needs_a_message():
    scenario = Scenario("mute")
    scenario.group("g", 1, lambda e: Heater())
    scenario.on_build(lambda ctx: ctx.warn(""))
    with pytest.raises(
        ScenarioError, match="ctx.warn\\(message\\) needs a non-empty str"
    ):
        scenario.run(seed=1)
