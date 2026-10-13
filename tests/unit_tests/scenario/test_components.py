import importlib.metadata
import json
import sys

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from pydantic import BaseModel  # noqa: E402

from mango import (  # noqa: E402
    Agent,
    Area2D,
    DefaultEnvironment,
    SeedTree,
    create_world,
)
from mango.scenario import (  # noqa: E402
    ComponentContext,
    CompositeBehavior,
    ParamsError,
    Profile,
    Scenario,
    ScenarioComponent,
    ScenarioError,
    ScenarioParams,
    Uniform,
    check_path,
    component_kinds,
    load_params,
    params_schema,
    register_component,
    resolve_component,
)
from mango.scenario import components as components_module  # noqa: E402
from mango.simulation import Behavior  # noqa: E402

# ----------------------------------------------------------------- helpers

PROFILES = {
    "h0": {"source": "inline", "values": [1.0, 2.0, 3.0, 4.0], "resolution": "1h"},
    "pv": {"source": "inline", "values": [0.0, 0.5, 1.0, 0.5], "resolution": "1h"},
}
TIME = {"start": "2024-06-01T00:00", "horizon": "2h", "step": "15min"}


class FeederBehavior(Behavior):
    def __init__(self, n_buses, r_pu_per_kw):
        self.n_buses = n_buses
        self.r_pu_per_kw = r_pu_per_kw
        self.import_kw = 0.0
        self.voltages = [1.0] * n_buses
        self.members = []

    def install(self, agent, **kwargs):
        self.members.append((agent, kwargs.get("bus")))

    def on_step(self, environment, clock, step_size_s):
        self.import_kw = sum(getattr(a, "net_kw", 0.0) for a, _ in self.members)
        self.voltages = [
            1.0 - self.r_pu_per_kw * self.import_kw * (bus + 1)
            for bus in range(self.n_buses)
        ]


class HouseholdAgent(Agent):
    def __init__(self, pv_peak_kw, annual_kwh, load_profile, pv_profile, step_s):
        super().__init__()
        self.pv_peak_kw = pv_peak_kw
        self.annual_kwh = annual_kwh
        self.load_profile = load_profile
        self.pv_profile = pv_profile
        self.step_s = step_s
        self.pv_kw = 0.0
        self.net_kw = 0.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.pv_kw = self.pv_peak_kw * self.pv_profile.at(clock)
        self.net_kw = self.annual_kwh / 8760 * self.load_profile.at(clock) - self.pv_kw


class CoordinatorAgent(Agent):
    def handle_message(self, content, meta):
        pass


class Neighbourhood(ScenarioComponent):
    """The kit of the side-by-side example, verbatim."""

    class Params(BaseModel):
        n_buses: int = 12
        r_pu_per_kw: float = 0.0006
        n_households: int = 30
        pv_share: float = 0.6
        pv_peak_kw: Uniform = Uniform(3.0, 8.0)
        annual_kwh: Uniform = Uniform(2500.0, 5000.0)

    def build(self, ctx):
        p = ctx.p
        feeder = ctx.set_behavior(FeederBehavior(p.n_buses, p.r_pu_per_kw))
        homes = ctx.group(
            "hh",
            p.n_households,
            lambda e: HouseholdAgent(
                pv_peak_kw=p.pv_peak_kw.draw(e.rng)
                if e.rng.random() < p.pv_share
                else 0.0,
                annual_kwh=p.annual_kwh.draw(e.rng),
                load_profile=ctx.profile("h0"),
                pv_profile=ctx.profile("pv"),
                step_s=ctx.time.step_s,
            ),
            install=lambda e: {"bus": e.index},
        )
        ctx.record("pv_kw", homes, lambda a: a.pv_kw)
        ctx.record_world("import_kw", lambda: feeder.import_kw)
        ctx.record_world("min_voltage_pu", lambda: min(feeder.voltages))


class Params(ScenarioParams):
    algorithm: str = "greedy"


def ev_feeder(n_households=4):
    scenario = Scenario("ev_feeder", params=Params)
    scenario.use(Neighbourhood(n_households=n_households), prefix="grid")
    coordinator = scenario.group("coordinator", 1, lambda e: CoordinatorAgent())
    scenario.link("reports", "grid.hh", coordinator, pattern="many_to_one")
    return scenario


BASE = {"time": TIME, "profiles": PROFILES}


class Plain(Agent):
    def handle_message(self, content, meta):
        pass


class Noisy(Behavior):
    def __init__(self, log=None, name="", rng_key=()):
        self.rng = None
        self.rng_key = rng_key
        self.log = log if log is not None else []
        self.name = name
        self.installed = []

    def initialize(self, environment, clock):
        self.log.append(("init", self.name))

    def on_step(self, environment, clock, step_size_s):
        self.log.append(("step", self.name))

    def install(self, agent, **kwargs):
        self.installed.append(agent.aid)


