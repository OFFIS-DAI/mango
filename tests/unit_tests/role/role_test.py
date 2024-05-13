from mango.agent.role import Role, RoleHandler, DataContainer, RoleContext
from mango.util.scheduling import Scheduler
from dataclasses import dataclass


class RoleModel:
    def __init__(self):
        self.model_property_a = 1
        self.model_property_b = 1


class SubRole(Role):
    def __init__(self):
        self.counter = 1

    def on_change_model(self, model):
        self.counter = self.counter + 1
        self.last_model = model


def test_subscription():
    # GIVEN
    role_handler = RoleHandler(None, None)
    ex_role = SubRole()
    ex_role2 = SubRole()
    role_model = role_handler.get_or_create_model(RoleModel)

    # WHEN
    role_handler.subscribe(ex_role, RoleModel)
    role_handler.subscribe(ex_role2, RoleModel)
    role_model.model_property_a = 2
    role_handler.update(role_model)

    # THEN
    assert ex_role.counter == 2
    assert ex_role2.counter == 2
    assert ex_role.last_model == role_model


def test_subscription_deactivated():
    # GIVEN
    role_handler = RoleHandler(None, Scheduler())
    ex_role = SubRole()
    ex_role2 = SubRole()
    role_model = role_handler.get_or_create_model(RoleModel)

    # WHEN
    role_handler.subscribe(ex_role, RoleModel)
    role_handler.subscribe(ex_role2, RoleModel)
    role_model.model_property_a = 2
    role_handler.deactivate(ex_role2)
    role_handler.update(role_model)

    # THEN
    assert ex_role.counter == 2
    assert ex_role2.counter == 1
    assert ex_role.last_model == role_model


def test_no_subscription_update():
    # GIVEN
    role_handler = RoleHandler(None, None)
    ex_role = SubRole()
    role_model = role_handler.get_or_create_model(RoleModel)

    # WHEN
    role_model.model_property_a = 2
    role_handler.update(role_model)

    # THEN
    assert ex_role.counter == 1


def test_append_message_subs():
    # GIVEN
    role_handler = RoleHandler(None, None)
    test_role = SubRole()

    # WHEN
    role_handler.subscribe_message(test_role, str.center, lambda x: True, 0)
    role_handler.subscribe_message(test_role, str.casefold, lambda x: True, 5)
    role_handler.subscribe_message(test_role, str.capitalize, lambda x: True, 2)
    role_handler.subscribe_message(test_role, str.endswith, lambda x: True, 8)
    role_handler.subscribe_message(test_role, str.count, lambda x: True, 0)

    # THEN
    assert role_handler._message_subs[0][2] == str.center
    assert role_handler._message_subs[1][2] == str.count
    assert role_handler._message_subs[2][2] == str.capitalize
    assert role_handler._message_subs[3][2] == str.casefold
    assert role_handler._message_subs[4][2] == str.endswith


def test_data_container():
    # GIVEN
    data_container = DataContainer()
    data_container.abc = "123"
    data_container.cba = "123"

    # WHEN THEN
    assert "abc" in data_container
    assert "cba" in data_container
    assert not "bca" in data_container


@dataclass
class Event:
    name: str


class RoleHandlingEvents(Role):
    def __init__(self):
        self.source = None
        self.event = None

    def handle_event(self, event, source):
        self.event = event
        self.source = source

    def setup(self) -> None:
        self.context.subscribe_event(self, Event, self.handle_event)


def test_emit_event():
    # GIVEN
    role_handler = RoleHandler(None, None)
    context = RoleContext(None, None, role_handler, None, None)
    ex_role = SubRole()
    ex_role2 = RoleHandlingEvents()
    context.add_role(ex_role)
    context.add_role(ex_role2)
    event = Event("Here I am!")

    # WHEN
    context.emit_event(event=event)

    # THEN
    assert ex_role2.event == event
    assert ex_role2.source is None

    # WHEN
    context.emit_event(event=event, event_source=ex_role)

    # THEN
    assert ex_role2.event == event
    assert ex_role2.source == ex_role

def test_data_container():
    # GIVEN
    data_container = DataContainer()
    data_container["abc"] = "123"
    data_container.update({
        "cba": 123
    })

    # WHEN THEN
    assert data_container.cba == 123
    assert data_container.get("abc") == "123"
    assert data_container.get("bca") is None