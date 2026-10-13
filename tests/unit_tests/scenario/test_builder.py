import json
import random

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from pydantic import BaseModel  # noqa: E402

from mango import Agent, Area2D, DefaultEnvironment, Position2D, SeedTree  # noqa: E402
from mango.scenario import (  # noqa: E402
    BuildContext,
    CommConfig,
    Entity,
    EntityContext,
    GroupRef,
    ParamsError,
    Scenario,
    ScenarioError,
    ScenarioParams,
    ScenarioPlan,
    Uniform,
    patterns,
)
from mango.simulation import (  # noqa: E402
    SimpleCommunicationSimulation,
)

# ----------------------------------------------------------------- helpers


class Node(Agent):
    def __init__(self, value: float = 0.0):
        super().__init__()
        self.value = value
        self.received = 0

    def handle_message(self, content, meta):
        self.received += 1

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid="ring"):
            self.schedule_instant_message(self.value, neighbor)


class EVParams(BaseModel, extra="forbid"):
    n: int = 3
    soc: Uniform = Uniform(0.2, 0.8)


class Params(ScenarioParams):
    ev: EVParams = EVParams()
    households: int = 2


class Plain(Agent):
    def __init__(self, **kwargs):
        super().__init__()
        self.__dict__.update(kwargs)

    def handle_message(self, content, meta):
        pass


def small(name="s", **time):
    scenario = Scenario(name, params=Params)
    scenario.time(horizon=time.get("horizon", "3h"), step=time.get("step", "1h"))
    return scenario


MODULE_SCENARIO = Scenario("module_level")


# ----------------------------------------------------------------- R11 toy


def test_toy_scenario_in_a_handful_of_lines():
    scenario = Scenario("toy")
    nodes = scenario.group("node", 10, lambda e: Node(value=e.index))
    scenario.link("ring", nodes, pattern="ring")
    scenario.record("received", nodes, attr="received")
    result = scenario.run(seed=1)

    assert result.groups() == ["node"]
    assert list(result.wide("received").columns) == [f"node_{i}" for i in range(10)]
    # 24 hourly steps, two ring neighbours each sending one message per step
    assert result.wide("received").iloc[-1].tolist() == [48.0] * 10
    assert result.stats["link_edges"] == {"ring": 10}
    ring = {
        aid: sorted(
            a.aid
            for a in result.world.agents.values()
            if a.addr in agent.neighbors(tid="ring")
        )
        for aid, agent in result.world.agents.items()
    }
    assert ring["node_0"] == ["node_1", "node_9"]


# ----------------------------------------------------------------- construction


def test_params_model_must_subclass_scenario_params():
    with pytest.raises(TypeError, match="ScenarioParams"):
        Scenario("x", params=EVParams)
    with pytest.raises(ScenarioError, match="non-empty"):
        Scenario("")
    assert Scenario("x").params_model is ScenarioParams
    assert repr(Scenario("x", params=Params)) == "Scenario('x', params=Params)"


def test_scenario_ref_of_module_level_scenario():
    assert MODULE_SCENARIO.ref == f"{__name__}:MODULE_SCENARIO"
    assert Scenario("local").ref is None


def test_time_and_defaults_become_param_defaults():
    scenario = Scenario("x", params=Params)
    scenario.time(start="2024-06-01T00:00", horizon="2d", step="15min")
    scenario.defaults({"ev.n": 5, "households": 4})
    scenario.defaults({"ev": {"soc": [0.1, 0.9]}})
    # merged like presets: shorthands expanded against the params model
    assert scenario.param_defaults() == {
        "time": {"start": "2024-06-01T00:00", "horizon": "2d", "step": "15min"},
        "ev": {"n": 5, "soc": {"kind": "uniform", "low": 0.1, "high": 0.9}},
        "households": 4,
    }
    params = scenario.resolve_params()
    assert params.ev.n == 5 and params.ev.soc == Uniform(0.1, 0.9)
    assert params.time.step_s == 900.0
    # presets and overrides win over scenario defaults
    assert (
        scenario.resolve_params({"ev": {"n": 7}}, overrides=["households=1"]).ev.n == 7
    )


class HouseParams(BaseModel, extra="forbid"):
    load_kw: float = 1.0


class ListParams(ScenarioParams):
    houses: list[HouseParams] = [HouseParams(), HouseParams(load_kw=2.0)]
    arrival: Uniform = Uniform(1.0, 2.0)


