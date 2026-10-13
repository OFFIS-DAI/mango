"""Communication of a scenario: comm.kind switches (C19), custom factories with
ctx and rebuild semantics, per-link delay/loss, partitions and dynamic links
(C08), and factory provenance (C20)."""

import json
import textwrap

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    CommConfig,
    LayeredCommunication,
    LinkComm,
    LogNormal,
    Scenario,
    ScenarioError,
    ScenarioParams,
    Uniform,
    build_communication,
    deep_merge,
    load_params,
    load_run,
    load_scenario,
)
from mango.scenario.context import RunState  # noqa: E402
from mango.simulation import (  # noqa: E402
    CommunicationSimulation,
    CommunicationSimulationResult,
    PackageResult,
    SimpleCommunicationSimulation,
)
from mango.util.seeding import SeedTree  # noqa: E402

H = 3600.0
LOGNORMAL_BASE = {
    "comm": {"kind": "simple", "loss_percent": 0.2, "delay_s": {"lognormal": [-3, 0.5]}}
}


# --------------------------------------------------------------------------- factories


class CountingSim(CommunicationSimulation):
    """Delivers everything after *delay_s* and counts the messages it saw."""

    instances: list = []

    def __init__(self, delay_s: float = 0.0, options: dict | None = None, ctx=None):
        self.delay_s = delay_s
        self.options = dict(options or {})
        self.ctx = ctx
        self.count = 0
        CountingSim.instances.append(self)

    def calculate_communication(self, current_time, messages):
        self.count += len(messages)
        return CommunicationSimulationResult(
            [PackageResult(True, self.delay_s) for _ in messages]
        )


def make_counting(cfg, rng, ctx):
    return CountingSim(float(cfg.delay_s), cfg.options, ctx)


def make_counting_without_ctx(cfg, rng):
    return CountingSim(float(cfg.delay_s), cfg.options)


class TellableSim(CountingSim):
    """Keeps its state across comm boundaries: the run tells it the new settings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.changes: list = []

    def on_comm_change(self, comm):
        self.changes.append(dict(comm.options))
        self.options = dict(comm.options)


def make_tellable(cfg, rng, ctx):
    return TellableSim(0.0, cfg.options, ctx)


class Talker(Agent):
    """Sends one message to each neighbour of *tid* in every step and logs arrivals."""

    def __init__(self, tid="net"):
        super().__init__()
        self.tid = tid
        self.arrivals: list[tuple[float, str, float]] = []

    def handle_message(self, content, meta):
        self.arrivals.append((self.scheduler.clock.time, meta["sender_id"], content))

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid=self.tid):
            self.schedule_instant_message(clock.time, neighbor)


def talkers(n=3, *, pattern="complete", horizon="4h", **link):
    scenario = Scenario("talk")
    scenario.time(horizon=horizon, step="1h")
    nodes = scenario.group("node", n, lambda e: Talker())
    scenario.link("net", nodes, pattern=pattern, **link)
    scenario.record("received", nodes, lambda a: len(a.arrivals))
    return scenario


# --------------------------------------------------------------------------- C19


def test_comm_kind_switch_keeps_loss_and_delay_in_overrides():
    params = load_params(
        ScenarioParams,
        LOGNORMAL_BASE,
        overrides=["comm.kind=custom", "comm.factory=m:f"],
    )
    assert params.comm.kind == "custom" and params.comm.factory == "m:f"
    assert params.comm.loss_percent == 0.2
    assert params.comm.delay_s == LogNormal(-3.0, 0.5)
    # and back: custom -> simple keeps loss and options
    back = load_params(
        ScenarioParams,
        {
            "comm": {
                "kind": "custom",
                "factory": "m:f",
                "loss_percent": 0.3,
                "options": {"mbps": 10},
            }
        },
        overrides=["comm.kind=simple"],
    )
    assert back.comm.loss_percent == 0.3 and back.comm.options == {"mbps": 10}


def test_comm_kind_switch_keeps_settings_through_preset_extends(tmp_path):
    (tmp_path / "base.yaml").write_text(
        "comm: {kind: simple, loss_percent: 0.2, delay_s: {lognormal: [-3, 0.5]}}\n"
    )
    (tmp_path / "cut.yaml").write_text(
        "extends: base.yaml\ncomm: {kind: custom, factory: 'm:f'}\n"
    )
    params = load_params(ScenarioParams, str(tmp_path / "cut.yaml"))
    assert params.comm.kind == "custom"
    assert params.comm.loss_percent == 0.2
    assert params.comm.delay_s == LogNormal(-3.0, 0.5)


def test_comm_kind_switch_keeps_settings_through_scenario_file_extends(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            name: base
            comm: {kind: simple, loss_percent: 0.1, delay_s: 0.05}
            """
        )
    )
    (tmp_path / "cut.yaml").write_text(
        "extends: scenario.yaml\ncomm: {kind: custom, factory: 'm:f'}\n"
    )
    comm = load_scenario("cut.yaml").resolve_params().comm
    assert (comm.kind, comm.loss_percent, comm.delay_s) == ("custom", 0.1, 0.05)