class EVFleet(ScenarioComponent):
    class Params(BaseModel, extra="forbid"):
        n: int = 2

    def build(self, ctx):
        ctx.group("ev", ctx.p.n, lambda e: Plain())


class Empty(ScenarioComponent):
    pass


# ----------------------------------------------------------------- side-by-side shape


def test_side_by_side_neighbourhood_kit_runs_as_written():
    scenario = ev_feeder()
    result = scenario.run(BASE, seed=3, overrides=["grid.pv_share=1.0"])
    run_entities = result.entities
    assert [n for n, g in run_entities.items() if g == "grid.hh"] == [
        f"grid.hh_{i}" for i in range(4)
    ]
    world = result.world
    feeder = world.environment.behavior
    assert isinstance(feeder, FeederBehavior)  # one behavior: installed directly
    # every agent is installed; households carry their bus
    assert [bus for _, bus in feeder.members] == [0, 1, 2, 3, None]
    frame = result.frame
    world_rows = frame[frame.variable.isin(["import_kw", "min_voltage_pu"])]
    assert set(world_rows.group) == {"grid"}
    assert set(world_rows.entity) == {"grid"}
    # the world recorder reads the very behavior the environment steps
    assert result.ts("import_kw").iloc[-1] == pytest.approx(feeder.import_kw)
    assert set(frame[frame.variable == "pv_kw"].group) == {"grid.hh"}
    assert result.ts("pv_kw").max() > 0
    hh = result.agent("grid.hh_0")
    assert isinstance(hh.load_profile, Profile)
    assert hh.load_profile is result.agent("grid.hh_1").load_profile
    assert world.environment.behavior is feeder
    neighbours = {a for a in result.agent("coordinator").neighbors(tid="reports")}
    assert len(neighbours) == 4


def test_kit_params_nest_under_prefix_with_init_kwargs_as_defaults():
    scenario = ev_feeder(n_households=7)
    model = scenario.params_model
    assert "grid" in model.model_fields
    assert issubclass(model, Params)
    assert model.__name__ == "Params"
    params = load_params(scenario, BASE)
    assert isinstance(params.grid, Neighbourhood.Params)
    assert params.grid.n_households == 7
    assert params.grid.pv_share == 0.6
    plan = scenario.plan(BASE)
    assert plan.group("grid.hh").count == 7
    assert plan.group("grid.hh").names[0] == "grid.hh_0"
    assert scenario.kits["grid"].params.n_households == 7


def test_overrides_and_presets_reach_kit_params():
    scenario = ev_feeder()
    params = scenario.resolve_params(
        [BASE, {"grid": {"n_buses": 3}}], overrides=["grid.pv_share=0.8"]
    )
    assert params.grid.pv_share == 0.8
    assert params.grid.n_buses == 3
    assert params.grid.n_households == 4
    check_path(scenario, "grid.pv_peak_kw.low")
    with pytest.raises(ParamsError, match="did you mean 'grid.pv_share'"):
        scenario.resolve_params(BASE, overrides=["grid.pv_shar=0.8"])
    with pytest.raises(ParamsError, match="pv_shre"):
        scenario.resolve_params([BASE, {"grid": {"pv_shre": 0.8}}])
    params = scenario.resolve_params(BASE, overrides=["grid.pv_peak_kw=[1, 2]"])
    assert params.grid.pv_peak_kw == Uniform(1, 2)
    scenario.defaults({"grid.n_households": 9})
    assert scenario.plan(BASE).group("grid.hh").count == 9


def test_params_schema_includes_kit_params():
    schema = params_schema(ev_feeder())
    grid = schema["properties"]["grid"]
    ref = grid.get("$ref") or grid["allOf"][0]["$ref"]
    definition = schema["$defs"][ref.rsplit("/", 1)[-1]]
    assert "n_households" in definition["properties"]
    assert grid["default"]["n_households"] == 4


def test_init_kwargs_are_validated_immediately():
    with pytest.raises(ParamsError, match="did you mean 'n_households'") as info:
        Neighbourhood(n_houshold=40)
    assert info.value.path == "n_houshold"
    assert "Neighbourhood()" in str(info.value)
    with pytest.raises(ParamsError, match="pv_share"):
        Neighbourhood(pv_share="lots")
    with pytest.raises(ParamsError, match="low"):
        Neighbourhood(pv_peak_kw={"uniform": [5, 1]})
    kit = Neighbourhood(pv_peak_kw={"uniform": [1, 2]}, n_households=3)
    assert kit.params.pv_peak_kw == Uniform(1, 2)
    assert kit.init_kwargs == {"pv_peak_kw": {"uniform": [1, 2]}, "n_households": 3}
    assert "Neighbourhood(" in repr(kit)
    with pytest.raises(AttributeError, match="ctx.p"):
        kit.p  # noqa: B018


