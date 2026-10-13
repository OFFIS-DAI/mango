"""Errors in user code name the agent, the time and the user's file."""

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from mango import Agent, Role, RoleAgent  # noqa: E402
from mango.scenario import Scenario  # noqa: E402
from mango.scenario._util import user_frame  # noqa: E402

from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

MODEL = """
from mango import Agent
from mango.scenario import Scenario


class Sensor(Agent):
    def __init__(self):
        super().__init__()
        self.readings = []

    def on_step(self, env, clock, step_size_s):
        self.avg = sum(self.readings) / len(self.readings)


class Pinger(Agent):
    def on_step(self, env, clock, step_size_s):
        if self.aid == "p_0":
            self.schedule_instant_message({"ping": 1}, "p_1")


scenario = Scenario("sensors")
scenario.time(horizon=3, step=1)
scenario.group("a", 2, lambda e: Sensor())

pings = Scenario("pings")
pings.time(horizon=3, step=1)
pings.group("p", 2, lambda e: Pinger())
"""


class Divider(Agent):
    def on_step(self, env, clock, step_size_s):
        if clock.time >= 2:
            raise ZeroDivisionError("division by zero")


class Counting(Role):
    def on_step(self, env, clock, step_size_s):
        raise ValueError("role broke")


def test_on_step_errors_name_agent_role_and_time():
    scenario = Scenario("div")
    scenario.time(horizon=5, step=1)
    scenario.group("a", 2, lambda e: Divider())
    with pytest.raises(ZeroDivisionError) as info:
        scenario.run(seed=1)
    assert "while stepping agent 'a_0' in on_step at t=2 s" in info.value.__notes__
    assert user_frame(info.value).endswith("in on_step")
    assert "test_errors_context.py" in user_frame(info.value)

    def with_role(e):
        agent = RoleAgent()
        agent.add_role(Counting())
        return agent

    scenario = Scenario("roles")
    scenario.time(horizon=5, step=1)
    scenario.group("r", 1, with_role)
    with pytest.raises(ValueError, match="role broke") as info:
        scenario.run(seed=1)
    assert info.value.__notes__ == [
        "while stepping agent 'r' (role Counting) in on_step at t=0 s"
    ]


def test_cli_errors_point_at_user_code(capsys, isolated, monkeypatch):
    monkeypatch.syspath_prepend(str(isolated))
    write(isolated, "model.py", MODEL)
    code, _, err = cli(capsys, "validate", "model:scenario", "--build")
    assert code == 1
    assert "error: ZeroDivisionError: division by zero" in err
    assert "(while stepping agent 'a_0' in on_step at t=0 s)" in err
    assert "(at model.py:" in err and "in on_step)" in err
    code, _, err = cli(capsys, "run", "model:scenario", "--out", "res")
    assert code == 1
    assert "failed: ZeroDivisionError: division by zero\n" in err
    assert "  (while stepping agent 'a_0' in on_step at t=0 s; at model.py:" in err


def test_a_wrong_receiver_fails_where_it_is_passed(capsys, isolated, monkeypatch):
    monkeypatch.syspath_prepend(str(isolated))
    write(isolated, "model.py", MODEL)
    code, _, err = cli(capsys, "run", "model:pings", "--out", "res")
    assert code == 1
    assert "receiver_addr must be an AgentAddress, got str 'p_1'" in err
    assert "sender_addr(meta)" in err
    assert "at model.py:" in err


@pytest.mark.parametrize(
    "option, fn",
    [
        ("factory", lambda: Agent()),
        ("install", lambda: {"bus": 0}),
        ("position", lambda a, b: None),
    ],
)
def test_group_functions_must_take_e(option, fn):
    scenario = Scenario("x")
    kwargs = {option: fn}
    factory = kwargs.pop("factory", lambda e: Agent())
    with pytest.raises(TypeError, match=f"the {option} must take one argument e"):
        scenario.group("a", 1, factory, **kwargs)


def test_flexible_signatures_pass_the_arity_check():
    scenario = Scenario("x")
    scenario.group("a", 1, lambda *args: Agent())
    scenario.group("b", 1, lambda e, extra=1: Agent())

    class Factory:
        def __call__(self, e):
            return Agent()

    scenario.group("c", 1, Factory())
    assert len(scenario.run(seed=1).world.agents) == 3


# ----------------------------------------------------------------- kit scope (N28)


def _chain_kit(factory):
    from pydantic import BaseModel

    from mango.scenario import ScenarioComponent

    class Stage(Agent):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg

        def handle_message(self, content, meta):
            pass

    class Chain(ScenarioComponent):
        name = "chain"

        class Params(BaseModel):
            holding: float = 0.5

        def build(self, ctx):
            ctx.group("stage", 2, lambda e: Stage(factory(e)))

    return Chain()


def test_a_kit_factory_reading_its_prefix_gets_the_scope_hint():
    scenario = Scenario.compose(_chain_kit(lambda e: e.p.chain.holding))
    with pytest.raises(AttributeError) as info:
        scenario.run(seed=1, until=1)
    notes = info.value.__notes__
    assert "while building entity 'chain.stage_0' of group 'chain.stage'" in notes[0]
    assert notes[1] == (
        "inside kit 'chain', e.p already is params.chain (the kit's parameters): "
        "write e.p.holding, not e.p.chain.holding"
    )
    # the pointer names the user's factory, not pydantic's __getattr__
    assert "test_errors_context.py" in user_frame(info.value)


def test_a_kit_factory_reading_a_scenario_field_gets_the_params_hint():
    scenario = Scenario.compose(_chain_kit(lambda e: e.p.time))
    with pytest.raises(AttributeError) as info:
        scenario.run(seed=1, until=1)
    assert info.value.__notes__[-1] == (
        "inside kit 'chain', e.p is params.chain (the kit's parameters); read the "
        "scenario-level field with e.params.time"
    )


def test_other_attribute_errors_get_no_kit_hint():
    scenario = Scenario.compose(_chain_kit(lambda e: e.p.nonexistent))
    with pytest.raises(AttributeError) as info:
        scenario.run(seed=1, until=1)
    assert not any("inside kit" in note for note in info.value.__notes__)


class BadReply(Agent):
    def on_step(self, env, clock, step_size_s):
        if self.aid == "b_0" and clock.time == 0:
            self.schedule_instant_message(
                {"ping": 1}, self.ctx.entity("b_1").agent.addr
            )

    def handle_message(self, content, meta):
        self.schedule_instant_message({"pong": 1}, "b_0")  # a str, not an address


def test_a_failed_handler_message_has_no_doubled_period():
    scenario = Scenario("bad_reply")
    scenario.time(horizon=3, step=1)

    def make(e):
        agent = BadReply()
        agent.ctx = e.ctx
        return agent

    scenario.group("b", 2, make)
    with pytest.raises(RuntimeError) as info:
        scenario.run(seed=1)
    text = str(info.value)
    assert "reply with sender_addr(meta)." in text and ".." not in text