def test_event_comm_kind_switch_keeps_base_settings():
    from mango.scenario.events import _effective_comm

    base = CommConfig(loss_percent=0.2, delay_s=0.5).model_dump(mode="json")
    config = _effective_comm(base, {"kind": "custom", "factory": "m:f"})
    assert (config.loss_percent, config.delay_s) == (0.2, 0.5)


def test_real_discriminated_unions_still_switch():
    from typing import Literal

    from pydantic import BaseModel

    class Greedy(BaseModel, extra="forbid"):
        kind: Literal["greedy"] = "greedy"
        threshold: float = 1.0

    class Market(BaseModel, extra="forbid"):
        kind: Literal["market"] = "market"
        rounds: int = 3

    class Params(ScenarioParams):
        algorithm: Greedy | Market = Greedy()
        arrival: Uniform = Uniform(1.0, 2.0)

    params = load_params(
        Params,
        {"algorithm": {"kind": "greedy", "threshold": 5.0}},
        overrides=["algorithm.kind=market"],
    )
    assert params.algorithm == Market()
    # without a model, deep_merge keeps the generic rule (comm merges through)
    assert deep_merge({"a": {"kind": "x", "v": 1}}, {"a": {"kind": "y"}}) == {
        "a": {"kind": "y"}
    }
    assert deep_merge(
        {"comm": {"kind": "simple", "delay_s": 1}}, {"comm": {"kind": "custom"}}
    ) == {"comm": {"kind": "custom", "delay_s": 1}}
    assert deep_merge(
        {"components": {"comm": {"kind": "a", "x": 1}}},
        {"components": {"comm": {"kind": "b"}}},
    ) == {"components": {"comm": {"kind": "b"}}}


# --------------------------------------------------------------------------- C08


def test_custom_params_factory_gets_ctx_and_keeps_two_argument_factories():
    CountingSim.instances.clear()
    scenario = talkers()
    result = scenario.run(
        {"comm": {"kind": "custom", "factory": f"{__name__}:make_counting"}}, seed=1
    )
    sim = CountingSim.instances[-1]
    assert sim.ctx is not None and [a.aid for a in sim.ctx.agents("node")] == [
        "node_0",
        "node_1",
        "node_2",
    ]
    assert result.world.communication_sim is sim
    old_style = build_communication(
        CommConfig(kind="custom", factory=f"{__name__}:make_counting_without_ctx"),
        SeedTree(1).rng("comm"),
    )
    assert isinstance(old_style, CountingSim) and old_style.ctx is None


def test_factory_instances_are_reused_when_settings_return():
    CountingSim.instances.clear()
    scenario = talkers(horizon="6h")
    result = scenario.run(
        {
            "comm": {"kind": "custom", "factory": f"{__name__}:make_counting"},
            "events": [{"at": "2h", "until": "4h", "comm": {"options": {"jam": 1}}}],
        },
        seed=1,
    )
    base, jammed = CountingSim.instances
    assert jammed.options == {"jam": 1} and base.options == {}
    assert result.world.communication_sim is base  # the base instance came back
    # 6 messages per step: base saw steps 0,1 and 4,5, the jammed one 2,3
    assert (base.count, jammed.count) == (24, 12)


def test_factory_with_on_comm_change_is_kept_and_told():
    CountingSim.instances.clear()
    scenario = talkers(horizon="6h")
    result = scenario.run(
        {
            "comm": {"kind": "custom", "factory": f"{__name__}:make_tellable"},
            "events": [{"at": "2h", "until": "4h", "comm": {"options": {"jam": 1}}}],
        },
        seed=1,
    )
    (sim,) = CountingSim.instances
    assert sim.changes == [{"jam": 1}, {}]
    assert sim.count == 36 and result.world.communication_sim is sim