def test_default_name_is_snake_case_and_explicit_names_are_checked():
    assert Neighbourhood.name == "neighbourhood"
    assert EVFleet.name == "ev_fleet"

    class PandapowerGrid(ScenarioComponent):
        pass

    assert PandapowerGrid.name == "pandapower_grid"
    with pytest.raises(ScenarioError, match="valid kit prefix"):

        class Bad(ScenarioComponent):
            name = "my-kit"

    with pytest.raises(TypeError, match="BaseModel"):

        class BadParams(ScenarioComponent):
            Params = dict


def test_use_rejects_bad_components_and_prefixes():
    scenario = Scenario("s", params=Params)
    with pytest.raises(TypeError, match=r"Neighbourhood\(\.\.\.\)"):
        scenario.use(Neighbourhood)
    with pytest.raises(TypeError, match="ScenarioComponent"):
        scenario.use(object())
    with pytest.raises(ScenarioError, match="valid kit prefix"):
        scenario.use(EVFleet(), prefix="a.b")
    with pytest.raises(ScenarioError, match="clashes with the field 'algorithm'"):
        scenario.use(EVFleet(), prefix="algorithm")
    with pytest.raises(ScenarioError, match="clashes with the field 'time'"):
        scenario.use(EVFleet(), prefix="time")
    assert scenario.use(EVFleet(), prefix="fleet").name == "ev_fleet"
    with pytest.raises(ScenarioError, match="used twice"):
        scenario.use(EVFleet(n=3), prefix="fleet")


def test_kit_that_skips_super_init_fails_clearly():
    class Forgetful(ScenarioComponent):
        def __init__(self, n):
            self.n = n

    with pytest.raises(TypeError, match=r"super\(\).__init__"):
        Scenario("s").use(Forgetful(3))


def test_kit_without_params_adds_no_field():
    seen = {}

    class Probe(ScenarioComponent):
        def build(self, ctx):
            seen["p"] = ctx.p
            seen["ctx"] = ctx

    scenario = Scenario("s")
    scenario.use(Probe())
    scenario.use(Empty(), prefix="nothing")
    assert scenario.params_model is ScenarioParams
    scenario.plan()
    assert isinstance(seen["p"], Probe.Params)
    assert isinstance(seen["ctx"], ComponentContext)
    assert seen["ctx"].component is scenario.kits["probe"]
    assert "probe" in repr(seen["ctx"])


def test_compose_uses_default_prefixes_and_records_module():
    scenario = Scenario.compose(EVFleet(n=3), Empty(), name="ev", params=Params)
    assert scenario.name == "ev"
    assert list(scenario.kits) == ["ev_fleet", "empty"]
    assert scenario.__module__ == __name__
    plan = scenario.plan(overrides=["ev_fleet.n=5"])
    assert plan.group("ev_fleet.ev").count == 5
    assert Scenario.compose(EVFleet(), Empty()).name == "ev_fleet_empty"
    with pytest.raises(TypeError, match="ScenarioComponent"):
        Scenario.compose(EVFleet, name="x")


# ----------------------------------------------------------------- lifecycle and scope


def test_lifecycle_runs_all_builds_then_wires_then_records_in_declaration_order():
    log = []

    class Logger(ScenarioComponent):
        def __init__(self, tag):
            super().__init__()
            self.tag = tag

        def build(self, ctx):
            log.append(f"{self.tag}.build")
            ctx.group("g", 1, lambda e: Plain())

        def wire(self, ctx):
            log.append(f"{self.tag}.wire")

        def record(self, ctx):
            log.append(f"{self.tag}.record")

    def count(p):
        log.append("scenario.build")
        return 1

    scenario = Scenario("s")
    scenario.use(Logger("a"), prefix="a")
    scenario.group("mid", count, lambda e: Plain())
    scenario.use(Logger("b"), prefix="b")
    scenario.plan()
    assert log == [
        "a.build",
        "scenario.build",
        "b.build",
        "a.wire",
        "b.wire",
        "a.record",
        "b.record",
    ]


