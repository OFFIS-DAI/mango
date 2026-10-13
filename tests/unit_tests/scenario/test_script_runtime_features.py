"""to_script: generated scripts run and record what the run records, also for the
runtime features agents use while the run steps (N06, N29)."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from pydantic import BaseModel  # noqa: E402

from mango import Agent  # noqa: E402
from mango.scenario import FromColumn, Scenario, ScenarioParams, patterns  # noqa: E402
from mango.scenario.result import frame_checksum  # noqa: E402
from mango.scenario.script import code_attributes, generate_script  # noqa: E402
from mango.util.seeding import SeedTree  # noqa: E402

from . import script_fixtures as fx  # noqa: E402
from .script_lean_fixtures import lean  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def replays(scenario, tmp_path, params=None, *, seed=3, overrides=None, lean=None):
    """Generate, run and compare the script; return (script, rows, namespace, run)."""
    script = generate_script(
        scenario, params, seed=seed, overrides=overrides, lean=lean
    )
    assert script.placeholders == ()
    rows, namespace = fx.run_generated(script, tmp_path)
    run = scenario.run(params, seed=seed, overrides=overrides)
    assert len(rows) == len(run.frame)
    assert fx.rows_checksum(rows) == frame_checksum(run.frame)  # logs: own tests
    return script, rows, namespace, run


# ------------------------------------------------------------------ dynamic links


class Peer(Agent):
    """Counts what it hears; node_0 rewires the overlay at t=2 and t=4."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.heard = 0
        self.degree = 0

    def handle_message(self, content, meta):
        self.heard += 1

    def on_step(self, env, clock, step_size_s):
        if self.aid == "peer_0" and clock.time == 2:
            pairs = self.ctx.link_pairs("overlay")
            self.ctx.relink("overlay", pairs[:2])
        if self.aid == "peer_0" and clock.time == 4:
            self.ctx.relink("overlay")  # the declared pattern again (stream relink/1)
        neighbors = self.neighbors(tid="overlay")
        self.degree = len(neighbors)
        for neighbor in neighbors:
            self.schedule_instant_message(self.heard, neighbor)


relinking = Scenario("relinking")
relinking.time(horizon=6, step=1)
peer_group = relinking.group("peer", 5, lambda e: Peer(e.ctx))
relinking.link("overlay", peer_group, pattern=patterns.random(per_target=(1, 2)))
relinking.record("heard", peer_group, attr="heard")
relinking.record("degree", peer_group, attr="degree", kind="state")


def test_relink_and_link_pairs_are_written_out_and_replay(tmp_path):
    script, *_ = replays(relinking, tmp_path)
    assert not script.lean
    assert "def relink(self, name, pairs=None):" in script
    assert "def link_pairs(self, name):" in script
    assert "LINK_PATTERNS = {" in script  # what ctx.relink('overlay') applies again
    assert "ctx.links['overlay'] = (topology, node)" in script


# ------------------------------------------------------------------ births and deaths


class Prey(Agent):
    def __init__(self, ctx, energy):
        super().__init__()
        self.ctx = ctx
        self.energy = energy
        self.heard = 0

    def handle_message(self, content, meta):
        self.heard += 1

    def on_step(self, env, clock, step_size_s):
        self.energy += 0.5
        if self.energy >= 2.0:
            self.energy -= 1.0
            child = self.ctx.spawn("prey", attrs={"born": clock.time})
            self.ctx.log("births", parent=self.aid, child=child.name)
        for neighbor in self.neighbors(tid="herd"):
            self.schedule_instant_message("hi", neighbor)


class Fox(Agent):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.eaten = 0

    def on_step(self, env, clock, step_size_s):
        prey = self.ctx.entities("prey")
        if clock.time % 2 == 0 and prey:
            self.ctx.retire(prey[0])
            self.eaten += 1
            self.ctx.set_metric("last_meal_t", clock.time)
            self.ctx.relink("herd")  # the newborn join the herd


population = Scenario("population")
population.time(horizon=8, step=1)
prey_group = population.group(
    "prey",
    3,
    lambda e: Prey(e.ctx, e.rng.uniform(0.0, 1.5)),
    install=lambda e: {"born": 0.0},
)
population.group("fox", 1, lambda e: Fox(e.ctx))
population.link("herd", prey_group, pattern="ring", delay=1.0)
population.record("energy", prey_group, attr="energy")
population.record("heard", prey_group, attr="heard", aggregate="sum")
population.record_world("alive", lambda ctx: len(ctx.entities("prey")), kind="state")
population.metric("eaten", lambda ctx: ctx.agents("fox")[0].eaten)


def test_spawn_retire_logs_and_metrics_replay(tmp_path, capsys):
    script, rows, namespace, run = replays(population, tmp_path)
    assert run.stats["spawned"] > 0 and run.stats["retired"] > 0
    assert "def spawn(self, group, *, attrs=None):" in script
    assert "def retire(self, entity):" in script
    assert "await ctx.finish_retiring()" in script
    _, ctx = asyncio.run(namespace["simulate"]())  # the script's ctx: logs, metrics
    log = run.log("births")
    assert [(t, row["parent"], row["child"]) for t, _, row in ctx.logs["births"]] == [
        (t, parent, child)
        for t, parent, child in zip(log["time"], log["parent"], log["child"])
    ]
    assert ctx.metrics == run.metrics
    out = capsys.readouterr().out
    assert "event log 'births'" in out and "metrics: {" in out


