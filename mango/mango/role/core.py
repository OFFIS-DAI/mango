"""
...
"""

from typing import Any, Dict, Optional, Union, Tuple

from ..core.agent import Agent
from .role import Role

class RoleHandler:

    def __init__(self):
        self._role_models = {}
        self._roles = []
        self._role_model_type_to_subs = {}

    def get_or_create_model(self, cls):
        if cls in self._role_models:
            return self._role_models[cls]
        
        print(type(RoleHandler())())
        self._role_models[cls] = cls()
        return self._role_models[cls]

    def update(self, role_model, context):
        role_model_type = type(role_model)
        self._role_models[role_model_type] = role_model

        # Notify all subscribing agents
        if role_model_type in self._role_model_type_to_subs:
            for role in self._role_model_type_to_subs[role_model_type]:
                role.on_change_model(role_model, context)

    def subscribe(self, role, role_model_type):
        if role_model_type in self._role_model_type_to_subs:
            self._role_model_type_to_subs[role_model_type].append(role)
        else: 
            self._role_model_type_to_subs[role_model_type] = [role]

    def add_role(self, role):
        self._roles.append(role)

    @property
    def roles(self):
        return self._roles

    async def _on_stop(self):
        for role in self._roles:
            await role.on_stop()


class RoleAgentContext:

    def __init__(self, container, role_handler: RoleHandler, aid, scheduler):
        self._role_handler = role_handler
        self._container = container
        self._aid = aid
        self._scheduler = scheduler

    def get_or_create_model(self, cls):
        return self._role_handler.get_or_create_model(cls)

    def update(self, role_model):
        self._role_handler.update(role_model, self)

    def subscribe(self, role, role_model_type):
        self._role_handler.subscribe(role, role_model_type)

    def _add_role(self, role):
        self._role_handler.add_role(role)

    def handle_msg(self, content, meta: Dict[str, Any]):
        for role in self._role_handler.roles:
            if role.is_applicable(content, meta):
                role.handle_msg(content, meta, self)

    def schedule_task(self, task):
        self._scheduler.schedule_task(task)

    async def send_message(
            self, content,
            receiver_addr: Union[str, Tuple[str, int]], *,
            receiver_id: Optional[str] = None,
            create_acl: bool = False,
            acl_metadata: Optional[Dict[str, Any]] = None,
            mqtt_kwargs: Optional[Dict[str, Any]] = None,
    ):
        """Send a message to another agent. Delegates the call to the agent-container.

        Args:
            content (arbitrary class): the message you want to send
            receiver_addr (Union[str, Tuple[str, int]]): address of the recipient
            receiver_id (Optional[str], optional): ip of the recipient. Defaults to None.
            create_acl (bool, optional): set true if you want to create an acl. Defaults to False.
            acl_metadata (Optional[Dict[str, Any]], optional): Metadata of the acl. Defaults to None.
            mqtt_kwargs (Dict[str, Any], optional): Args for mqtt. Defaults to None.
        """
        return await self._container.send_message(
            content=content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            create_acl=create_acl,
            acl_metadata=acl_metadata,
            mqtt_kwargs=mqtt_kwargs)

    def get_addr(self):
        return self._container.addr

    def get_aid(self):
        return self._aid


class RoleAgent(Agent):

    def __init__(self, container):
        super(RoleAgent, self).__init__(container)

        self._role_handler = RoleHandler()
        self._agent_context = RoleAgentContext(container, self._role_handler, self.aid, self._scheduler)
        
    def add_role(self, role: Role):
        self._agent_context._add_role(role)
        
        # Setup role
        role.setup(self._agent_context)

    @property
    def roles(self):
        return self._role_handler.roles()

    def handle_msg(self, content, meta: Dict[str, Any]):
        self._agent_context.handle_msg(content, meta)

    async def shutdown(self):
        await self._role_handler._on_stop()
        await super(RoleAgent, self).shutdown()