def test_kit_context_is_scoped_to_its_prefix():
    seen = {}

    class Scoped(ScenarioComponent):
        class Params(BaseModel, extra="forbid"):
            n: int = 2

        def build(self, ctx):
            seen["p"] = ctx.p
            seen["prefix"] = ctx.prefix
            seen["draw"] = ctx.rng("weather").random()
            ctx.group("hh", "n", lambda e: Plain())  # count path relative to ctx.p

        def wire(self, ctx):
            ctx.link("feeder", "hh", pattern="line")

        def record(self, ctx):
            ctx.record("aid", "hh", lambda a: a.aid)
            ctx.record_world("const", lambda: 1.0)

    scenario = Scenario("s")
    scenario.use(Scoped(n=3), prefix="grid")
    plan = scenario.plan()
    assert seen["prefix"] == "grid"
    assert seen["p"].n == 3
    assert seen["draw"] == SeedTree(0).rng("grid", "weather").random()
    assert plan.group("grid.hh").names == ["grid.hh_0", "grid.hh_1", "grid.hh_2"]
    assert plan.link("feeder").declarations[0]["source"] == "grid.hh"
    assert plan.link("feeder").edges == 2
    world_rec = [r for r in plan.recorders if r.kind == "world"][0]
    assert world_rec.entity == "grid"


def test_kit_hook_errors_name_the_kit():
    class Broken(ScenarioComponent):
        def wire(self, ctx):
            ctx.link("x", "missing")

    scenario = Scenario("s")
    scenario.use(Broken(), prefix="broken")
    with pytest.raises(
        ScenarioError, match="'missing' in link\\('x'\\) of kit 'broken'"
    ):
        scenario.plan()

    class Raising(ScenarioComponent):
        def record(self, ctx):
            raise ValueError("boom")

    scenario = Scenario("s")
    scenario.use(Raising(), prefix="raising")
    with pytest.raises(ValueError, match="boom") as info:
        scenario.plan()
    if sys.version_info >= (3, 11):
        assert "in Raising.record() of kit 'raising'" in info.value.__notes__


def test_fingerprint_includes_kit_class_kwargs_and_prefix():
    def make(n=2, prefix="fleet"):
        scenario = Scenario("s")
        scenario.use(EVFleet(n=n), prefix=prefix)
        return scenario

    assert make().fingerprint() == make().fingerprint()
    assert make(n=3).fingerprint() != make().fingerprint()
    assert make(prefix="other").fingerprint() != make().fingerprint()
    other = Scenario("s")
    other.use(Empty(), prefix="fleet")
    assert other.fingerprint() != make().fingerprint()


def test_run_id_covers_kit_params():
    scenario = ev_feeder()
    a = scenario.build(BASE, seed=1)
    b = scenario.build(BASE, seed=1, overrides=["grid.pv_share=0.8"])
    assert a.run_id != b.run_id
    assert "grid" in a.params.model_dump()


# ----------------------------------------------------------------- resources


def test_resources_are_shared_in_hooks_factories_and_recorders():
    seen = {}

    class Consumer(ScenarioComponent):
        def wire(self, ctx):
            seen["wire"] = ctx.resource("feeder")

        def record(self, ctx):
            ctx.record_world("from_resource", lambda: ctx.resource("feeder")["v"])

    class Provider(ScenarioComponent):
        def build(self, ctx):
            assert ctx.provide("feeder", {"v": 5.0}) == {"v": 5.0}
            ctx.group(
                "hh",
                2,
                lambda e: Plain(),
                install=lambda e: {"v": e.resource("feeder")["v"]},
            )

    scenario = Scenario("s")
    scenario.time(horizon="1h", step="1h")
    scenario.use(Consumer())
    scenario.use(Provider())
    plan = scenario.plan()
    assert plan.resources == {"provider.feeder": "provider"}  # scoped like groups
    assert seen["wire"] == {"v": 5.0}  # found by its local name from another kit
    result = scenario.run(seed=1)
    assert result.ts("from_resource").tolist() == [5.0, 5.0]


def test_resource_errors_are_precise():
    class EarlyReader(ScenarioComponent):
        def build(self, ctx):
            ctx.resource("feeder")

    class Provider(ScenarioComponent):
        def build(self, ctx):
            ctx.provide("feeder", 1)

    scenario = Scenario("s")
    scenario.use(EarlyReader())
    scenario.use(Provider())
    with pytest.raises(ScenarioError, match="kits declared earlier"):
        scenario.plan()

    class GlobalProvider(ScenarioComponent):
        def build(self, ctx):
            ctx.provide("feeder", 1, scope="global")

    twice = Scenario("s")
    twice.use(GlobalProvider(), prefix="a")
    twice.use(GlobalProvider(), prefix="b")
    with pytest.raises(ScenarioError, match="provided twice \\(by 'a' and 'b'\\)"):
        twice.plan()

    class Reader(ScenarioComponent):
        def wire(self, ctx):
            ctx.resource("feeder")

    ambiguous = Scenario("s")
    ambiguous.use(Provider(), prefix="a")
    ambiguous.use(Provider(), prefix="b")
    ambiguous.use(Reader())
    with pytest.raises(
        ScenarioError, match="several kits \\('a.feeder', 'b.feeder'\\)"
    ):
        ambiguous.plan()

    class BadScope(ScenarioComponent):
        def build(self, ctx):
            ctx.provide("x", 1, scope="world")

    bad = Scenario("s")
    bad.use(BadScope())
    with pytest.raises(ScenarioError, match="scope must be 'kit' or 'global'"):
        bad.plan()

    class Typo(ScenarioComponent):
        def wire(self, ctx):
            ctx.resource("feedr")

    typo = Scenario("s")
    typo.use(Provider())
    typo.use(Typo())
    with pytest.raises(ScenarioError, match="Did you mean 'feeder'.*'provider.feeder'"):
        typo.plan()

    late = Scenario("s")
    late.use(Provider())
    late.on_build(lambda ctx: ctx.provide("x", 1))
    late.group("g", 1, lambda e: Plain())
    with pytest.raises(ScenarioError, match="declaration stage"):
        late.run(seed=1)


