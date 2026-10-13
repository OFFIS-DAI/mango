import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    AgentPopulation,
    CommConfig,
    CommunicationPreset,
    Normal,
    ParamsError,
    Scenario,
    ScenarioError,
    load_params,
    resolve_component,
)
from mango.scenario.kits import COMM_PRESETS  # noqa: E402
from mango.simulation import (  # noqa: E402
    DelayProviderCommunicationSimulation,
    SimpleCommunicationSimulation,
)


class Sensor(Agent):
    def __init__(self, period_s=1.0, label="s"):
        super().__init__()
        self.period_s = period_s
        self.label = label

    def handle_message(self, content, meta):
        pass


def make_sensor(e):
    return Sensor(period_s=e.p.kwargs.get("period_s", 5.0), label=e.name)


def sensor_install(e):
    return {"slot": e.index * 10}


def not_a_factory(e):
    return 1


NOT_CALLABLE = 42


def quick(name="s"):
    scenario = Scenario(name)
    scenario.time(horizon="1h", step="1h")
    return scenario


# ----------------------------------------------------------------- AgentPopulation


def test_agent_population_with_agent_class_and_kwargs():
    scenario = quick()
    scenario.use(
        AgentPopulation(
            factory=f"{__name__}:Sensor", count=3, kwargs={"period_s": 60.0}
        ),
        prefix="sensors",
    )
    plan = scenario.plan()
    assert plan.group("sensors.agent").names == [
        "sensors.agent_0",
        "sensors.agent_1",
        "sensors.agent_2",
    ]
    result = scenario.run(seed=1, overrides=["sensors.count=2"])
    agents = [result.agent(name) for name in ("sensors.agent_0", "sensors.agent_1")]
    assert all(isinstance(a, Sensor) and a.period_s == 60.0 for a in agents)
    assert result.groups() == ["sensors.agent"]


def test_agent_population_with_function_factory_install_and_link():
    scenario = quick()
    scenario.use(
        AgentPopulation(
            factory=make_sensor,  # stored as its import reference
            count=4,
            group="node",
            install=sensor_install,
            kwargs={"period_s": 2.0},
            link="mesh",
            pattern="ring",
        ),
        prefix="pop",
    )
    kit = scenario.kits["pop"]
    assert kit.params.factory == f"{__name__}:make_sensor"
    # callables in kit arguments enter the fingerprint by name, not by address
    assert "0x" not in repr(scenario._declarations[0].fingerprint)
    plan = scenario.plan()
    assert plan.link("mesh").edges == 4
    assert plan.link("mesh").declarations[0]["source"] == "pop.node"
    result = scenario.run(seed=2)
    sensor = result.agent("pop.node_1")
    assert sensor.period_s == 2.0 and sensor.label == "pop.node_1"
    assert len(sensor.neighbors(tid="mesh")) == 2


def test_agent_population_links_to_other_groups_and_random_patterns():
    scenario = quick()
    hub = scenario.group("hub", 1, lambda e: Sensor())
    scenario.use(
        AgentPopulation(
            factory=f"{__name__}:Sensor",
            count=3,
            link="reports",
            pattern="many_to_one",
            target=hub.name,
        ),
        prefix="pop",
    )
    scenario.use(
        AgentPopulation(
            factory=f"{__name__}:Sensor",
            count=5,
            link="peers",
            pattern={"random": {"p": 0.5}},
        ),
        prefix="crowd",
    )
    plan = scenario.plan()
    assert plan.link("reports").edges == 3
    assert plan.link("reports").declarations[0]["target"] == "hub"
    assert plan.link("peers").edges is None  # random: unknown before the run
    assert len(scenario.run(seed=3).agent("hub").neighbors(tid="reports")) == 3


