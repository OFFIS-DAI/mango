from mango.agent.role import Role, RoleAgent
from mango.express.api import agent_composed_of


class AddrAccessingRole(Role):
    """Role that reads self.context.addr during setup — the crash site."""

    def __init__(self):
        super().__init__()
        self.setup_called = False
        self.addr_at_setup = "NOT_SET"

    def setup(self) -> None:
        self.setup_called = True
        self.addr_at_setup = self.context.addr


def test_add_role_before_registration_calls_setup():
    # GIVEN an agent that has not been registered with any container yet
    agent = RoleAgent()
    role = AddrAccessingRole()

    # WHEN adding a role whose setup() accesses context.addr
    # THEN setup() is called immediately, no AttributeError is raised, and addr is None
    agent.add_role(role)

    assert role.setup_called
    assert role.addr_at_setup is None


class CounterModel:
    def __init__(self):
        self.count = 0


class IncrementRole(Role):
    def setup(self):
        model = self.context.get_or_create_model(CounterModel)
        model.count += 1
        self.context.update(model)


class DisplayRole(Role):
    def setup(self):
        self.context.subscribe_model(self, CounterModel)
        self.last_count = 0

    def on_change_model(self, model):
        self.last_count = model.count


def test_agent_composed_of_initializes_and_updates_synchronously():
    # GIVEN two roles that coordinate via shared models during setup()
    display = DisplayRole()
    increment = IncrementRole()

    # WHEN creating a composed agent (which calls add_role() and thus setup() on roles)
    agent = agent_composed_of(display, increment)

    # THEN setup() ran immediately and display role got notified of increment during setup()
    assert display.last_count == 1