# ----------------------------------------------------------------- behaviors and space


def test_two_kits_adding_behaviors_form_a_composite_in_order_with_own_streams():
    log = []

    class A(ScenarioComponent):
        def build(self, ctx):
            ctx.add_behavior(Noisy(log, "a0", ("a", 0)))
            ctx.add_behavior(Noisy(log, "a1", ("a", 1)))
            ctx.group("g", 1, lambda e: Plain())

    class B(ScenarioComponent):
        def build(self, ctx):
            ctx.set_behavior(Noisy(log, "b0", ("b", 0)))

    scenario = Scenario("s")
    scenario.time(horizon="1h", step="1h")
    scenario.use(A(), prefix="a")
    scenario.use(B(), prefix="b")
    plan = scenario.plan()
    assert plan.behaviors == ["Noisy (a)", "Noisy (a)", "Noisy (b)"]
    result = scenario.run(seed=11)
    composite = result.world.environment.behavior
    assert isinstance(composite, CompositeBehavior)
    assert [child.name for child in composite] == ["a0", "a1", "b0"]
    assert log == [
        ("init", "a0"),
        ("init", "a1"),
        ("init", "b0"),
        ("step", "a0"),
        ("step", "a1"),
        ("step", "b0"),
    ]
    tree = SeedTree(11)
    expected = {
        "a0": tree.rng("behavior", "a", 0),
        "a1": tree.rng("behavior", "a", 1),
        "b0": tree.rng("behavior", "b", 0),
    }
    for child in composite:
        assert child.rng is result.world.rng("behavior", *child.rng_key)
        assert child.rng.random() == expected[child.name].random()
    assert composite[0].installed == ["a.g"]


def test_behavior_streams_do_not_shift_when_a_kit_is_added():
    class A(ScenarioComponent):
        def build(self, ctx):
            ctx.add_behavior(Noisy(name="a"))
            ctx.group("g", 1, lambda e: Plain())

    class B(ScenarioComponent):
        def build(self, ctx):
            ctx.add_behavior(Noisy(name="b"))

    def first_draw(*kits):
        scenario = Scenario("s")
        scenario.time(horizon="1h", step="1h")
        for kit in kits:
            scenario.use(kit)
        behavior = scenario.run(seed=5).world.environment.behavior
        children = (
            list(behavior) if isinstance(behavior, CompositeBehavior) else [behavior]
        )
        return {child.name: child.rng.random() for child in children}

    assert first_draw(A())["a"] == first_draw(B(), A())["a"]


def test_explicit_behavior_rng_is_kept():
    import numpy as np

    mine = np.random.default_rng(99)

    class A(ScenarioComponent):
        def build(self, ctx):
            behavior = Noisy(name="a")
            behavior.rng = mine
            ctx.add_behavior(behavior)
            ctx.group("g", 1, lambda e: Plain())

    scenario = Scenario("s")
    scenario.time(horizon="1h", step="1h")
    scenario.use(A())
    assert scenario.run(seed=1).world.environment.behavior.rng is mine


def test_custom_environment_factory_must_install_the_kit_behaviors():
    class A(ScenarioComponent):
        def build(self, ctx):
            ctx.add_behavior(Noisy(name="a"))
            ctx.group("g", 1, lambda e: Plain())

    scenario = Scenario("s")
    scenario.time(horizon="1h", step="1h")
    scenario.use(A())
    scenario.environment(lambda ctx: DefaultEnvironment())
    with pytest.raises(ScenarioError, match="behavior=ctx.behavior"):
        scenario.run(seed=1)
    scenario.environment(lambda ctx: DefaultEnvironment(behavior=ctx.behavior))
    assert scenario.run(seed=1).world.environment.behavior.name == "a"
    extra = Noisy(name="extra")
    scenario.environment(
        lambda ctx: DefaultEnvironment(
            behavior=CompositeBehavior([extra, ctx.behavior])
        )
    )
    behavior = scenario.run(seed=1).world.environment.behavior
    assert [child.name for child in behavior] == ["extra", "a"]