def test_agent_population_validation_errors():
    with pytest.raises(ParamsError, match="factory"):
        AgentPopulation(count=2)
    with pytest.raises(ParamsError, match="cannot be referenced"):
        AgentPopulation(factory=lambda e: Sensor())
    with pytest.raises(ParamsError, match="count"):
        AgentPopulation(factory=f"{__name__}:Sensor", count=-1)
    with pytest.raises(ParamsError, match="pattern"):
        AgentPopulation(factory=f"{__name__}:Sensor", pattern="spiral")
    with pytest.raises(ParamsError, match="group"):
        AgentPopulation(factory=f"{__name__}:Sensor", group="a.b")
    with pytest.raises(ParamsError, match="did you mean 'count'"):
        AgentPopulation(factory=f"{__name__}:Sensor", cuont=3)

    for factory, match in [
        (f"{__name__}:Missing", "has no attribute|Did you mean"),
        (f"{__name__}:NOT_CALLABLE", "not an Agent class"),
    ]:
        scenario = quick()
        scenario.use(AgentPopulation(factory=factory))
        with pytest.raises(ScenarioError, match=match):
            scenario.plan()

    scenario = quick()
    scenario.use(AgentPopulation(factory=f"{__name__}:Sensor", target="hub"))
    with pytest.raises(ScenarioError, match="needs a link name"):
        scenario.plan()


def test_agent_population_factory_returning_non_agent_fails_at_build():
    scenario = quick()
    scenario.use(AgentPopulation(factory=f"{__name__}:not_a_factory"))
    with pytest.raises(TypeError, match="must return a mango Agent"):
        scenario.run(seed=1)


# ----------------------------------------------------------------- CommunicationPreset


@pytest.mark.parametrize("preset", sorted(COMM_PRESETS))
def test_communication_presets_set_comm_defaults(preset):
    scenario = quick()
    kit = scenario.use(CommunicationPreset(preset))
    assert "communication" not in scenario.params_model.model_fields
    params = load_params(scenario)
    assert params.comm == kit.comm
    assert params.comm == CommConfig.model_validate(COMM_PRESETS[preset])


def test_communication_preset_values_and_simulations():
    lte = CommunicationPreset("lte").comm
    assert lte.delay_s == Normal(0.04, 0.008, low=0.0)
    assert CommunicationPreset("lan").comm.delay_s == 0.001
    assert CommunicationPreset("lossy").comm.loss_percent == 0.1
    assert CommunicationPreset("ideal").comm.kind == "ideal"

    scenario = quick()
    scenario.group("a", 2, lambda e: Sensor())
    scenario.use(CommunicationPreset("lte"))
    run = scenario.build(seed=1)
    assert run.params.comm.delay_s == Normal(0.04, 0.008, low=0.0)
    world = scenario.run(seed=1).world
    assert isinstance(world.communication_sim, DelayProviderCommunicationSimulation)

    scenario = quick()
    scenario.group("a", 2, lambda e: Sensor())
    scenario.use(CommunicationPreset("lan"))
    world = scenario.run(seed=1).world
    assert isinstance(world.communication_sim, SimpleCommunicationSimulation)


def test_presets_and_overrides_beat_the_communication_preset():
    scenario = quick()
    scenario.use(CommunicationPreset("lossy", loss_percent=0.2))
    assert load_params(scenario).comm.loss_percent == 0.2
    params = load_params(scenario, {"comm": {"delay_s": 0.5}})
    assert params.comm.loss_percent == 0.2 and params.comm.delay_s == 0.5
    params = load_params(scenario, overrides=["comm.loss_percent=0.0"])
    assert params.comm.loss_percent == 0.0
    params = load_params(scenario, {"comm": {"kind": "ideal"}})
    assert params.comm.kind == "ideal"


def test_communication_preset_errors_and_identity():
    with pytest.raises(ParamsError, match="Did you mean 'lte'"):
        CommunicationPreset("lt")
    with pytest.raises(ParamsError, match="Did you mean 'loss_percent'"):
        CommunicationPreset("lan", loss_precent=0.1)
    with pytest.raises(ParamsError, match="loss_percent"):
        CommunicationPreset("lan", loss_percent=2.0)
    kit = CommunicationPreset("lte", loss_percent=0.01)
    assert kit.init_kwargs == {"preset": "lte", "loss_percent": 0.01}
    assert kit.preset == "lte"
    a, b = quick(), quick()
    a.use(CommunicationPreset("lte"))
    b.use(CommunicationPreset("lan"))
    assert a.fingerprint() != b.fingerprint()


def test_builtin_kits_are_registered():
    assert resolve_component("agent_population") is AgentPopulation
    assert resolve_component("communication_preset") is CommunicationPreset
    kit = resolve_component("communication_preset")(preset="lan")
    assert kit.comm.delay_s == 0.001
