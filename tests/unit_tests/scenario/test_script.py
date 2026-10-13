"""Scenario.to_script: the generated plain mango script records what the scenario run records."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from pydantic import BaseModel  # noqa: E402

from mango.scenario import ParamsError, Scenario, ScenarioComponent  # noqa: E402
from mango.scenario.result import frame_checksum, tidy_frame  # noqa: E402
from mango.scenario.script import generate_script  # noqa: E402

from . import ev_tutorial  # noqa: E402
from . import script_fixtures as fx  # noqa: E402
from . import yaml_fixtures as yfx  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def run_script(source: str, folder: Path):
    """Execute a generated script in this process; return (rows, its globals)."""
    path = folder / "generated.py"
    path.write_text(source, encoding="utf-8")
    namespace = runpy.run_path(str(path), run_name="generated")
    return namespace["main"](), namespace


def checksum(rows, namespace, calendar=True):
    if "PHASES" in namespace:
        phases = [(start, name) for start, name, _ in namespace["PHASES"]]
    else:  # the lean form: one phase
        phases = [(0.0, rows[0]["phase"] if rows else "main")]
    return frame_checksum(
        tidy_frame(rows, run_id="x", phases=phases, calendar=calendar)
    )


def test_script_records_the_same_values_as_the_run(tmp_path, capsys):
    source = fx.scenario.to_script(seed=11)
    rows, namespace = run_script(source, tmp_path)
    result = fx.scenario.run(seed=11)

    assert len(rows) == len(result.frame)
    assert checksum(rows, namespace) == result.checksum()
    # the run exercises what the script must reproduce
    assert result.ts("events").max() > 0  # emits reached the agents
    assert set(result.frame["phase"]) == {"main"}  # warm-up not recorded
    assert "values recorded" in capsys.readouterr().out


def test_script_reproduces_an_event_driven_run(tmp_path):
    overrides = ["time.step=event"]
    source = fx.scenario.to_script(seed=5, overrides=overrides)
    rows, namespace = run_script(source, tmp_path)

    assert namespace["STEP_S"] is None
    assert (
        checksum(rows, namespace)
        == fx.scenario.run(seed=5, overrides=overrides).checksum()
    )


def test_script_of_the_tutorial_inlines_lambdas_and_positions(tmp_path):
    source = ev_tutorial.scenario.to_script(seed=7)
    rows, namespace = run_script(source, tmp_path)

    assert checksum(rows, namespace) == ev_tutorial.scenario.run(seed=7).checksum()
    # lambdas are written out as functions; positions are applied after the groups
    assert "def coordinator_factory(e):" in source
    assert "return CoordinatorAgent(threshold_kw=e.p.threshold_kw)" in source
    assert "world.environment.space.move(e.agent, household_position(e))" in source
    # the tutorial's function 'environment' would be shadowed by the local variable
    assert "environment as ev_tutorial_environment" in source
    assert source.lean and source.placeholders == ()
    assert "Placeholder" not in source


def test_script_writes_the_run_explicitly(tmp_path):
    source = fx.scenario.to_script(seed=11)

    assert "SEED = 11" in source
    assert "params = Params.model_validate(CONFIG)" in source
    assert "start_datetime=params.time.start" in source
    assert "PHASES = [(0.0, 'warmup', False), (3600.0, 'main', True)]" in source
    # every link as explicit edges, also random and attribute links
    assert "create_topology(tid='peers')" in source
    assert "create_topology(tid='buses')" in source
    assert "for a, b in [('node_0', 'node_2'), ('node_1', 'node_3')]:" in source
    # the comm outage switches the communication simulation
    assert "follow_communication(world, ctx, communication)" in source
    assert "(7200.0," in source and "'loss_percent': 1.0" in source
    # events with emits and actions
    assert "(10800.0, 'ping', {'kind': 'ping'}, None, None, None, None)," in source
    assert (
        "(14400.0, 'poke', {'kind': 'poke'}, ('node_0',), None, None, None)," in source
    )
    assert "boost_gains, None, None)," in source
    assert "add_world_recorder(world, 'total_received', 'world'" in source
    assert "every=3600.0" in source
    compile(source, "generated.py", "exec")


def test_generated_script_runs_as_a_program(tmp_path):
    path = tmp_path / "demo_script.py"
    path.write_text(fx.scenario.to_script(seed=3), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), *sys.path])}
    completed = subprocess.run(
        [sys.executable, str(path), str(tmp_path / "values.csv")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert "values recorded" in completed.stdout
    lines = (tmp_path / "values.csv").read_text().splitlines()
    assert lines[0].split(",")[:2] == ["time", "datetime"]
    assert len(lines) - 1 == len(fx.scenario.run(seed=3).frame)


def test_closures_over_literals_are_written_out(tmp_path):
    source = fx.closure_scenario.to_script(seed=1)

    assert source.placeholders == ()
    assert (
        "def hub_factory(e):\n    offset = 3.0\n    return Hub(boost=offset)" in source
    )
    rows, namespace = run_script(source, tmp_path)
    assert (
        checksum(rows, namespace, calendar=False)
        == fx.closure_scenario.run(seed=1).checksum()
    )


def test_closures_over_other_objects_become_marked_placeholders(tmp_path):
    source = fx.opaque_closure_scenario.to_script(seed=1)

    assert source.placeholders == ("factory of group 'hub'",)
    assert "1 PLACEHOLDER(s)" in source
    assert "  - factory of group 'hub'" in source  # listed in the docstring
    assert "# PLACEHOLDER factory of group 'hub': " in source
    assert "_opaque_closure_scenario.<locals>.<lambda>" in source
    assert "lambda e: Hub(boost=next(counter))" in source  # the source, as a comment
    assert "hub_factory_placeholder = Placeholder(\"factory of group 'hub'\")" in source
    with pytest.raises(
        NotImplementedError, match="PLACEHOLDER: factory of group 'hub'"
    ):
        run_script(source, tmp_path)


def test_kit_closures_behaviors_and_resources_are_written_out(tmp_path):
    source = yfx.ev_scenario.to_script(seed=1)

    assert "params = load_params(" in source  # kit params need the scenario's model
    assert source.placeholders == () and "# PLACEHOLDER" not in source
    # the factory closes over the kit's ctx.p: written out as params.grid
    assert "def grid_hh_factory(e):\n    p = params.grid\n" in source
    # the kit's behavior is also its resource: one object, rebuilt from its class
    assert "    feeder = Feeder()\n" in source
    assert "ctx.behavior = compose_behavior([('grid', feeder)], seeds)" in source
    assert "ctx.provide('grid.feeder', feeder)" in source
    assert "grid_ctx = ctx.scoped('grid')" in source
    rows, namespace = run_script(source, tmp_path)
    assert (
        checksum(rows, namespace, calendar=False)
        == yfx.ev_scenario.run(seed=1).checksum()
    )


def test_seed_comes_from_the_argument_then_params_then_fresh_entropy():
    assert "SEED = 4\n" in fx.scenario.to_script(seed=4)
    assert "SEED = 9\n" in fx.scenario.to_script(overrides=["seed=9"])
    fresh = fx.scenario.to_script()
    line = next(line for line in fresh.splitlines() if line.startswith("SEED = "))
    assert int(line.removeprefix("SEED = ")) > 0


def test_to_script_validates_params_and_delegates():
    with pytest.raises(ParamsError, match="node.nn"):
        fx.scenario.to_script(seed=1, overrides=["node.nn=3"])
    assert generate_script(fx.scenario, seed=2) == fx.scenario.to_script(seed=2)


world = 2.0  # a module global named like a local of the generated simulate()


def test_lambdas_using_names_the_script_needs_become_placeholders():
    scenario = Scenario("clash")
    scenario.group("hub", 1, lambda e: fx.Hub(boost=world))
    scenario.group("other", 1, lambda e: fx.Hub(boost=e.index))
    source = scenario.to_script(seed=1)

    # 'world' would be shadowed in the script: that lambda cannot be written out
    assert "PLACEHOLDER factory of group 'hub'" in source
    assert "def other_factory(e):" in source
    assert "return fx.Hub(boost=e.index)" in source


def test_attribute_links_build_the_agents_once_to_find_their_edges(monkeypatch):
    from mango.scenario import script

    calls = []
    original = script._built_state

    async def counting(*args):
        calls.append(args)
        return await original(*args)

    monkeypatch.setattr(script, "_built_state", counting)
    fx.scenario.to_script(seed=1)  # has a by_attr link
    assert len(calls) == 1
    ev_tutorial.scenario.to_script(seed=1)  # only deterministic links
    assert len(calls) == 1


def kit_load(ctx):
    return ctx.p.load * 2


class Meter(ScenarioComponent):
    class Params(BaseModel, extra="forbid"):
        load: float = 1.0

    def record(self, ctx):
        ctx.record_world("feeder_kw", kit_load)


two_meters = Scenario("two_meters")
two_meters.time(horizon="2h", step="1h")
two_meters.use(Meter(), prefix="north")
two_meters.use(Meter(load=3.0), prefix="south")


def test_script_of_one_kit_under_two_prefixes_keeps_both_world_variables(tmp_path):
    source = two_meters.to_script(seed=2)
    assert "key='south/feeder_kw'" in source
    rows, namespace = run_script(source, tmp_path)
    assert (
        checksum(rows, namespace, calendar=False) == two_meters.run(seed=2).checksum()
    )
    assert {(row["entity"], row["variable"]) for row in rows} == {
        ("north", "feeder_kw"),
        ("south", "feeder_kw"),
    }


def test_local_functions_closing_over_ctx_and_params_are_written_out(tmp_path):
    source = fx.fleet_scenario.to_script(seed=4)

    assert source.placeholders == ()
    # the local def keeps its source; its closure variables come first
    assert (
        "def north_node_factory(e):\n"
        '    """Nodes with a gain drawn from the kit\'s parameters."""\n'
        "    ctx = SHARED['north_ctx']\n"
        "    p = params.north\n"
    ) in source
    assert "meter = Meter(scale=2.0, settings=params.north)" in source
    assert "    meter = SHARED['meter']\n    return meter.scale" in source
    assert "SHARED.update(north_ctx=north_ctx, meter=meter)" in source
    rows, namespace = run_script(source, tmp_path)
    assert (
        checksum(rows, namespace, calendar=False)
        == fx.fleet_scenario.run(seed=4).checksum()
    )