def test_each_defaults_call_is_a_layer_merged_like_a_preset():
    scenario = Scenario("x", params=ListParams)
    # "+houses" extends the model's default list, as in overrides
    scenario.defaults({"+houses": [{"load_kw": 3.0}]})
    assert [h.load_kw for h in scenario.resolve_params().houses] == [1.0, 2.0, 3.0]
    assert [
        h.load_kw
        for h in Scenario("y", params=ListParams)
        .resolve_params(overrides={"+houses": [{"load_kw": 3.0}]})
        .houses
    ] == [1.0, 2.0, 3.0]
    # a later shorthand of another kind replaces the earlier distribution
    scenario.defaults({"arrival": {"kind": "normal", "mean": 16.0, "std": 1.5}})
    scenario.defaults({"arrival": {"uniform": [3, 4]}})
    assert scenario.resolve_params().arrival == Uniform(3, 4)
    # a sub-field default patches the model default distribution
    scenario.defaults({"arrival.high": 9.0})
    assert scenario.resolve_params().arrival == Uniform(3, 9)


def test_time_validates_each_field_eagerly():
    scenario = Scenario("x")
    with pytest.raises(ParamsError, match="horizon") as info:
        scenario.time(horizon="2x")
    assert info.value.source == "scenario defaults"
    with pytest.raises(ParamsError):
        scenario.time(step="-1h")
    with pytest.raises(ScenarioError, match="at least one"):
        scenario.time()
    scenario.time(step="event")
    assert scenario.resolve_params().time.is_event_driven


def test_unknown_default_path_fails_when_params_load():
    scenario = Scenario("x", params=Params)
    scenario.defaults({"ev.nn": 5})
    with pytest.raises(ParamsError, match="scenario defaults") as info:
        scenario.plan()
    assert "did you mean 'ev.n'" in str(info.value)


def test_params_forms_and_overrides(tmp_path):
    scenario = Scenario("x", params=Params)
    preset = tmp_path / "base.yaml"
    preset.write_text("ev:\n  n: 6\nhouseholds: 5\n")
    assert scenario.resolve_params().ev.n == 3
    assert scenario.resolve_params(Params(households=9)).households == 9
    assert scenario.resolve_params({"ev": {"n": 4}}).ev.n == 4
    assert scenario.resolve_params(str(preset)).ev.n == 6
    merged = scenario.resolve_params([preset, {"ev": {"n": 8}}, None])
    assert (merged.ev.n, merged.households) == (8, 5)
    assert scenario.resolve_params(preset, overrides={"ev.n": 11}).ev.n == 11
    # an already resolved instance resolves to the same params
    assert scenario.resolve_params(merged) == merged


# ----------------------------------------------------------------- entity naming


def names_of(scenario, **kwargs):
    return {g.name: g.names for g in scenario.plan(**kwargs).groups}


def test_default_names_and_singleton_rule():
    scenario = small()
    scenario.group("coordinator", 1, lambda e: Plain())
    scenario.group("ev", "ev.n", lambda e: Plain())
    scenario.group("single_from_path", "households", lambda e: Plain())
    scenario.group("single_from_fn", lambda p: 1, lambda e: Plain())
    names = names_of(scenario, overrides=["households=1"])
    assert names == {
        "coordinator": ["coordinator"],
        "ev": ["ev_0", "ev_1", "ev_2"],
        "single_from_path": ["single_from_path_0"],
        "single_from_fn": ["single_from_fn_0"],
    }


def test_naming_format_and_callable():
    scenario = small()
    scenario.group("ev", 3, lambda e: Plain(), naming="car-{index:02d}")
    scenario.group(
        "hh", 2, lambda e: Plain(), naming=lambda e: f"home{e.index + e.p.households}"
    )
    scenario.group("bus", 1, lambda e: Plain(), naming="{group}_{index}")
    assert names_of(scenario) == {
        "ev": ["car-00", "car-01", "car-02"],
        "hh": ["home2", "home3"],
        "bus": ["bus_0"],
    }
    result = scenario.run(seed=1)
    assert set(result.world.agents) == {
        "car-00",
        "car-01",
        "car-02",
        "home2",
        "home3",
        "bus_0",
    }
    assert result.entities["home3"] == "hh"