def test_link_delay_and_loss_apply_to_the_link_only():
    scenario = Scenario("links")
    scenario.time(horizon="6h", step="1h")
    a = scenario.group("a", 1, lambda e: Talker("slow"))
    b = scenario.group("b", 1, lambda e: Talker("fast"))
    c = scenario.group("c", 1, lambda e: Talker("none"))
    scenario.link("slow", a, b, pattern="one_to_one", directed=True, delay="2h")
    scenario.link("fast", b, c, pattern="one_to_one", directed=True)
    result = scenario.run(seed=1)
    b_arrivals = result.agent("b").arrivals
    c_arrivals = result.agent("c").arrivals
    # sends from on_step at t are stamped t+1h; +2h link delay: handled at t+3h
    assert b_arrivals[0][:2] == (3 * H, "a") and b_arrivals[0][2] == 0.0
    assert c_arrivals[0][:2] == (1 * H, "b")
    plan = scenario.plan()
    assert plan.link("slow").comm == {"delay": 7200.0, "loss": 0.0}
    assert "delay 7200.0" in str(plan)


def test_link_loss_draws_from_edge_streams_and_replays():
    scenario = talkers(n=4, horizon="20h", loss=0.5)
    first = scenario.run(seed=3)
    again = scenario.run(seed=3)
    assert first.checksum() == again.checksum()
    received = sum(len(a.arrivals) for a in first.world.agents.values())
    sent = 12 * 20  # 4 x 3 neighbours per step
    assert 0.3 * sent < received < 0.7 * sent
    assert isinstance(first.world.communication_sim, LayeredCommunication)


def test_link_delay_distributions_accept_durations():
    assert LinkComm(delay={"uniform": ["1h", "2h"]}).delay == Uniform(3600.0, 7200.0)
    assert LinkComm(delay="30min").delay == 1800.0 and not LinkComm().active
    with pytest.raises(ScenarioError, match="link\\('x'\\): .*less than or equal"):
        talkers().link("x", "node", pattern="ring", loss=2)
    scenario = talkers(delay=1.0)
    scenario.link("net", "node", pattern="ring", delay=2.0)
    with pytest.raises(ScenarioError, match="different delay/loss"):
        scenario.plan()


def test_partition_blocks_messages_between_groups_during_an_event():
    scenario = Scenario("split")
    scenario.time(horizon="6h", step="1h")
    left = scenario.group("left", 2, lambda e: Talker())
    right = scenario.group("right", 2, lambda e: Talker())
    scenario.link("net", left, right, pattern="all_to_all")
    scenario.link("net", left, pattern="complete")
    result = scenario.run(
        {
            "events": [
                {
                    "at": "2h",
                    "until": "4h",
                    "comm": {"partition": [["left"], ["right"]]},
                }
            ]
        },
        seed=1,
    )
    left0 = result.agent("left_0").arrivals
    from_right = sorted({t for t, sender, _ in left0 if sender.startswith("right")})
    from_left = sorted({t for t, sender, _ in left0 if sender.startswith("left")})
    # sends are stamped when the clock reached the step end: from on_step at 1h
    # and 2h they are stamped 2h and 3h, inside the partition [2h, 4h)
    assert from_right == [1 * H, 4 * H, 5 * H, 6 * H]
    assert from_left == [h * H for h in range(1, 7)]


def test_partition_by_attribute_and_validation():
    scenario = Scenario("split")
    scenario.time(horizon="3h", step="1h")
    nodes = scenario.group(
        "node", 4, lambda e: Talker(), install=lambda e: {"side": e.index < 2}
    )
    scenario.link("net", nodes, pattern="complete")
    result = scenario.run({"comm": {"partition": {"by_attr": "side"}}}, seed=1)
    senders = {sender for _, sender, _ in result.agent("node_0").arrivals}
    assert senders == {"node_1"}
    with pytest.raises(ScenarioError, match="'lft' is neither a group nor an entity"):
        scenario.plan({"comm": {"partition": [["lft"], ["node"]]}})
    with pytest.raises(Exception, match="at least two blocks"):
        CommConfig(partition=[["a"]])
    with pytest.raises(Exception, match="by_attr"):
        CommConfig(partition={"side": "x"})


def test_partition_is_left_out_of_dumps_while_unset():
    assert "partition" not in CommConfig().model_dump()
    assert "partition" not in repr(CommConfig())
    assert CommConfig(partition=[["a"], ["b"]]).model_dump()["partition"] == [
        ["a"],
        ["b"],
    ]