def test_objects_changed_after_construction_become_placeholders():
    source = fx.tally_scenario.to_script(seed=1)
    assert source.placeholders == ("resource 't.tally' (provided by 't')",)


def test_lean_script_with_profiles_and_sampling_periods(tmp_path):
    source = fx.lean_scenario.to_script(seed=8)

    assert source.lean and source.placeholders == ()
    for machinery in (
        "PHASES",
        "EVENTS",
        "COMMUNICATION",
        "class Placeholder",
        "scoped",
    ):
        assert machinery not in source
    assert "def due(t, every):" in source  # value is sampled every hour
    assert "def profile(self, name, key=None, entity=None):" in source
    rows, namespace = run_script(source, tmp_path)
    assert (
        checksum(rows, namespace, calendar=False)
        == fx.lean_scenario.run(seed=8).checksum()
    )
    full = fx.lean_scenario.to_script(seed=8, lean=False)
    assert not full.lean and "PHASES = " in full


# ---------------------------------------- reduced and (agent, ctx) recorders


def _person_factory(e):
    from mango import Agent

    class _Person(Agent):
        def __init__(self, index):
            super().__init__()
            self.index, self.k = index, 0
            self.state, self.q = "S", 0.0

        def on_step(self, env, clock, step_size_s):
            self.k += 1
            self.state = "SEIR"[(self.k + self.index) % 4] if self.k > 2 else "S"
            self.q = float((self.k * (self.index + 1)) % 7)
            here = env.space.location(self)
            env.space.move(self, type(here)(here.x + 0.5, here.y))

    return _Person(e.index)


