
from mango.role.core import RoleHandler
from mango.role.role import Role

class RoleModel:
    def __init__(self):
        self.model_property_a = 1
        self.model_property_b = 1

class SubRole(Role):
    def __init__(self):
        self.counter = 1

    def on_change_model(self, model, agent_context):
        self.counter = self.counter + 1
        self.last_model = model
        self.last_context = agent_context

def test_subscription():
    # GIVEN
    role_handler = RoleHandler()
    ex_role = SubRole()
    ex_role2 = SubRole()
    role_model = role_handler.get_or_create_model(RoleModel)

    # WHEN
    role_handler.subscribe(ex_role, RoleModel)
    role_handler.subscribe(ex_role2, RoleModel)
    role_model.model_property_a = 2
    role_handler.update(role_model, None)

    # THEN
    assert ex_role.counter == 2
    assert ex_role2.counter == 2
    assert ex_role.last_model == role_model
    assert ex_role.last_context == None
    
def test_no_subscription_update():
    # GIVEN
    role_handler = RoleHandler()
    ex_role = SubRole()
    role_model = role_handler.get_or_create_model(RoleModel)

    # WHEN
    role_model.model_property_a = 2
    role_handler.update(role_model, None)

    # THEN
    assert ex_role.counter == 1
    