def test_space_from_a_kit_and_custom_factory_detection():
    class Spatial(ScenarioComponent):
        def build(self, ctx):
            ctx.set_space(Area2D(10, 10))
            ctx.group("g", 2, lambda e: Plain())

    scenario = Scenario("s")
    scenario.time(horizon="1h", step="1h")
    scenario.use(Spatial())
    world = scenario.run(seed=1).world
    assert isinstance(world.environment.space, Area2D)
    scenario.environment(lambda ctx: DefaultEnvironment(behavior=ctx.behavior))
    with pytest.raises(ScenarioError, match="space=ctx.space"):
        scenario.run(seed=1)

    class Twice(ScenarioComponent):
        def build(self, ctx):
            ctx.set_space(Area2D(1, 1))

    two = Scenario("s")
    two.use(Spatial())
    two.use(Twice())
    with pytest.raises(ScenarioError, match="space is set twice"):
        two.plan()


def test_behavior_and_space_argument_errors():
    class BadBehavior(ScenarioComponent):
        def build(self, ctx):
            ctx.add_behavior(object())

    class BadSpace(ScenarioComponent):
        def build(self, ctx):
            ctx.set_space("plane")

    class Repeat(ScenarioComponent):
        def build(self, ctx):
            behavior = Noisy()
            ctx.add_behavior(behavior)
            ctx.add_behavior(behavior)

    class Early(ScenarioComponent):
        def build(self, ctx):
            ctx.behavior  # noqa: B018

    for kit, error, match in [
        (BadBehavior(), TypeError, "Behavior"),
        (BadSpace(), TypeError, "Space"),
        (Repeat(), ScenarioError, "added twice"),
        (Early(), ScenarioError, "environment factory"),
    ]:
        scenario = Scenario("s")
        scenario.use(kit)
        with pytest.raises(error, match=match):
            scenario.plan()


@pytest.mark.asyncio
async def test_composite_behavior_standalone_binds_child_streams():
    log = []
    first, second = Noisy(log, "x"), Noisy(log, "y")
    composite = CompositeBehavior([first, second])
    assert len(composite) == 2 and composite[1] is second
    assert "Noisy, Noisy" in repr(composite)
    world = create_world(environment=DefaultEnvironment(behavior=composite), seed=4)
    tree = SeedTree(4)
    assert first.rng.random() == tree.rng("behavior", 0).random()
    assert second.rng.random() == tree.rng("behavior", 1).random()
    agent = world.register(Plain(), suggested_aid="p")
    assert first.installed == ["p"] and second.installed == ["p"]
    keyed = CompositeBehavior([Noisy(), Noisy()], keys=["w", ("v", 2)])
    keyed.rng = SeedTree(4).rng("behavior")
    assert keyed[1].rng.random() == SeedTree(4).rng("behavior", "v", 2).random()
    assert agent.aid == "p"
    await world.shutdown()
    with pytest.raises(ValueError, match="2 keys for 1"):
        CompositeBehavior([Noisy()], keys=[0, 1])
    with pytest.raises(TypeError, match="child 0"):
        CompositeBehavior([object()])


# ----------------------------------------------------------------- plan


def test_plan_lists_kits_resources_and_behaviors():
    scenario = ev_feeder()
    scenario.use(EVFleet(n=1), prefix="fleet")
    plan = scenario.plan(BASE)
    assert [kit.prefix for kit in plan.kits] == ["grid", "fleet"]
    assert plan.kits[0].component == "Neighbourhood"
    assert plan.kits[0].ref.endswith(":Neighbourhood")
    assert plan.kits[0].params["n_households"] == 4
    assert plan.behaviors == ["FeederBehavior (grid)"]
    text = str(plan)
    assert "kits:" in text and "behaviors: FeederBehavior (grid)" in text
    data = json.loads(json.dumps(plan.to_dict()))
    assert data["kits"][1]["params"] == {"n": 1}
    assert data["behaviors"] == ["FeederBehavior (grid)"]


# ----------------------------------------------------------------- registry


class RegisteredKit(ScenarioComponent):
    pass


class FakeEntryPoint:
    def __init__(self, name, value, obj=None, error=None):
        self.name, self.value, self.group = name, value, "mango.scenario.components"
        self._obj, self._error = obj, error

    def load(self):
        if self._error is not None:
            raise self._error
        return self._obj


@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(
        components_module, "_REGISTRY", dict(components_module._REGISTRY)
    )