def test_naming_errors():
    def plan_with(**group_kwargs):
        scenario = small()
        scenario.group("ev", 2, lambda e: Plain(), **group_kwargs)
        return scenario.plan()

    with pytest.raises(ScenarioError, match="invalid naming format"):
        plan_with(naming="{group}_{idx}")
    with pytest.raises(ScenarioError, match="twice"):
        plan_with(naming="{group}")
    with pytest.raises(ScenarioError, match="must not contain '/'"):
        plan_with(naming="ev/{index}")
    with pytest.raises(ScenarioError, match="non-empty strings"):
        plan_with(naming=lambda e: e.index)
    with pytest.raises(ScenarioError, match="not available inside naming"):
        plan_with(naming=lambda e: f"ev{e.rng.integers(10)}")
    with pytest.raises(TypeError, match="naming"):
        plan_with(naming=3)


def test_generated_aid_names_are_rejected():
    scenario = small()
    scenario.group("agent3", 1, lambda e: Plain())
    with pytest.raises(ScenarioError, match="'agent3'.*generates aids 'agent<N>'"):
        scenario.plan()
    scenario = small()
    scenario.group("ev", 2, lambda e: Plain(), naming="agent{index}")
    with pytest.raises(ScenarioError, match="naming="):
        scenario.plan()
    # "agent" as group with default naming is fine
    scenario = small()
    scenario.group("agent", 2, lambda e: Plain())
    assert names_of(scenario) == {"agent": ["agent_0", "agent_1"]}


def test_entity_names_must_be_unique_across_groups():
    scenario = small()
    scenario.group("ev", 2, lambda e: Plain())
    scenario.group("other", 1, lambda e: Plain(), naming="ev_1")
    with pytest.raises(
        ScenarioError, match="'ev_1' of group 'other' is already used by group 'ev'"
    ):
        scenario.plan()


def test_group_name_rules():
    scenario = small()
    scenario.group("ev", 1, lambda e: Plain())
    with pytest.raises(ScenarioError, match="declared twice"):
        scenario.group("ev", 2, lambda e: Plain())
    for bad in ("grid.hh", "a/b", "", "  "):
        with pytest.raises(ScenarioError):
            scenario.group(bad, 1, lambda e: Plain())
    with pytest.raises(TypeError, match="factory"):
        scenario.group("x", 1, factory=42)


# ----------------------------------------------------------------- counts


def test_count_forms():
    scenario = small()
    scenario.group("a", 2, lambda e: Plain())
    scenario.group("b", "ev.n", lambda e: Plain())
    scenario.group("c", lambda p: p.households * 2, lambda e: Plain())
    scenario.group("d", 0, lambda e: Plain())
    plan = scenario.plan(overrides=["ev.n=4"])
    assert [(g.name, g.count) for g in plan.groups] == [
        ("a", 2),
        ("b", 4),
        ("c", 4),
        ("d", 0),
    ]
    assert plan.total_agents == 10
    result = scenario.run(seed=3, overrides=["ev.n=4"])
    assert result.stats["agents_per_group"] == {"a": 2, "b": 4, "c": 4, "d": 0}
    assert result.groups() == ["a", "b", "c", "d"]


def test_count_errors():
    def plan(count):
        scenario = small()
        scenario.group("ev", count, lambda e: Plain())
        return scenario.plan()

    with pytest.raises(
        ScenarioError, match="'ev.nn' is not a parameter path.*did you mean 'ev.n'"
    ):
        plan("ev.nn")
    with pytest.raises(ScenarioError, match="must be an int"):
        plan("ev.soc")
    with pytest.raises(ScenarioError, match=">= 0"):
        plan(lambda p: -1)
    with pytest.raises(ScenarioError, match=">= 0"):
        small().group("ev", -2, lambda e: Plain())
    with pytest.raises(TypeError, match="count"):
        small().group("ev", 2.5, lambda e: Plain())
    with pytest.raises(TypeError, match="count"):
        small().group("ev", True, lambda e: Plain())


# ----------------------------------------------------------------- entities


def test_entity_rng_is_agent_rng_and_draws_continue_one_stream():
    scenario = small()
    seen = {}

    def make(e):
        seen[e.name] = e
        return Plain(soc=e.p.ev.soc.draw(e.rng), sub=e.rng_for("arrival").random())

    scenario.group("ev", "ev.n", make)
    run = scenario.build(seed=5)

    async def check():
        async with run:
            for name, e in seen.items():
                agent = run.world.agents[name]
                assert e.rng is agent.rng
                assert e.rng_for("arrival") is agent.rng_for("arrival")
                assert e.agent is agent
                assert e.rng is run.seeds.rng("agent", name)
        return run

    import asyncio

    asyncio.run(check())
    # the construction draw is the first draw of the entity stream
    expected = SeedTree(5).rng("agent/ev_1")
    assert run.world.agents["ev_1"].soc == Uniform(0.2, 0.8).draw(expected)