class Mover(Agent):
    def handle_message(self, content, meta):
        pass


def test_relink_replaces_a_links_neighbourhood_during_the_run():
    scenario = Scenario("dynamic")
    scenario.time(horizon="3h", step="1h")
    nodes = scenario.group("node", 4, lambda e: Mover())
    scenario.link("net", nodes, pattern="ring", delay="1h")
    seen = []

    @scenario.event("1h")
    def to_a_line(ctx):
        names = [e.name for e in ctx.entities("node")]
        assert ctx.relink("net", zip(names, names[1:])) == 3
        seen.append(ctx.link_pairs("net"))

    scenario.record("degree", nodes, lambda a: len(a.neighbors(tid="net")))
    result = scenario.run(seed=1)
    assert seen == [[("node_0", "node_1"), ("node_1", "node_2"), ("node_2", "node_3")]]
    assert result.wide("degree").loc[0.0].tolist() == [2, 2, 2, 2]
    assert result.wide("degree").loc[2 * H].tolist() == [1, 2, 2, 1]
    assert result.stats["link_edges"] == {"net": 3}
    rules = result.world.communication_sim.edges
    assert ("node_0", "node_3") not in rules and ("node_1", "node_0") in rules


@pytest.mark.asyncio
async def test_relink_errors():
    scenario = Scenario("dynamic")
    scenario.time(horizon="2h", step="1h")
    nodes = scenario.group("node", 2, lambda e: Mover())
    scenario.link("net", nodes, pattern="complete")
    run = scenario.build(seed=1)
    declaring = RunState(scenario, run.params, run.seeds).root  # no run, no world
    with pytest.raises(ScenarioError, match="running run"):
        declaring.relink("net", [])
    with pytest.raises(ScenarioError, match="running run"):
        declaring.link_pairs("net")
    async with run:
        with pytest.raises(
            ScenarioError, match="no link of that name.*Did you mean 'net'"
        ):
            run.ctx.relink("nte", [])
        with pytest.raises(ScenarioError, match="'node_9' is not an entity"):
            run.ctx.relink("net", [("node_0", "node_9")])
        with pytest.raises(ScenarioError, match="pairs must be"):
            run.ctx.relink("net", ["node_0"])
        assert run.ctx.relink("net", [(run.ctx.agents("node")[0], "node_1")]) == 1


# --------------------------------------------------------------------------- C20


def radio(ctx):
    return SimpleCommunicationSimulation(loss_percent=0.0)


def test_plan_and_manifest_name_the_factories(tmp_path):
    scenario = talkers()
    scenario.communication(radio)
    plan = scenario.plan({"comm": {"loss_percent": 0.1}})
    assert plan.communication.startswith("factory radio (overrides comm.kind")
    assert "communication: factory radio" in str(plan)
    assert plan.to_dict()["environment"] == "DefaultEnvironment"
    result = scenario.run({"comm": {"loss_percent": 0.1}}, seed=1)
    assert result.factories == {
        "communication": f"{__name__}:radio",
        "environment": None,
    }
    folder = result.save(tmp_path / "run", format="csv")
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["factories"]["communication"] == f"{__name__}:radio"
    config_text = (folder / "config.resolved.yaml").read_text()
    assert config_text.startswith("# comm.kind/comm.factory are not used")
    assert load_run(folder).factories == manifest["factories"]
    assert load_run(folder).config["comm"]["loss_percent"] == 0.1


def test_a_params_level_factory_gets_no_not_used_comment(tmp_path):
    scenario = talkers()
    comm = {"kind": "custom", "factory": f"{__name__}:radio"}
    result = scenario.run({"comm": comm}, seed=1)
    assert result.factories["communication"] == f"{__name__}:radio (comm.factory)"
    folder = result.save(tmp_path / "run", format="csv")
    config_text = (folder / "config.resolved.yaml").read_text()
    assert not config_text.startswith("#")  # comm.factory is what ran
    assert "factory: " in config_text


def test_plan_describes_params_communication():
    scenario = talkers()
    assert scenario.plan().communication == "simple, loss 0, delay 0s"
    assert "communication:" not in str(scenario.plan())  # the default is not shown
    text = str(scenario.plan({"comm": {"loss_percent": 0.1, "delay_s": "2s"}}))
    assert "communication: simple, loss 0.1, delay 2s" in text
    custom = scenario.plan({"comm": {"kind": "custom", "factory": "m:f"}})
    assert custom.communication == "custom factory m:f (comm.kind)"