def split_states(values):
    return {"infectious": sum(v in "EI" for v in values), "other": len(values)}


def _reduced_scenario():
    from mango import Area2D, DefaultEnvironment, Position2D

    scenario = Scenario("reduced")
    scenario.time(horizon="3min", step="5s")
    people = scenario.group(
        "person", 5, _person_factory, position=lambda e: Position2D(e.index, 1.0)
    )
    visitors = scenario.group(
        "visitor", 2, _person_factory, position=lambda e: Position2D(e.index, 5.0)
    )
    scenario.environment(lambda ctx: DefaultEnvironment(space=Area2D(100, 100)))
    scenario.record("q_mixed", people, attr="q")  # plain on one group,
    scenario.record("q_mixed", visitors, attr="q", every="1min", window="max")
    scenario.record("state", people, attr="state", aggregate="count")
    scenario.record(
        "state_peak",
        people,
        attr="state",
        aggregate="count",
        every="1min",
        window="max",
    )
    scenario.record("infected", people, lambda a: a.state == "I", aggregate="sum")
    scenario.record("q_mean", people, attr="q", aggregate="mean", every="30s")
    scenario.record("split", people, attr="state", aggregate=split_states)
    scenario.record("q_window", people, attr="q", every="1min", window="mean")
    scenario.record("x", people, lambda a, ctx: ctx.space.location(a).x)
    scenario.record_world(
        "total_q",
        lambda ctx: sum(a.q for a in ctx.agents("person")),
        every="1min",
        window="sum",
    )
    return scenario


reduced_scenario = _reduced_scenario()
WARM_UP = {"phases": [{"name": "warmup", "until": "30s", "record": False}]}


@pytest.mark.parametrize(
    ("params", "overrides", "lean"),
    [
        (None, None, True),  # lean form
        (None, None, False),
        (WARM_UP, None, False),  # phases gate the samples and the windows
        (WARM_UP, ["time.step=event"], False),
    ],
)
def test_scripts_reduce_like_the_run(tmp_path, params, overrides, lean):
    script = reduced_scenario.to_script(params, seed=3, overrides=overrides, lean=lean)
    assert script.lean is lean and script.placeholders == ()
    rows, namespace = run_script(script, tmp_path)
    run = reduced_scenario.run(params, seed=3, overrides=overrides)
    assert set(run.frame["variable"]) >= {"state", "split", "x", "total_q"}
    assert checksum(rows, namespace, calendar=False) == run.checksum()
