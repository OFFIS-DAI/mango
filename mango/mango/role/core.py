"""
Internal module, which implements the framework API of the role package. Provides an
implementation of the :class:`RoleContext`, the RoleAgent and some internal handlers
for the communication between roles.
"""

from typing import Any, Dict, Optional, Union, Tuple, List

from mango.util.scheduling import ScheduledTask, Scheduler
from mango.core.agent import Agent
from mango.role.api import Role, RoleContext


class RoleHandler:
    """Contains all roles and their models. Implements the communication between roles.
    """

    def __init__(self):
        self._role_models = {}
        self._roles = []
        self._role_model_type_to_subs = {}

    def get_or_create_model(self, cls):
        """Creates or return (when already created) a central role model.

        Returns:
            [type]: the model
        """
        if cls in self._role_models:
            return self._role_models[cls]

        self._role_models[cls] = cls()
        return self._role_models[cls]

    def update(self, role_model) -> None:
        """Notifies all subscribers of an update of the given role_model.

        Args:
            role_model ([type]): the role model to notify about
        """
        role_model_type = type(role_model)
        self._role_models[role_model_type] = role_model

        # Notify all subscribing agents
        if role_model_type in self._role_model_type_to_subs:
            for role in self._role_model_type_to_subs[role_model_type]:
                role.on_change_model(role_model)

    def subscribe(self, role: Role, role_model_type) -> None:
        """Subscibe a role to change events of a specific role model type

        Args:
            role ([type]): the role
            role_model_type ([type]): the type of the role model
        """
        if role_model_type in self._role_model_type_to_subs:
            self._role_model_type_to_subs[role_model_type].append(role)
        else:
            self._role_model_type_to_subs[role_model_type] = [role]

    def add_role(self, role: Role) -> None:
        """Add a new role

        Args:
            role ([type]): the role
        """
        self._roles.append(role)

    @property
    def roles(self) -> List[Role]:
        """Returns all roles

        Returns:
            List[Role]: the roles hold by this handler
        """
        return self._roles

    async def on_stop(self):
        """Notifiy all roles when the container is shut down
        """
        for role in self._roles:
            await role.on_stop()


class RoleAgentContext(RoleContext):
    """Implementation of the RoleContext-API.
    """

    def __init__(self, container, role_handler: RoleHandler, aid: str, inbox, scheduler: Scheduler):
        self._role_handler = role_handler
        self._container = container
        self._aid = aid
        self._scheduler = scheduler
        self._message_subs = {}
        self._send_msg_subs = {}
        self._inbox = inbox

    def inbox_length(self):
        return len(self._inbox)

    def get_or_create_model(self, cls):
        return self._role_handler.get_or_create_model(cls)

    def update(self, role_model):
        self._role_handler.update(role_model)

    def subscribe_model(self, role, role_model_type):
        self._role_handler.subscribe(role, role_model_type)

    def subscribe_message(self, role, method, message_condition):
        if role in self._message_subs:
            self._message_subs[role].append((message_condition, method))
        else:
            self._message_subs[role] = [(message_condition, method)]

    def subscribe_send(self, role, method):
        if role in self._send_msg_subs:
            self._send_msg_subs[role].append(method)
        else:
            self._send_msg_subs[role] = [method]

    def add_role(self, role: Role):
        """Add a role to the context.

        Args:
            role ([Role]): the Role
        """
        self._role_handler.add_role(role)

    def handle_msg(self, content, meta: Dict[str, Any]):
        """Handle an incoming message, delegating it to all applicable subscribers

        Args:
            content ([type]): content
            meta (Dict[str, Any]): meta
        """
        for role in self._role_handler.roles:
            if role in self._message_subs:
                for (condition, method) in self._message_subs[role]:
                    if condition(content, meta):
                        method(content, meta)

    def schedule_task(self, task: ScheduledTask):
        self._scheduler.schedule_task(task)

    async def send_message(self, content,
                           receiver_addr: Union[str, Tuple[str, int]], *,
                           receiver_id: Optional[str] = None,
                           create_acl: bool = False,
                           acl_metadata: Optional[Dict[str, Any]] = None,
                           mqtt_kwargs: Dict[str, Any] = None,
                           ):
        """Send a message to another agent. Delegates the call to the agent-container.

        Args:
            content (arbitrary class): the message you want to send
            receiver_addr (Union[str, Tuple[str, int]]): address of the recipient
            receiver_id (Optional[str], optional): ip of the recipient. Defaults to None.
            create_acl (bool, optional): set true if you want to create an acl. Defaults to False.
            acl_metadata (Optional[Dict[str, Any]], optional): Metadata of the acl. Defaults to None
            mqtt_kwargs (Dict[str, Any], optional): Args for mqtt. Defaults to None.
        """
        for role in self._send_msg_subs:
            self._send_msg_subs[role](
                content, receiver_addr, receiver_id, create_acl, acl_metadata, mqtt_kwargs)
        return await self._container.send_message(content=content,
                                                  receiver_addr=receiver_addr,
                                                  receiver_id=receiver_id,
                                                  create_acl=create_acl,
                                                  acl_metadata=acl_metadata,
                                                  mqtt_kwargs=mqtt_kwargs)

    @property
    def addr(self):
        return self._container.addr

    @property
    def aid(self):
        return self._aid


class RoleAgent(Agent):
    """Agent, which support the role API-system. When you want to use the role-api you always need
    a RoleAgent as base for your agents. A role can be added with :func:`RoleAgent.add_role`.
    """

    def __init__(self, container):
        super().__init__(container)

        self._role_handler = RoleHandler()
        self._agent_context = RoleAgentContext(
            container, self._role_handler, self.aid, self.inbox, self._scheduler)

    def add_role(self, role: Role):
        """Add a role to the agent. This will lead to the call of :func:`Role.setup`.

        Args:
            role (Role): the role to add
        """
        role.bind(self._agent_context)
        self._agent_context.add_role(role)

        # Setup role
        role.setup()

    @property
    def roles(self) -> List[Role]:
        """Returns list of roles

        Returns:
            [List[Role]]: list of roles
        """
        return self._role_handler.roles

    def handle_msg(self, content, meta: Dict[str, Any]):
        self._agent_context.handle_msg(content, meta)

    async def shutdown(self):
        await self._role_handler.on_stop()

        await super().shutdown()