def test_install_is_evaluated_before_factory_and_passed_to_environment():
    order = []

    class RecordingEnvironment(DefaultEnvironment):
        def __init__(self):
            super().__init__()
            self.installed = {}

        def install(self, agent, agent_id=None, **kwargs):
            order.append(("env", agent_id))
            self.installed[agent_id] = kwargs
            super().install(agent, agent_id=agent_id, **kwargs)

    def install(e):
        order.append(("install", e.name))
        assert e.attrs == {}
        return {"bus": e.index * 10}

    def factory(e):
        order.append(("factory", e.name))
        return Plain(bus=e.attrs["bus"])

    scenario = small()
    scenario.environment(lambda ctx: RecordingEnvironment())
    scenario.group("ev", 2, factory, install=install)
    result = scenario.run(seed=1)
    env = result.world.environment
    assert env.installed == {"ev_0": {"bus": 0}, "ev_1": {"bus": 10}}
    assert order == [
        ("install", "ev_0"),
        ("factory", "ev_0"),
        ("env", "ev_0"),
        ("install", "ev_1"),
        ("factory", "ev_1"),
        ("env", "ev_1"),
    ]
    assert result.agent("ev_1").bus == 10


def test_install_errors():
    scenario = small()
    scenario.group("ev", 1, lambda e: Plain(), install=lambda e: 5)
    with pytest.raises(
        TypeError, match="install function of group 'ev' must return a dict"
    ):
        scenario.run(seed=1)
    scenario = small()
    scenario.group("ev", 1, lambda e: Plain(), install=lambda e: {"agent_id": 1})
    with pytest.raises(
        ScenarioError, match="install attribute 'agent_id' is not allowed"
    ):
        scenario.run(seed=1)


def test_factory_must_return_an_agent():
    scenario = small()
    scenario.group("ev", 2, lambda e: "not an agent")
    with pytest.raises(TypeError, match="group 'ev' returned str for entity 'ev_0'"):
        scenario.run(seed=1)


def test_category_and_entities():
    class Tagged(Plain):
        def __init__(self):
            super().__init__()
            self.update_description(category="custom")

    scenario = small()
    scenario.group("ev", 2, lambda e: Plain())
    scenario.group("tagged", 1, lambda e: Tagged())
    result = scenario.run(seed=1)
    assert result.agent("ev_0").description.category == "ev"
    assert result.agent("tagged").description.category == "custom"
    assert result.entities == {"ev_0": "ev", "ev_1": "ev", "tagged": "tagged"}


def test_positions_are_applied():
    scenario = small()
    scenario.environment(lambda ctx: DefaultEnvironment(space=Area2D(100, 100)))
    scenario.group(
        "ev", 3, lambda e: Plain(), position=lambda e: Position2D(e.index * 2.0, 1.0)
    )
    scenario.group("free", 2, lambda e: Plain())
    result = scenario.run(seed=1)
    space = result.world.environment.space
    assert space.location(result.agent("ev_2")) == Position2D(4.0, 1.0)
    # agents without explicit positions are placed by the space's own stream
    assert space.has_position(result.agent("free_0"))


def test_positions_need_a_space():
    scenario = small()
    scenario.group("ev", 1, lambda e: Plain(), position=lambda e: Position2D(0, 0))
    with pytest.raises(ScenarioError, match="no space"):
        scenario.run(seed=1)