def test_register_and_resolve_components(clean_registry):
    @register_component("my_kit")
    class MyKit(ScenarioComponent):
        pass

    assert resolve_component("my_kit") is MyKit
    assert resolve_component(MyKit) is MyKit
    assert register_component(RegisteredKit) is RegisteredKit
    assert resolve_component("registered_kit") is RegisteredKit
    register_component("my_kit")(MyKit)  # same class again: fine
    with pytest.raises(ScenarioError, match="already registered"):
        register_component("my_kit")(RegisteredKit)
    with pytest.raises(ScenarioError, match="':'"):
        register_component("a:b")
    with pytest.raises(ScenarioError, match="not a ScenarioComponent"):
        register_component("plain")(Plain)
    kinds = component_kinds()
    assert kinds["my_kit"].endswith(
        ":test_register_and_resolve_components.<locals>.MyKit"
    )
    assert "agent_population" in kinds and "communication_preset" in kinds


def test_resolve_component_by_reference_and_errors(clean_registry):
    ref = f"{__name__}:RegisteredKit"
    assert resolve_component(ref) is RegisteredKit
    with pytest.raises(ScenarioError, match="not a ScenarioComponent"):
        resolve_component(f"{__name__}:Plain")
    with pytest.raises(ScenarioError, match="has no attribute|Did you mean"):
        resolve_component(f"{__name__}:Missing")
    with pytest.raises(ScenarioError, match="Did you mean 'agent_population'") as info:
        resolve_component("agent_populaton")
    assert "communication_preset" in str(info.value)
    with pytest.raises(TypeError):
        resolve_component("")
    with pytest.raises(ScenarioError, match="not a ScenarioComponent"):
        resolve_component(Plain)


def test_resolve_component_from_entry_points(clean_registry, monkeypatch):
    calls = []

    def entry_points(**kwargs):
        calls.append(kwargs)
        return [
            FakeEntryPoint("grid_kit", "pkg.kits:GridKit", obj=RegisteredKit),
            FakeEntryPoint("broken_kit", "pkg.kits:Broken", error=ImportError("nope")),
            FakeEntryPoint("not_a_kit", "pkg.kits:thing", obj=len),
        ]

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    assert resolve_component("grid_kit") is RegisteredKit
    assert calls[0] == {"group": "mango.scenario.components"}
    assert component_kinds()["grid_kit"] == "pkg.kits:GridKit"
    with pytest.raises(ScenarioError, match="loading entry point 'pkg.kits:Broken'"):
        resolve_component("broken_kit")
    with pytest.raises(ScenarioError, match="not a ScenarioComponent"):
        resolve_component("not_a_kit")
    with pytest.raises(ScenarioError, match="Did you mean 'grid_kit'"):
        resolve_component("grid_kti")
    # registered kinds win over entry points of the same name
    register_component("grid_kit")(EVFleet)
    assert resolve_component("grid_kit") is EVFleet


def test_broken_entry_point_metadata_does_not_break_lookups(
    clean_registry, monkeypatch
):
    def entry_points(**kwargs):
        raise RuntimeError("corrupt metadata")

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    assert resolve_component("agent_population").__name__ == "AgentPopulation"
    assert "agent_population" in component_kinds()


# ----------------------------------------------------------------- reuse under prefixes


class Meter(ScenarioComponent):
    """A kit with a world recorder, used twice in the reuse tests."""

    class Params(BaseModel, extra="forbid"):
        n: int = 1
        load: float = 1.0

    def build(self, ctx):
        ctx.group("hh", ctx.p.n, lambda e: Plain())
        load = ctx.p.load
        ctx.record_world("feeder_kw", lambda: load * 2)


def test_a_kit_with_world_recorders_can_be_used_under_two_prefixes():
    scenario = Scenario("two")
    scenario.time(horizon=2, step=1)
    scenario.use(Meter(), prefix="north")
    scenario.use(Meter(load=3.0), prefix="south")
    scenario.record_world("feeder_kw", lambda: 0.0)  # entity "world"
    result = scenario.run(seed=1)
    assert result.variables() == ["feeder_kw"]
    rows = result.frame[result.frame["variable"] == "feeder_kw"]
    assert list(dict.fromkeys(rows["entity"])) == ["north", "south", "world"]
    assert result.ts("feeder_kw", entity="south").tolist() == [6.0, 6.0, 6.0]
    assert result.ts("feeder_kw", entity="north").tolist() == [2.0, 2.0, 2.0]
    assert result.ts("feeder_kw", group="world").tolist() == [0.0, 0.0, 0.0]
    recorders = [r for r in scenario.plan().recorders if r.kind == "world"]
    assert [r.entity for r in recorders] == ["north", "south", "world"]


def test_one_world_variable_of_one_entity_is_still_declared_once():
    scenario = Scenario("dup")
    scenario.use(Meter(), prefix="north")
    scenario.record_world("feeder_kw", lambda: 0.0, entity="north")
    with pytest.raises(
        ScenarioError,
        match="'feeder_kw' of entity 'north' is declared twice "
        "\\(by kit 'north' and the scenario\\)",
    ):
        scenario.plan()


