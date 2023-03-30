"""
API classes for using the role system. The role system is based on the idea, that
everything an agent can do, is described as role/responsibility and is implemented in
one separate class. For example participating in a coalition would be a separate role,
monitoring grid voltage another one.

A role is part of a :class:`RoleAgent` which inherits from :class:`Agent`.

There are essentially two APIs for acting resp reacting:
* [Reacting] :func:`RoleContext.subscribe_message`, which allows you to subscribe to
             certain message types and lets you handle the message
* [Acting] :func:`RoleContext.schedule_task`, this allows you to schedule a task with
            delay/repeating/...

To interact with the environment an instance of the role context is provided. This context
provides methods to share data with other roles and to communicate with other agents.

A message can be send using the method :func:`RoleContext.send_message`.

There are often dependencies between different parts of an agent, there are options to
interact with other roles: Roles have the possibility to use shared models and to act on
changes of these models. A role can subscribe specific data that another role provides.
To set this up, a model has to be created via
:func:`RoleContext.get_or_create_model`. To notify other roles
:func:`RoleContext.update` has to be called. In order to let a Role subscribe to a model you can use
:func:`subscribe_model`.
If you prefer a lightweight variant you can use :func:`RoleContext.data` to assign/access shared data.

Furthermore there are two lifecycle methods to know about:
* :func:`Role.setup` is called when the Role is added to the agent, so its the perfect place
                     for initialization and scheduling of tasks
* :func:`Role.on_stop` is called when the container the agent lives in, is shut down
"""
import asyncio
from abc import ABC
from typing import Any, Dict, List, Optional, Tuple, Union

from mango.agent.core import Agent, AgentContext, AgentDelegates
from mango.util.scheduling import Scheduler


class DataContainer:
    def __getitem__(self, key):
        return self.__getattribute__(key)


class RoleContext:
    pass


class Role(ABC):
    """General role class, defining the API every role can use. A role implements one responsibility
    of an agent.

    Every role
    must be added to a :class:`RoleAgent` and is defined by some lifecycle methods:
    * :func:`Role.setup` is called when the Role is added to the agent, so its the perfect place for
                         initialization and scheduling of tasks
    * :func:`Role.on_stop` is called when the container the agent lives in, is shut down

    To interact with the environment you have to use the context, accessible via :func:Role.context.
    """

    def __init__(self) -> None:
        """Initialize the roles internals.
        !!Care!! the role context is unknown at this point!
        """
        self._context = None

    def bind(self, context: RoleContext) -> None:
        """Method used internal to set the context, do not override!

        :param context: the role context
        """
        self._context = context

    @property
    def context(self) -> RoleContext:
        """Return the context of the role. This context can be send as bridge to the agent.

        :return: the context of the role
        """
        return self._context

    def setup(self) -> None:
        """Lifecycle hook in, which will be called on adding the role to agent. The role context
        is known from hereon.
        """

    def on_change_model(self, model) -> None:
        """Will be invoked when a subscribed model changes via :func:`RoleContext.update`.

        :param model: the model
        """

    def on_deactivation(self, src) -> None:
        """Hook in, which will be called when another role deactivates this instance (temporarily)"""

    async def on_stop(self) -> None:
        """Lifecycle hook in, which will be called when the container is shut down or if the role got removed."""