# ------------------------------------------------------------------ link delay, loss, partitions


class Talker(Agent):
    def __init__(self):
        super().__init__()
        self.heard = 0
        self.last_delay = 0.0

    def handle_message(self, content, meta):
        self.heard += 1

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid="wire"):
            self.schedule_instant_message(clock.time, neighbor)
        for neighbor in self.neighbors(tid="radio"):
            self.schedule_instant_message(clock.time, neighbor)


class SideParams(BaseModel, extra="forbid"):
    n: int = 3


class CommParams(ScenarioParams):
    west: SideParams = SideParams()


layered = Scenario("layered", params=CommParams)
layered.time(horizon=12, step=1)
west = layered.group(
    "west", "west.n", lambda e: Talker(), install=lambda e: {"side": "w"}
)
east = layered.group("east", 2, lambda e: Talker(), install=lambda e: {"side": "e"})
layered.link("wire", west, east, pattern="all_to_all", delay={"choice": [0, 1, 2]})
layered.link("radio", west, pattern="ring", loss=0.3)
layered.record("heard", [west, east], attr="heard")

PARTITIONED = {
    "comm": {"kind": "simple", "partition": {"by_attr": "side"}},
    "phases": [
        {"name": "joined", "until": 4},
        {"name": "split", "until": 8, "comm": {"partition": [["west"], ["east"]]}},
    ],
}


@pytest.mark.parametrize("params", [None, PARTITIONED])
def test_link_delay_loss_and_partitions_replay(tmp_path, params):
    script, *_ = replays(layered, tmp_path, params)
    assert (
        "LayeredCommunication(inner, block_of=block_of, edges=ctx.edge_rules)" in script
    )
    assert "'wire': LinkComm.model_validate(" in script
    assert "set_edge_rules(ctx, 'wire')" in script
    assert "# PLACEHOLDER" not in script


# ------------------------------------------------------------------ stopping early


class Counter(Agent):
    def __init__(self, ctx, stop_at):
        super().__init__()
        self.ctx = ctx
        self.stop_at = stop_at
        self.k = 0

    def on_step(self, env, clock, step_size_s):
        self.k += 1
        if self.k == self.stop_at:
            self.ctx.stop(f"counted to {self.k}")


def stopping(stop_at=None, when=None, action_at=None):
    scenario = Scenario("stopping")
    scenario.time(horizon=20, step=1)
    scenario.group("counter", 1, lambda e: Counter(e.ctx, stop_at))
    scenario.record("k", "counter", attr="k")
    if when is not None:
        scenario.stop_when(lambda ctx: ctx.agents("counter")[0].k >= when, every=2)
    if action_at is not None:
        scenario.event(action_at, action=lambda ctx: ctx.stop("event"), name="halt")
    return scenario


@pytest.mark.parametrize(
    "kwargs", [{"stop_at": 5}, {"when": 7}, {"action_at": 9}], ids=str
)
def test_ctx_stop_stop_when_and_event_stops_replay(tmp_path, capsys, kwargs):
    scenario = stopping(**kwargs)
    *_, run = replays(scenario, tmp_path)
    assert run.stats["stopped_early"]
    assert (
        f"stopped early at t={run.stats['stop_time_s']:g} s" in capsys.readouterr().out
    )


# ------------------------------------------------------------------ events with selectors


class Listener(Agent):
    def __init__(self):
        super().__init__()
        self.got = 0

    def on_agent_event(self, event):
        self.got += 1

    def on_step(self, env, clock, step_size_s):
        if self.aid == "fan_0" and clock.time == 1:
            self.ctx_spawn("fan")

    def ctx_spawn(self, group):
        self.scenario_ctx.spawn(group, attrs={"age": "young"})


def _listener(e):
    agent = Listener()
    agent.scenario_ctx = e.ctx
    return agent


events = Scenario("selectors")
events.time(horizon=6, step=1)
events.group(
    "fan", 3, _listener, install=lambda e: {"age": "old" if e.index else "young"}
)
events.event(2, every=1, emit={"kind": "ad"}, emit_to={"by_attr": {"age": "young"}})
events.event(3, emit={"kind": "all"}, emit_to="fan")
events.record("got", "fan", attr="got")


def test_selector_and_group_targets_resolve_when_the_event_fires(tmp_path):
    script, *_ = replays(events, tmp_path)
    assert "def event_targets(ctx, emit_to, selector):" in script


# ------------------------------------------------------------------ tables, phase, warn


class Respondent(Agent):
    def __init__(self, ctx, opinion, row):
        super().__init__()
        self.ctx = ctx
        self.opinion = opinion
        self.row = row
        self.phase_seen = ""

    def on_step(self, env, clock, step_size_s):
        self.phase_seen = self.ctx.phase + ("*" if self.ctx.recording else "")
        if clock.time == 2:
            self.ctx.warn("halfway")