def test_context_lookups_and_build_order():
    log = []

    def make_ev(e):
        coordinator = e.ctx.agents("coordinator")[0]
        assert e.ctx.entities("ev")[: e.index] == [
            e.ctx.entity(f"ev_{i}") for i in range(e.index)
        ]
        return Plain(coordinator=coordinator)

    scenario = small()
    scenario.group("coordinator", 1, lambda e: Plain())
    ev = scenario.group("ev", 3, make_ev)
    scenario.link("dispatch", ev, "coordinator", pattern="many_to_one")
    scenario.record("x", ev, lambda a: 1)

    @scenario.on_build
    def check(ctx):
        log.append("on_build")
        assert isinstance(ctx, BuildContext) and ctx.prefix == ""
        coordinator = ctx.agents("coordinator")[0]
        assert len(coordinator.neighbors(tid="dispatch")) == 3
        assert "x" in ctx.world.data_agent_collections
        assert [e.name for e in ctx.entities(ev)] == ["ev_0", "ev_1", "ev_2"]
        assert isinstance(ctx.entity("ev_0"), Entity)
        with pytest.raises(ScenarioError, match="Unknown entity 'ev_9'"):
            ctx.entity("ev_9")
        with pytest.raises(ScenarioError, match="declaration stage"):
            ctx.group("late", 1, lambda e: Plain())

    result = scenario.run(seed=1)
    assert log == ["on_build"]
    assert result.agent("ev_1").coordinator is result.agent("coordinator")


def test_entities_of_a_later_group_are_not_available():
    scenario = small()
    scenario.group("a", 1, lambda e: Plain(other=e.ctx.agents("b")))
    scenario.group("b", 1, lambda e: Plain())
    with pytest.raises(ScenarioError, match="Group 'b' is not built yet"):
        scenario.run(seed=1)


def test_decorator_forms():
    scenario = small()

    @scenario.group("ev", 2, install=lambda e: {"bus": e.index})
    def ev(e):
        return Plain(bus=e.attrs["bus"])

    @scenario.environment
    def environment(ctx):
        return DefaultEnvironment(space=Area2D(5, 5))

    @scenario.communication
    def communication(ctx):
        return SimpleCommunicationSimulation(default_delay_s=ctx.comm.loss_percent)

    assert isinstance(ev, GroupRef) and str(ev) == "ev"
    assert callable(environment) and callable(communication)
    result = scenario.run(seed=1, overrides=["comm.loss_percent=0.5"])
    assert isinstance(result.world.environment.space, Area2D)
    assert result.world.communication_sim.default_delay_s == 0.5


def test_group_without_factory_used_as_group_gives_hint():
    scenario = small()
    decorator = scenario.group("ev", 2)
    with pytest.raises(TypeError, match="without a factory returns a decorator"):
        scenario.link("x", decorator, pattern="complete")


def test_default_communication_follows_params_comm():
    scenario = small()
    scenario.group("ev", 1, lambda e: Plain())
    result = scenario.run(
        seed=4, overrides={"comm.loss_percent": 0.25, "comm.delay_s": 2.0}
    )
    sim = result.world.communication_sim
    assert isinstance(sim, SimpleCommunicationSimulation)
    assert (sim.loss_percent, sim.default_delay_s) == (0.25, 2.0)
    assert sim.rng is result.world.rng("comm")


def test_factory_type_checks():
    scenario = small()
    scenario.environment(lambda ctx: "env")
    with pytest.raises(TypeError, match="must return a mango Environment"):
        scenario.run(seed=1)
    scenario = small()
    scenario.communication(lambda ctx: "comm")
    with pytest.raises(TypeError, match="must return a CommunicationSimulation"):
        scenario.run(seed=1)
    with pytest.raises(TypeError):
        small().environment(3)


# ----------------------------------------------------------------- records


def test_record_fn_attr_and_shared_variable():
    class Holder:
        def __init__(self, v):
            self.v = v

    scenario = small()
    ev = scenario.group("ev", 2, lambda e: Plain(state=Holder(e.index)))
    hh = scenario.group("hh", 1, lambda e: Plain(state=Holder(10)))
    scenario.record("v", ev, attr="state.v")
    scenario.record("v", hh, lambda a: a.state.v * 2)
    scenario.record("idx", ev, lambda a: a.state.v)
    result = scenario.run(seed=1)
    assert result.variables() == ["v", "idx"]
    frame = result.frame
    assert set(frame[frame.variable == "v"].group) == {"ev", "hh"}
    assert result.ts("v", entity="hh").tolist() == [20.0] * 4
    assert result.wide("v").columns.tolist() == ["ev_0", "ev_1", "hh"]