class RoleHandler:
    """Contains all roles and their models. Implements the communication between roles."""

    def __init__(self, agent_context, scheduler):
        self._role_models = {}
        self._roles = []
        self._role_to_active = {}
        self._role_model_type_to_subs = {}
        self._message_subs = []
        self._send_msg_subs = {}
        self._agent_context = agent_context
        self._scheduler = scheduler
        self._data = DataContainer()

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
                if self._is_role_active(role):
                    role.on_change_model(role_model)

    def subscribe(self, role: Role, role_model_type) -> None:
        """Subscribe a role to change events of a specific role model type

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
        self._role_to_active[role] = True

    @property
    def roles(self) -> List[Role]:
        """Returns all roles

        Returns:
            List[Role]: the roles hold by this handler
        """
        return self._roles

    def deactivate(self, role) -> None:
        """Deactivates the role. This includes all tasks (soft suspending)

        :param role: the role to deactivate
        :type role: Role
        """
        self._role_to_active[role] = False
        self._scheduler.suspend(role)

    def activate(self, role) -> None:
        """Activates the given role.

        :param role: the role to activate
        :type role: Role
        """
        self._role_to_active[role] = True
        self._scheduler.resume(role)

    def _is_role_active(self, role) -> bool:
        if role in self._role_to_active:
            return self._role_to_active[role]
        return True

    async def on_stop(self):
        """Notify all roles when the container is shutdown"""
        for role in self._roles:
            await role.on_stop()

    def handle_message(self, content, meta: Dict[str, Any]):
        """Handle an incoming message, delegating it to all applicable subscribers
        for role, message_condition, method, _ in self._message_subs:
            if self._is_role_active(role) and message_condition(content, meta):
                method(content, meta)

        :param content: content
        :param meta: meta
        """
        for role, message_condition, method, _ in self._message_subs:
            if self._is_role_active(role) and message_condition(content, meta):
                method(content, meta)

    def _notify_send_message_subs(self, content, receiver_addr, receiver_id, **kwargs):
        for role in self._send_msg_subs:
            for sub in self._send_msg_subs[role]:
                if self._is_role_active(role):
                    sub(
                        content=content,
                        receiver_addr=receiver_addr,
                        receiver_id=receiver_id,
                        **kwargs
                    )

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        **kwargs
    ):
        self._notify_send_message_subs(content, receiver_addr, receiver_id, **kwargs)
        return await self._agent_context.send_message(
            content=content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            **kwargs
        )

    async def send_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        self._notify_send_message_subs(content, receiver_addr, receiver_id, **kwargs)
        return await self._agent_context.send_acl_message(
            content=content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            acl_metadata=acl_metadata,
            **kwargs
        )

    def subscribe_message(self, role, method, message_condition, priority=0):
        if len(self._message_subs) == 0:
            self._message_subs.append((role, message_condition, method, priority))
            return

        for i in range(len(self._message_subs)):
            _, _, _, other_prio = self._message_subs[i]
            if priority < other_prio:
                self._message_subs.insert(
                    i, (role, message_condition, method, priority)
                )
                break
            elif i == len(self._message_subs) - 1:
                self._message_subs.append((role, message_condition, method, priority))

    def subscribe_send(self, role, method):
        if role in self._send_msg_subs:
            self._send_msg_subs[role].append(method)
        else:
            self._send_msg_subs[role] = [method]


class RoleContext(AgentDelegates):
    """Implementation of the RoleContext."""

    def __init__(
        self,
        agent_context: AgentContext,
        scheduler: Scheduler,
        role_handler: RoleHandler,
        aid: str,
        inbox,
    ):
        self._agent_context = agent_context
        self._role_handler = role_handler
        self._aid = aid
        self._scheduler = scheduler
        self._inbox = inbox

    @property
    def data(self):
        """Return data container of the agent

        :return: the data container
        :rtype: DataContainer
        """
        return self._get_container()

    @property
    def current_timestamp(self) -> float:
        return self._agent_context.current_timestamp

    def _get_container(self):
        return self._role_handler._data

    def inbox_length(self):
        return self._inbox.qsize()

    def get_or_create_model(self, cls):
        return self._role_handler.get_or_create_model(cls)

    def update(self, role_model):
        self._role_handler.update(role_model)

    def subscribe_model(self, role, role_model_type):
        self._role_handler.subscribe(role, role_model_type)

    def subscribe_message(self, role, method, message_condition, priority=0):
        self._role_handler.subscribe_message(
            role, method, message_condition, priority=priority
        )

    def subscribe_send(self, role, method):
        self._role_handler.subscribe_send(role, method)

    def add_role(self, role: Role):
        """Add a role to the context.

        :param role: the Role
        """
        self._role_handler.add_role(role)

    def handle_message(self, content, meta: Dict[str, Any]):
        """Handle an incoming message, delegating it to all applicable subscribers
        for role, message_condition, method, _ in self._message_subs:
            if self._is_role_active(role) and message_condition(content, meta):
                method(content, meta)

        :param content: content
        :param meta: meta
        """
        self._role_handler.handle_message(content, meta)

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        **kwargs
    ):
        return await self._role_handler.send_message(
            content=content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            **kwargs
        )

    async def send_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        return await self._role_handler.send_acl_message(
            content=content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            acl_metadata=acl_metadata,
            **kwargs
        )

    @property
    def addr(self):
        return self._agent_context.addr

    @property
    def aid(self):
        return self._aid

    def deactivate(self, role) -> None:
        self._role_handler.deactivate(role)

    def activate(self, role) -> None:
        self._role_handler.activate(role)


class RoleAgent(Agent):
    """Agent, which support the role API-system. When you want to use the role-api you always need
    a RoleAgent as base for your agents. A role can be added with :func:`RoleAgent.add_role`.
    """

    def __init__(self, container, suggested_aid: str = None):
        """Create a role-agent

        :param container: container the agent lives in
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used.
                              Using the generated aid-style ("agentX") is not allowed.
        """
        super().__init__(container, suggested_aid=suggested_aid)

        self._role_handler = RoleHandler(self._context, self._scheduler)
        self._role_context = RoleContext(
            self._context, self._scheduler, self._role_handler, self.aid, self.inbox
        )

    def add_role(self, role: Role):
        """Add a role to the agent. This will lead to the call of :func:`Role.setup`.

        :param role: the role to add
        """
        role.bind(self._role_context)
        self._role_context.add_role(role)

        # Setup role
        role.setup()

    def remove_role(self, role: Role):
        """Remove a role permanently from the agent.

        :param role: [description]
        :type role: Role
        """
        self._role_context.remove_role(role)
        asyncio.create_task(role.on_stop())

    @property
    def roles(self) -> List[Role]:
        """Returns list of roles

        :return: list of roles
        """
        return self._role_handler.roles

    def handle_message(self, content, meta: Dict[str, Any]):
        self._role_context.handle_message(content, meta)

    async def shutdown(self):
        await self._role_handler.on_stop()
        await super().shutdown()