class SurveyParams(ScenarioParams):
    opinion: FromColumn = FromColumn("unset.csv", "opinion", mode="sample")


survey = Scenario("survey", params=SurveyParams)
survey.time(horizon=4, step=1)
survey.group(
    "person",
    4,
    lambda e: Respondent(e.ctx, e.draw(e.p.opinion), e.row(e.p.opinion)),
)
survey.record("opinion", "person", attr="opinion")
survey.record("row", "person", attr="row")
survey.record("phase_seen", "person", attr="phase_seen")


def test_sample_tables_phase_and_warn_replay(tmp_path):
    table = tmp_path / "survey.csv"
    table.write_text("opinion\n0.1\n0.2\n0.3\n0.4\n0.5\n0.6\n", encoding="utf-8")
    params = {
        "opinion": {
            "kind": "from_column",
            "path": str(table),
            "column": "opinion",
            "mode": "sample",
        },
        "phases": [{"name": "early", "until": 2, "record": False}],
    }
    with pytest.warns(UserWarning, match="halfway"):
        script, *_ = replays(survey, tmp_path, params)
    assert "def table_rows(self, dist, size):" in script
    assert "def warn(self, message):" in script


# ------------------------------------------------------------------ lean form


def test_lean_script_holds_only_what_the_code_uses(tmp_path):
    script, _, namespace, _ = replays(lean, tmp_path)
    assert script.lean
    for left_out in (
        "def log(",
        "def set_metric(",
        "def rng_for(",
        "def draw(",
        "def profile(",
        "add_reduced_recorder",
        "import inspect",
        "def due(",
        "class Placeholder",
    ):
        assert left_out not in script
    # a plain aggregate over each group is a short helper
    assert "add_group_recorder(world, ctx, ctx.reduced, 'total', 'sum', " in script
    ctx = namespace["ScriptContext"](namespace["params"], None)
    with pytest.raises(AttributeError, match="has no 'spawn': the lean script"):
        ctx.spawn("item")


def test_code_attributes_scan_the_project_modules():
    params = population.resolve_params(None)
    state = population._declaration_stage(params, SeedTree(1))
    names = code_attributes(population, state)
    assert {"spawn", "retire", "relink", "log", "set_metric"} <= names


# ------------------------------------------------------------------ the N06 reports


def test_generated_scripts_never_crash_on_runtime_features(tmp_path):
    """The verified reports: relink/link_pairs, spawn and link delays (to-script N06)."""
    for scenario in (relinking, population, layered):
        script = generate_script(scenario, seed=1)
        assert script.placeholders == ()
        fx.run_generated(script, tmp_path, name=f"{scenario.name}.py")


# ------------------------------------------------------------------ written elsewhere


def test_a_script_written_to_a_subfolder_runs_from_anywhere(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "model_n29.py").write_text(
        "from mango import Agent\n"
        "from mango.scenario import Scenario\n\n\n"
        "class A(Agent):\n"
        "    def __init__(self, v):\n"
        "        super().__init__()\n"
        "        self.v = v\n\n"
        "    def on_step(self, env, clock, step_size_s):\n"
        "        self.v += 1\n\n\n"
        "scenario = Scenario('n29')\n"
        "scenario.time(horizon=3, step=1)\n"
        "scenario.group('a', 2, lambda e: A(e.index))\n"
        "scenario.record('v', 'a', attr='v')\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    write = (
        "from model_n29 import scenario\n"
        "scenario.to_script(seed=1).write('archive/run_1.py')\n"
    )
    subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, '.')\n{write}"],
        cwd=project,
        env=env,
        check=True,
        timeout=120,
    )
    script = (project / "archive" / "run_1.py").read_text(encoding="utf-8")
    assert "PROJECT = Path(__file__).resolve().parents[1]" in script
    assert "Run (from any folder)" in script
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    completed = subprocess.run(
        [sys.executable, str(project / "archive" / "run_1.py"), "values.csv"],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "8 values recorded" in completed.stdout
    assert (elsewhere / "values.csv").exists()  # relative to where it was started


def test_for_location_leaves_scripts_in_the_project_folder_alone(tmp_path):
    script = generate_script(lean, seed=1)
    assert script.for_location(Path.cwd() / "plain.py") is script
    moved = script.for_location(tmp_path / "x" / "plain.py", project_root=Path.cwd())
    assert "sys.path.insert(0, str(PROJECT))" in moved
    assert "from pathlib import Path" in moved
    compile(moved, "moved.py", "exec")


def test_full_scripts_name_what_they_leave_out(tmp_path):
    _, namespace = fx.run_generated(fx.scenario.to_script(seed=1), tmp_path)
    ctx = namespace["ScriptContext"](namespace["params"], SeedTree(1))
    with pytest.raises(AttributeError, match="has no 'link': to-script writes out"):
        ctx.link("x", "node")
    with pytest.raises(AttributeError, match="ctx.spawn/retire/relink"):
        ctx.spawn("node")  # script_fixtures never spawns
    assert getattr(ctx, "spawn", None) is None  # getattr defaults keep working