def test_a_kits_own_group_wins_over_a_scenario_group_of_the_same_name():
    class Ring(ScenarioComponent):
        def build(self, ctx):
            ctx.group("hh", 2, lambda e: Plain())

        def wire(self, ctx):
            ctx.link("kit_ring", "hh", pattern="complete")
            ctx.link("to_coordinator", "hh", "coordinator", pattern="many_to_one")

        def record(self, ctx):
            ctx.record("aid", "hh", lambda a: a.aid)

    scenario = Scenario("shadow")
    scenario.group("hh", 3, lambda e: Plain())
    scenario.group("coordinator", 1, lambda e: Plain())
    scenario.use(Ring(), prefix="grid")
    plan = scenario.plan()
    assert plan.link("kit_ring").declarations[0]["source"] == "grid.hh"
    assert plan.link("kit_ring").edges == 1
    assert plan.link("to_coordinator").declarations[0]["target"] == "coordinator"
    assert [r.group for r in plan.recorders] == ["grid.hh"]


def test_unknown_group_errors_suggest_the_qualified_name():
    scenario = Scenario("s")
    scenario.use(EVFleet(), prefix="fleet")
    scenario.group("other", 1, lambda e: Plain())
    scenario.link("x", "ev", pattern="complete")
    with pytest.raises(ScenarioError, match="Did you mean 'fleet.ev'"):
        scenario.plan()


@pytest.mark.parametrize("prefix", sorted(components_module.RESERVED_PREFIXES))
def test_stream_root_prefixes_are_reserved(prefix):
    with pytest.raises(ScenarioError, match=f"{prefix!r} is reserved"):
        Scenario("s").use(EVFleet(), prefix=prefix)


def test_reserved_default_prefix_fails_at_use():
    class Link(ScenarioComponent):
        pass

    with pytest.raises(ScenarioError, match="prefix=... in Python"):
        Scenario("s").use(Link())


def test_a_kit_without_params_may_use_a_params_field_name_as_prefix():
    from mango.scenario.kits import CommunicationPreset

    scenario = Scenario("s")
    scenario.use(CommunicationPreset("lte"), prefix="comm")
    assert (
        repr(scenario.resolve_params().comm.delay_s) == "Normal(0.04, 0.008, low=0.0)"
    )
    assert scenario.plan().kits[0].prefix == "comm"
    with pytest.raises(
        ScenarioError, match="clashes with the field 'comm'.*Use another"
    ):
        Scenario("s").use(EVFleet(), prefix="comm")


def test_params_of_a_kit_scenario_pickle_and_keep_their_class():
    import copy
    import pickle

    scenario = ev_feeder()
    params = scenario.resolve_params(BASE, overrides=["grid.pv_share=0.25"])
    restored = pickle.loads(pickle.dumps(params))
    assert type(restored) is type(params) and restored == params
    assert restored.grid.pv_share == 0.25
    assert copy.deepcopy(params) == params
    assert ev_feeder().params_model is scenario.params_model  # equal kits, one class
    assert ev_feeder(n_households=5).params_model is not scenario.params_model


def test_fingerprint_does_not_depend_on_the_module_of_params_or_kit_classes():
    def make(module):
        kit_cls = type("Grid", (EVFleet,), {"__module__": module})
        params_cls = type("Params", (ScenarioParams,), {"__module__": module})
        scenario = Scenario("s", params=params_cls)
        scenario.use(kit_cls(), prefix="grid")
        return scenario

    assert make("__main__").fingerprint() == make("__mp_main__").fingerprint()
    assert make("__main__").fingerprint() == make("studies.run").fingerprint()


def test_scenario_files_can_use_the_comm_key_for_a_communication_preset(tmp_path):
    from mango.scenario import load_scenario

    path = tmp_path / "c.yaml"
    path.write_text(
        "name: c\ncomponents:\n  comm: {kind: communication_preset, preset: lan}\n"
    )
    assert load_scenario(path).resolve_params().comm.delay_s == 0.001


def test_reading_scenario_params_through_ctx_p_points_to_ctx_params():
    class Reader(ScenarioComponent):
        class Params(BaseModel, extra="forbid"):
            n: int = 1

        def build(self, ctx):
            ctx.p.algorithm  # noqa: B018  (a scenario-level field)

    scenario = Scenario("s", params=Params)
    scenario.use(Reader(), prefix="reader")
    with pytest.raises(AttributeError) as info:
        scenario.plan()
    notes = getattr(info.value, "__notes__", [])
    assert "in Reader.build() of kit 'reader'" in notes
    assert any("ctx.params.algorithm" in note for note in notes)
