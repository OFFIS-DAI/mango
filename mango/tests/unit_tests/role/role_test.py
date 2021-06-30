
from mango.role.core import RoleHandler
from mango.role.api import Role

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
    role_handler = RoleHandler()
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
    
def test_no_subscription_update():
    # GIVEN
    role_handler = RoleHandler()
    ex_role = SubRole()
    role_model = role_handler.get_or_create_model(RoleModel)

    # WHEN
    role_model.model_property_a = 2
    role_handler.update(role_model)

    # THEN
    assert ex_role.counter == 1
    