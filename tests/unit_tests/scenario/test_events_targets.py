"""emit_to: the delivery hook is explicit, warnings for unhandled events, selectors."""

import warnings

import pytest

pytest.importorskip("pydantic")

from mango import Agent, Role, RoleAgent  # noqa: E402
from mango.scenario import RunWarning, Scenario, ScenarioError  # noqa: E402

TIME = {"time": {"horizon": "4h", "step": "1h"}}


class GlobalOnly(Agent):
    def __init__(self):
        super().__init__()
        self.heard = 0

    def on_global_event(self, event):
        self.heard += 1


class Targeted(Agent):
    def __init__(self):
        super().__init__()
        self.heard = []

    def on_agent_event(self, event):
        self.heard.append(event.time)


class Listening(Role):
    def __init__(self):
        super().__init__()
        self.heard = 0

    def on_agent_event(self, event):
        self.heard += 1


def people(factory, n=4):
    scenario = Scenario("social")
    scenario.group(
        "person",
        n,
        factory,
        install=lambda e: {"age_group": "young" if e.index % 2 else "old"},
    )
    return scenario


def run(scenario, events, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = scenario.run({**TIME, "events": events}, seed=1, **kwargs)
    return result, [str(item.message) for item in caught]


def campaign(**extra):
    return {"name": "campaign", "at": "1h", "emit": {"kind": "campaign"}, **extra}


def test_emit_to_an_agent_without_on_agent_event_warns_once():
    result, caught = run(people(lambda e: GlobalOnly()), [campaign(emit_to="person")])
    assert all(agent.heard == 0 for agent in result.world.agents.values())
    (warning,) = result.warnings
    assert warning == (
        "event 'campaign' (to emit_to='person') is delivered through on_agent_event, "
        "but none of its 4 receivers implements it, so it is ignored; GlobalOnly "
        "implements only on_global_event (drop emit_to to broadcast, or implement "
        "on_agent_event)."
    )
    assert caught.count(warning) == 1


def test_one_receiver_is_named_and_repetitions_warn_once():
    events = [campaign(emit_to="person_1", every="1h")]
    result, _ = run(people(lambda e: GlobalOnly()), events)
    (warning,) = result.warnings
    assert "its receiver 'person_1' does not implement it" in warning


def test_a_broadcast_nobody_handles_warns():
    class Deaf(Agent):
        pass

    result, _ = run(people(lambda e: Deaf()), [campaign()])
    (warning,) = result.warnings
    assert "delivered through on_global_event" in warning
    assert "implement on_global_event(self, event) in Deaf" in warning


def test_handled_events_and_role_handlers_do_not_warn():
    result, caught = run(people(lambda e: Targeted()), [campaign(emit_to="person")])
    assert result.warnings == [] and not caught

    def with_role(e):
        agent = RoleAgent()
        agent.add_role(Listening())
        return agent

    result, caught = run(people(with_role), [campaign(emit_to="person_0")])
    assert result.warnings == []
    assert result.world.agents["person_0"].roles[0].heard == 1


def test_emit_to_selects_entities_by_install_attribute():
    events = [campaign(emit_to={"group": "person", "by_attr": {"age_group": "young"}})]
    result, _ = run(people(lambda e: Targeted()), events)
    heard = {aid: agent.heard for aid, agent in result.world.agents.items()}
    assert heard == {
        "person_0": [],
        "person_1": [3600.0],
        "person_2": [],
        "person_3": [3600.0],
    }
    events = [campaign(emit_to={"by_attr": {"age_group": ["young", "old"]}})]
    result, _ = run(people(lambda e: Targeted()), events)
    assert all(agent.heard == [3600.0] for agent in result.world.agents.values())


def test_a_selector_that_matches_nobody_warns():
    events = [campaign(emit_to={"by_attr": {"age_group": "teen"}})]
    result, _ = run(people(lambda e: Targeted()), events)
    (warning,) = result.warnings
    assert "selects no entity at t=3600 s, so the event reaches nobody" in warning


def test_selectors_are_checked_when_declared_and_planned():
    scenario = people(lambda e: Targeted())
    with pytest.raises(ScenarioError, match="needs by_attr"):
        scenario.plan({**TIME, "events": [campaign(emit_to={"group": "person"})]})
    with pytest.raises(ScenarioError, match="selector key 'by_atr' is unknown"):
        scenario.plan({**TIME, "events": [campaign(emit_to={"by_atr": {"a": 1}})]})
    with pytest.raises(ScenarioError, match="emit_to group 'persons' is not a group"):
        scenario.plan(
            {
                **TIME,
                "events": [campaign(emit_to={"group": "persons", "by_attr": {"a": 1}})],
            }
        )
    scenario.event("2h", emit={"kind": "x"}, emit_to={"by_attr": {"age_group": "old"}})
    plan = scenario.plan(TIME)
    assert "to entities with age_group=old (on_agent_event)" in str(plan)


def test_plan_names_the_delivery_hook():
    scenario = people(lambda e: Targeted())
    plan = scenario.plan(
        {
            **TIME,
            "events": [
                campaign(),
                {"at": "2h", "emit": {"k": 1}, "emit_to": ["person_0", "person_1"]},
            ],
        }
    )
    text = str(plan)
    assert "to all agents (on_global_event)" in text
    assert "to person_0, person_1 (on_agent_event)" in text


class Breeder(Targeted):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    def on_step(self, env, clock, step_size_s):
        if self.aid == "person_0" and clock.time == 0:
            self.ctx.spawn("person")


def test_group_targets_reach_spawned_members():
    scenario = Scenario("births")
    scenario.group("person", 1, lambda e: Breeder(e.ctx), naming="{group}_{index}")
    result, _ = run(scenario, [campaign(emit_to="person")])
    assert {aid: agent.heard for aid, agent in result.world.agents.items()} == {
        "person_0": [3600.0],
        "person_1": [3600.0],
    }


def test_run_warnings_are_run_warning_instances():
    with pytest.warns(RunWarning, match="delivered through on_agent_event"):
        people(lambda e: GlobalOnly()).run(
            {**TIME, "events": [campaign(emit_to="person")]}, seed=1
        )