def test_record_errors():
    scenario = small()
    ev = scenario.group("ev", 1, lambda e: Plain(x=1))
    with pytest.raises(ScenarioError, match="exactly one of fn"):
        scenario.record("x", ev)
    with pytest.raises(ScenarioError, match="exactly one of fn"):
        scenario.record("x", ev, lambda a: 1, attr="x")
    with pytest.raises(ScenarioError, match="positive duration"):
        scenario.record("x", ev, attr="x", every=0)
    scenario.record("x", ev, attr="x")
    scenario.record("x", "ev", attr="x")
    with pytest.raises(ScenarioError, match="declared twice on group 'ev'"):
        scenario.plan()

    scenario = small()
    ev = scenario.group("ev", 1, lambda e: Plain(x=1))
    scenario.record("x", ev, attr="x")
    scenario.record_world("x", lambda: 1)
    with pytest.raises(
        ScenarioError, match="both a world recorder and a per-agent recorder"
    ):
        scenario.plan()

    scenario = small()
    scenario.record_world("w", lambda: 1)
    scenario.record_world("w", lambda: 2)
    with pytest.raises(ScenarioError, match="declared twice"):
        scenario.plan()

    scenario = small()
    scenario.record("x", "evv", attr="x")
    scenario.group("ev", 1, lambda e: Plain(x=1))
    with pytest.raises(
        ScenarioError, match="Unknown group 'evv' in record\\('x'\\).*Did you mean 'ev'"
    ):
        scenario.plan()


def test_record_every_and_record_world_forms():
    scenario = small(horizon="6h")
    ev = scenario.group("ev", 1, lambda e: Plain(x=1))
    scenario.record("x", ev, attr="x", every="2h")
    scenario.record_world("plain", lambda: 7)
    scenario.record_world("with_ctx", lambda ctx: len(ctx.world.agents), entity="grid")
    scenario.record_world("sparse", lambda: 1, every="3h")
    result = scenario.run(seed=1)
    assert result.ts("x").index.tolist() == [0.0, 7200.0, 14400.0, 21600.0]
    assert result.ts("plain").tolist() == [7.0] * 7
    with_ctx = result.frame[result.frame.variable == "with_ctx"]
    assert set(with_ctx.group) == {"grid"} and set(with_ctx.entity) == {"grid"}
    assert with_ctx.value.tolist() == [1.0] * 7
    assert result.ts("sparse").index.tolist() == [0.0, 10800.0, 21600.0]
    world_rows = result.frame[result.frame.variable == "plain"]
    assert set(world_rows.group) == {"world"}


# ----------------------------------------------------------------- plan


def test_plan_without_building():
    built = []

    def factory(e):
        built.append(e.name)
        return Plain()

    scenario = small()
    scenario.time(start="2024-06-01T00:00")
    coordinator = scenario.group("coordinator", 1, factory)
    ev = scenario.group("ev", "ev.n", factory, install=lambda e: {"bus": e.index})
    scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    scenario.link("ring", ev, pattern="ring", directed=True)
    scenario.link("peers", ev, pattern=patterns.random(per_target=(0, 2)))
    scenario.link("bus", ev, ev, pattern=patterns.by_attr("bus"))
    scenario.link("filtered", ev, coordinator, where=lambda a, b: a.index > 0)
    scenario.record("soc", ev, attr="soc", every="1h")
    scenario.record_world("n", lambda: 1)
    plan = scenario.plan(overrides=["ev.n=4"])

    assert built == []
    assert isinstance(plan, ScenarioPlan)
    assert plan.total_agents == 5
    assert plan.group("ev").names == ["ev_0", "ev_1", "ev_2", "ev_3"]
    edges = {link.name: link.edges for link in plan.links}
    assert edges == {
        "dispatch": 4,
        "ring": 4,
        "peers": None,
        "bus": None,
        "filtered": 3,  # the where= filter reads only the index: exact
    }
    assert plan.link("ring").directed
    assert plan.link("dispatch").declarations == [
        {
            "source": "ev",
            "target": "coordinator",
            "pattern": "many_to_one",
            "where": None,
        }
    ]
    assert [(r.name, r.kind, r.group, r.every) for r in plan.recorders] == [
        ("soc", "agent", "ev", 3600.0),
        ("n", "world", None, None),
    ]
    data = plan.to_dict()
    assert json.loads(json.dumps(data))["agents"] == 5
    text = str(plan)
    assert "5 agents in 2 groups, 5 links, 2 recorders" in text
    assert "ev_0 .. ev_3" in text and "4 edges" in text and "? edges" in text
    assert "start 2024-06-01T00:00:00" in text
    with pytest.raises(KeyError, match="did you mean|Did you mean"):
        plan.group("evv")


def test_plan_does_not_touch_global_random():
    scenario = small()
    scenario.group("ev", 2, lambda e: Plain())
    random.seed(3)
    expected = random.random()
    random.seed(3)
    scenario.plan()
    assert random.random() == expected


