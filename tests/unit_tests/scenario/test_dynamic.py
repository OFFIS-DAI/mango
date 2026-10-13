"""Dynamic populations: ctx.spawn / ctx.retire during a run."""

import math

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pandas")

from mango import Agent, Area2D, DefaultEnvironment, Position2D  # noqa: E402
from mango.scenario import Scenario, ScenarioError  # noqa: E402


class Prey(Agent):
    def __init__(self, ctx, energy=1.0):
        super().__init__()
        self.ctx = ctx
        self.energy = energy
        self.stopped = False
        self.steps = 0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.steps += 1
        self.energy += 1.0

    async def on_stop(self):
        self.stopped = True


class Fox(Agent):
    """At t=2 eats the first prey, a new one is born and the food link recomputed."""

    def __init__(self, ctx, log):
        super().__init__()
        self.ctx = ctx
        self.log = log

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        if clock.time == 2:
            victim = self.ctx.entities("prey")[0]
            self.ctx.retire(victim)
            born = self.ctx.spawn("prey", attrs={"bus": 7})
            self.log.update(victim=victim, born=born)
            self.ctx.relink("food")


def meadow(log):
    scenario = Scenario("meadow")
    scenario.time(horizon=5, step=1)
    scenario.environment(lambda ctx: DefaultEnvironment(space=Area2D(10, 10)))
    scenario.group(
        "prey",
        3,
        lambda e: Prey(e.ctx, energy=e.attrs.get("bus", 0)),
        install=lambda e: {"bus": e.index},
        position=lambda e: Position2D(float(e.index), 0.0),
    )
    scenario.group("fox", 1, lambda e: Fox(e.ctx, log))
    scenario.link("food", "fox", "prey", pattern="all_to_all")
    scenario.record("energy", "prey", attr="energy")
    scenario.record_world("n_prey", lambda ctx: float(len(ctx.agents("prey"))))
    return scenario


def test_spawned_and_retired_entities_in_results_links_and_world():
    log = {}
    result = meadow(log).run(seed=1)
    victim, born = log["victim"], log["born"]
    assert born.name == "prey_3" and born.index == 3 and born.attrs == {"bus": 7}
    assert born.agent.energy == 9.0  # stepped at t=2..3 and 3..4 only
    wide = result.wide("energy")
    assert list(wide.columns) == ["prey_0", "prey_1", "prey_2", "prey_3"]
    assert wide["prey_0"].tolist()[:3] == [0.0, 1.0, 2.0]
    assert all(math.isnan(v) for v in wide["prey_0"].tolist()[3:])
    assert all(math.isnan(v) for v in wide["prey_3"].tolist()[:3])
    assert wide["prey_3"].tolist()[3:] == [7.0, 8.0, 9.0]
    assert result.ts("n_prey").tolist() == [3.0] * 6
    # stepped in the steps starting at 0, 1 and 2 (before the fox, in that step)
    assert not victim.alive and victim.agent.stopped and victim.agent.steps == 3
    assert "prey_0" not in result.world.agents
    assert result.entities["prey_3"] == "prey"
    stats = result.stats
    assert stats["spawned"] == 1 and stats["retired"] == 1
    assert stats["agents_alive"] == {"prey": 3, "fox": 1}
    assert stats["link_edges"] == {"food": 3}  # recomputed over the living prey
    world = result.world
    space = world.environment.space
    assert space.location(born.agent) == Position2D(3.0, 0.0)
    assert victim.agent not in world.environment.agents


def test_runs_without_population_changes_keep_their_stats():
    scenario = Scenario("static")
    scenario.time(horizon=2, step=1)
    scenario.group("g", 2, lambda e: Prey(e.ctx))
    stats = scenario.run(seed=1).stats
    assert "spawned" not in stats and "agents_alive" not in stats


def test_spawn_streams_are_stable_and_names_follow_naming():
    draws = []

    class Breeder(Agent):
        def __init__(self, ctx, first):
            super().__init__()
            self.ctx, self.first = ctx, first

        def on_step(self, env, clock, step_size_s):
            if self.first and clock.time == 0:
                entity = self.ctx.spawn("cell")
                draws.append((entity.name, entity.agent.rng.random()))

    scenario = Scenario("cells")
    scenario.time(horizon=2, step=1)
    scenario.group(
        "cell", 2, lambda e: Breeder(e.ctx, e.index == 0), naming="{group}-{index:02d}"
    )
    scenario.run(seed=4)
    scenario.run(seed=4)
    assert draws[0][0] == "cell-02" and draws[0] == draws[1]


def test_retired_links_drop_edges_and_relink_rejects_them():
    log = {}

    class Hunter(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        def on_step(self, env, clock, step_size_s):
            if clock.time == 0:
                self.ctx.retire("n_1")
                log["pairs"] = self.ctx.link_pairs("ring")
                with pytest.raises(ScenarioError, match="already retired"):
                    self.ctx.retire("n_1")
                with pytest.raises(ScenarioError, match="'n_1' is retired"):
                    self.ctx.relink("ring", [("n_0", "n_1")])
                log["entity"] = self.ctx.entity("n_1").alive

    scenario = Scenario("ring")
    scenario.time(horizon=1, step=1)
    scenario.group("n", 4, lambda e: Plain())
    scenario.group("hunter", 1, lambda e: Hunter(e.ctx))
    scenario.link("ring", "n", pattern="ring")
    scenario.run(seed=1)
    assert sorted(tuple(sorted(pair)) for pair in log["pairs"]) == [
        ("n_0", "n_3"),
        ("n_2", "n_3"),
    ]
    assert log["entity"] is False


class Plain(Agent):
    def handle_message(self, content, meta):
        pass


def test_spawn_and_retire_need_a_running_run():
    seen = {}
    scenario = Scenario("early")
    scenario.time(horizon=1, step=1)
    group = scenario.group("g", 1, lambda e: Plain())

    @scenario.on_build
    def hook(ctx):
        seen["entity"] = ctx.spawn(group)  # allowed: the world exists

    scenario.run(seed=1)
    assert seen["entity"].name == "g_1"

    declaring = Scenario("declaring")
    declaring.group("g", 1, lambda e: Plain())
    declaring._declare("build", lambda ctx: ctx.spawn("g"), "spawn")
    with pytest.raises(ScenarioError, match="changes the population of a running run"):
        declaring.plan()
    with pytest.raises(ScenarioError, match="is not an entity of the run"):
        broken = Scenario("broken")
        broken.time(horizon=1, step=1)
        broken.group("g", 1, lambda e: Plain())
        broken.on_build(lambda ctx: ctx.retire("nobody"))
        broken.run(seed=1)