def test_plan_columns_fit_long_names():
    scenario = small()
    scenario.group("ev", 2, lambda e: Plain(x=1.0))
    scenario.record("a_rather_long_recorder_name", "ev", attr="x")
    scenario.record_world("n", lambda: 1)
    scenario.record_world("grid_kw", lambda: 1, entity="grid")
    lines = str(scenario.plan()).splitlines()
    recorders = lines[lines.index("  recorders:") + 1 :]
    assert recorders == [
        "    a_rather_long_recorder_name ev",
        "    n                           world",
        "    grid_kw                     grid (world)",
    ]
    assert "    ev                               2  ev_0 .. ev_1" in lines


def test_plan_reports_empty_and_event_driven():
    scenario = Scenario("x")
    scenario.time(horizon="1h", step="event")
    scenario.group("ev", 0, lambda e: Plain())
    text = str(scenario.plan())
    assert "event-driven" in text and "(empty)" in text


# ----------------------------------------------------------------- seeds


def test_seed_precedence():
    scenario = small()
    scenario.group("ev", 2, lambda e: Plain(x=float(e.rng.random())))
    scenario.record("x", "ev", attr="x")

    explicit = scenario.run({"seed": 5}, seed=9)
    assert explicit.seed == {"entropy": 9, "spawn_key": []}
    from_params = scenario.run({"seed": 5})
    assert from_params.seed == {"entropy": 5, "spawn_key": []}
    fresh = scenario.run()
    assert fresh.seed["entropy"] not in (5, 9)
    replay = scenario.run(seed=fresh.seed)
    assert replay.checksum() == fresh.checksum()
    tree = scenario.run(seed=SeedTree(9))
    assert tree.checksum() == explicit.checksum()
    child = scenario.run(seed=SeedTree(9).child("replication", 1))
    assert child.seed["spawn_key"] != [] and child.checksum() != explicit.checksum()


def test_entity_context_repr_and_attributes():
    captured = []

    def factory(e):
        captured.append(e)
        return Plain()

    scenario = small()
    scenario.group("ev", 2, factory)
    scenario.run(seed=1)
    e = captured[1]
    assert isinstance(e, EntityContext)
    assert (e.name, e.group, e.index, e.count) == ("ev_1", "ev", 1, 2)
    assert e.p is e.params and e.time.horizon_s == 3 * 3600
    assert e.world is not None and "ev_1" in repr(e)


def test_comm_config_is_effective_config():
    seen = []
    scenario = small()
    scenario.group("ev", 1, lambda e: Plain())

    @scenario.communication
    def comm(ctx):
        seen.append(ctx.comm)
        return SimpleCommunicationSimulation()

    scenario.run(seed=1)
    assert isinstance(seen[0], CommConfig)


# ----------------------------------------------------------------- mistakes caught early


def test_every_must_be_a_multiple_of_a_fixed_step():
    scenario = small()
    nodes = scenario.group("node", 1, lambda e: Plain())
    scenario.record("v", nodes, attr="aid", every="90min")
    with pytest.raises(
        ScenarioError, match=r"every 1h30min is not a multiple of the step 1h"
    ) as info:
        scenario.plan()
    assert "use every='1h' or '2h'" in str(info.value)
    scenario = small()
    scenario.record_world("w", lambda: 1, every="30min")
    with pytest.raises(ScenarioError, match="use every='1h', or omit every"):
        scenario.plan()
    scenario = small()
    scenario.record_world("w", lambda: 1, every="2h")
    assert scenario.run(seed=1).ts("w").index.tolist() == [0.0, 7200.0]


def test_a_group_decorator_that_is_never_applied_fails():
    scenario = small()
    pending = scenario.group(
        "coordinator", lambda e: Plain()
    )  # factory in the count slot
    assert callable(pending)
    with pytest.raises(
        ScenarioError, match=r"scenario.group\('coordinator', 1, factory\)"
    ):
        scenario.plan()

    @scenario.group("coordinator", 1)
    def coordinator(e):
        return Plain()

    assert scenario.plan().group("coordinator").count == 1


def test_an_agent_class_is_not_a_factory():
    scenario = Scenario("x")
    with pytest.raises(
        TypeError, match=r"lambda e: Plain\(\.\.\.\), not the class Plain"
    ):
        scenario.group("ev", 2, Plain)
