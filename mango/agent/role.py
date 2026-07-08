"""
API classes for using the role system. The role system is based on the idea, that
everything an agent can do, is described as role/responsibility and is implemented in
one separate class. For example participating in a coalition would be a separate role,
monitoring grid voltage another one.

A role is part of a :class:`RoleAgent` which inherits from :class:`Agent`.

There are essentially two APIs for acting resp reacting:

* [Reacting] :func:`RoleContext.subscribe_message`, which allows you to subscribe to certain message types and lets you handle the message
* [Acting] :func:`RoleContext.schedule_task`, this allows you to schedule a task with delay/repeating/...

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
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from mango.agent.core import Agent, AgentAddress, AgentDelegates


class MessagePreprocessor(ABC):
    """Abstract base for message preprocessors in the role system.

    A preprocessor intercepts messages before they reach a role's handler,
    allowing transformation or rate-limiting.  Pass an instance to
    :meth:`RoleContext.subscribe_message` via the *preprocessor* keyword.

    Subclasses must implement :meth:`handle`.  Override :meth:`process` to
    transform the message content/meta before delivery.

    Example::

        class LoggingPreprocessor(MessagePreprocessor):
            def handle(self, role, handler, content, meta):
                print(f"[{role}] received: {content}")
                handler(content, meta)

        class MyRole(Role):
            def setup(self):
                self.context.subscribe_message(
                    self, self.on_msg, lambda c, m: True,
                    preprocessor=LoggingPreprocessor(),
                )

            def on_msg(self, content, meta):
                ...
    """

    def init(self, role_or_agent: Any) -> None:
        """Called once when the preprocessor is registered.

        :param role_or_agent: the role (or agent) that owns the subscription
        """

    @abstractmethod
    def handle(
        self,
        role_or_agent: Any,
        handler: Callable,
        content: Any,
        meta: dict,
    ) -> None:
        """Intercept a message.  Must call *handler(content, meta)* to deliver.

        :param role_or_agent: the subscribing role or agent
        :param handler: the original message handler
        :param content: message content
        :param meta: message metadata
        """

    def process(self, content: Any, meta: dict) -> tuple[Any, dict]:
        """Transform message before delivery.  Default: identity.

        :param content: message content
        :param meta: message metadata
        :return: transformed ``(content, meta)`` tuple
        """
        return content, meta


class WaitingMessagePreprocessor(MessagePreprocessor):
    """Prevents concurrent message handling for a role.

    Messages are queued and dispatched one at a time.  The next message is
    only delivered after the handler for the current one has returned (or its
    coroutine completed).  This avoids race conditions when a role's handler
    is async or triggers further messages.

    Example::

        class MyRole(Role):
            def setup(self):
                self.context.subscribe_message(
                    self, self.on_data, lambda c, m: True,
                    preprocessor=WaitingMessagePreprocessor(),
                )

            async def on_data(self, content, meta):
                await asyncio.sleep(0.1)   # safe – next msg waits
                ...
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running: bool = False

    def init(self, role_or_agent: Any) -> None:
        pass

    def handle(
        self,
        role_or_agent: Any,
        handler: Callable,
        content: Any,
        meta: dict,
    ) -> None:
        content, meta = self.process(content, meta)
        self._queue.put_nowait((handler, content, meta))
        if not self._running:
            self._running = True
            asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        while not self._queue.empty():
            handler, content, meta = self._queue.get_nowait()
            result = handler(content, meta)
            if asyncio.iscoroutine(result):
                await result
        self._running = False


class DataContainer:
    def __getitem__(self, key):
        return self.__getattribute__(key)

    def __setitem__(self, key, newvalue):
        self.__setattr__(key, newvalue)

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        if key in self:
            return self[key]
        else:
            return default

    def update(self, data: dict):
        for k, v in data.items():
            self.__setattr__(k, v)


class Role:
    pass


class RoleHandler:
    """Contains all roles and their models. Implements the communication between roles."""

    def __init__(self, scheduler):
        self._role_models = {}
        self._roles = []
        self._role_to_active = {}
        self._role_model_type_to_subs = {}
        self._message_subs = []
        self._send_msg_subs = {}
        self._role_event_type_to_handler = {}
        self._scheduler = scheduler
        self._data = DataContainer()
        # Back-reference to the owning :class:`RoleAgent`.  Set by
        # ``RoleAgent.__init__`` so role-level helpers like
        # :meth:`RoleContext.gather` can reach the agent-level
        # gather/transaction machinery without an indirection through
        # the agent context.
        self._agent: Agent | None = None

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

    def remove_role(self, role: Role) -> None:
        """Remove a given role

        Args:
            role ([type]): the role
        """
        self._roles.remove(role)
        del self._role_to_active[role]

    @property
    def roles(self) -> list[Role]:
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

    def handle_message(self, content, meta: dict[str, Any]):
        """Handle an incoming message, delegating it to all applicable subscribers

        .. code-block:: python

            for role, message_condition, method, _, preprocessor in self._message_subs:
                if self._is_role_active(role) and message_condition(content, meta):
                    if preprocessor:
                        preprocessor.handle(role, method, content, meta)
                    else:
                        method(content, meta)

        :param content: content
        :param meta: meta
        """
        handle_message_found = False
        for role, message_condition, method, _, preprocessor in self._message_subs:
            # do not execute handle_message twice if role has subscription as well
            if method.__name__ == "handle_message":
                handle_message_found = True
            if self._is_role_active(role) and message_condition(content, meta):
                if preprocessor is not None:
                    preprocessor.handle(role, method, content, meta)
                else:
                    method(content, meta)
        if not handle_message_found:
            for role in self.roles:
                role.handle_message(content, meta)

    def _notify_send_message_subs(self, content, receiver_addr: AgentAddress, **kwargs):
        for role in self._send_msg_subs:
            for sub in self._send_msg_subs[role]:
                if self._is_role_active(role):
                    sub(
                        content=content,
                        receiver_addr=receiver_addr,
                        **kwargs,
                    )

    def subscribe_message(
        self,
        role,
        method,
        message_condition,
        priority=0,
        preprocessor: "MessagePreprocessor | None" = None,
    ):
        if preprocessor is not None:
            preprocessor.init(role)
        entry = (role, message_condition, method, priority, preprocessor)
        if len(self._message_subs) == 0:
            self._message_subs.append(entry)
            return

        for i in range(len(self._message_subs)):
            _, _, _, other_prio, _ = self._message_subs[i]
            if priority < other_prio:
                self._message_subs.insert(i, entry)
                break
            elif i == len(self._message_subs) - 1:
                self._message_subs.append(entry)

    def subscribe_send(self, role: Role, method: Callable):
        if role in self._send_msg_subs:
            self._send_msg_subs[role].append(method)
        else:
            self._send_msg_subs[role] = [method]

    def emit_event(self, event: Any, event_source: Any = None, *, strict: bool = False):
        """Dispatch *event* to every subscribed handler on this agent.

        :param strict: when True, raise :class:`KeyError` if no role is
            subscribed to ``type(event)``.  Default is False — events
            without subscribers are silently dropped, matching the
            fire-and-forget semantics callers expect from a
            notification API and removing the ``try/except KeyError``
            guard pattern that otherwise wraps every ``emit_event``
            call site.
        """
        subs = self._role_event_type_to_handler.get(type(event))
        if subs is None:
            if strict:
                raise KeyError(type(event))
            return
        for _, method in subs:
            method(event, event_source)

    def subscribe_event(self, role: Role, event_type: type, method: Callable):
        if event_type not in self._role_event_type_to_handler:
            self._role_event_type_to_handler[event_type] = []

        self._role_event_type_to_handler[event_type] += [(role, method)]

    def on_start(self):
        for role in self.roles:
            role.on_start()

    def on_ready(self):
        for role in self.roles:
            role.on_ready()


class RoleContext(AgentDelegates):
    """Implementation of the RoleContext."""

    def __init__(
        self,
        role_handler: RoleHandler,
        aid: str,
        inbox,
    ):
        super().__init__()
        self._role_handler = role_handler
        self._aid = aid
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
        return self.context.current_timestamp

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

    def subscribe_message(
        self,
        role,
        method,
        message_condition,
        priority=0,
        preprocessor: "MessagePreprocessor | None" = None,
    ):
        self._role_handler.subscribe_message(
            role,
            method,
            message_condition,
            priority=priority,
            preprocessor=preprocessor,
        )

    def subscribe_send(self, role, method):
        self._role_handler.subscribe_send(role, method)

    def add_role(self, role: Role):
        """Add a role to the context.

        :param role: the Role
        """
        role._bind(self)
        self._role_handler.add_role(role)

        # Setup role
        role.setup()

    def get_role(self, cls: type) -> Role | None:
        """
        returns the first role of a given class
        returns None if no role of this type exists in the current context
        """
        for role in self._role_handler.roles:
            if isinstance(role, cls):
                return role
        return None

    def remove_role(self, role: Role):
        """Remove a role and call on_stop for clean up

        :param role: the role to remove
        :type role: Role
        """
        self._role_handler.remove_role(role)
        asyncio.create_task(role.on_stop())

    def handle_message(self, content, meta: dict[str, Any]):
        """Handle an incoming message, delegating it to all applicable subscribers

        .. code-block:: python

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
        receiver_addr: AgentAddress,
        **kwargs,
    ) -> bool:
        self._role_handler._notify_send_message_subs(content, receiver_addr, **kwargs)
        return await self.context.send_message(
            content=content,
            receiver_addr=receiver_addr,
            sender_id=self.aid,
            **kwargs,
        )

    def emit_event(self, event: Any, event_source: Any = None, *, strict: bool = False):
        """Emit an custom event to other roles.

        :param event: the event
        :type event: Any
        :param event_source: emitter of the event (mostly the emitting role), defaults to None
        :type event_source: Any, optional
        :param strict: when True, raise :class:`KeyError` if no role
            is subscribed to ``type(event)``.  Default False — see
            :meth:`RoleHandler.emit_event` for the rationale.
        """
        self._role_handler.emit_event(event, event_source, strict=strict)

    def subscribe_event(self, role: Role, event_type: Any, handler_method: Callable):
        """Subscribe to specific event types. The listener will be evaluated based
        on their order of subscription

        :param role: the role in which you want to handle the event
        :type role: Role
        :param event_type: the event type you want to handle
        :type event_type: Any
        """
        self._role_handler.subscribe_event(role, event_type, handler_method)

    def deactivate(self, role) -> None:
        self._role_handler.deactivate(role)

    def activate(self, role) -> None:
        self._role_handler.activate(role)

    # ------------------------------------------------------------------
    # Topology link-health queries (see mango.express.health)
    # ------------------------------------------------------------------

    def neighbour_score(self, neighbour_addr, *, tid: str = "default") -> float | None:
        """Return the current edge health for one neighbour, or ``None``
        when the topology does not have ``edge_health`` enabled."""
        agent = self._role_handler._agent
        if agent is None:
            return None
        from mango.agent.core import TopologyService

        svc = agent.service_of_type(TopologyService, None)
        if svc is None:
            return None
        health = svc.health_runtime(tid)
        if health is None:
            return None
        now = agent.scheduler.clock.time if agent.scheduler else 0.0
        return health.score(agent.addr, neighbour_addr, now)

    def neighbour_scores(self, tid: str = "default") -> dict:
        """Return ``{neighbour_addr: score}`` for every neighbour in *tid*.

        Empty when the agent is unbound or the topology has no
        ``edge_health`` configured.  Complements :meth:`neighbour_score`
        (single neighbour) and :meth:`live_neighbours` (threshold filter).
        """
        agent = self._role_handler._agent
        if agent is None:
            return {}
        from mango.agent.core import State, TopologyService

        svc = agent.service_of_type(TopologyService, None)
        if svc is None:
            return {}
        health = svc.health_runtime(tid)
        if health is None:
            return {}
        now = agent.scheduler.clock.time if agent.scheduler else 0.0
        my_addr = agent.addr
        return {
            n: health.score(my_addr, n, now)
            for n in svc.neighbors(state=State.NORMAL, tid=tid)
        }

    def live_neighbours(
        self,
        tid: str = "default",
        *,
        threshold: float | None = None,
    ):
        """Return the subset of ``topology_neighbors(tid=tid)`` whose
        edge health is at or above *threshold*.

        Falls back to the full neighbour list when the topology has no
        ``edge_health`` configured — callers can use this method
        unconditionally without having to branch on whether tracking is
        enabled.
        """
        agent = self._role_handler._agent
        if agent is None:
            return []
        from mango.agent.core import State, TopologyService

        svc = agent.service_of_type(TopologyService, None)
        if svc is None:
            return []
        all_neighbours = svc.neighbors(state=State.NORMAL, tid=tid)
        health = svc.health_runtime(tid)
        if health is None:
            return all_neighbours
        now = agent.scheduler.clock.time if agent.scheduler else 0.0
        my_addr = agent.addr
        return [
            n
            for n in all_neighbours
            if health.is_live(my_addr, n, now, threshold=threshold)
        ]

    def _bound_agent(self, operation: str):
        """Return the owning :class:`RoleAgent`, or raise a clear error.

        Conversation- and gather-style helpers need access to the
        agent's scheduler clock and message-routing tables; this
        override produces a uniform error when a role is used before
        its agent attaches.
        """
        agent = self._role_handler._agent
        if agent is None:
            raise RuntimeError(
                f"{operation} requires a bound RoleAgent — the role's "
                "context is not attached yet."
            )
        return agent

    def on_start(self):
        self._role_handler.on_start()

    def on_ready(self):
        self._role_handler.on_ready()


class RoleAgent(Agent):
    """Agent, which support the role API-system. When you want to use the role-api you always need
    a RoleAgent as base for your agents. A role can be added with :func:`RoleAgent.add_role`.
    """

    def __init__(self):
        """Create a role-agent

        :param container: container the agent lives in
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used.
                              Using the generated aid-style ("agentX") is not allowed.
        """
        super().__init__()
        self._role_handler = RoleHandler(None)
        self._role_handler._agent = self
        self._role_context = RoleContext(self._role_handler, self.aid, self.inbox)

    def on_start(self):
        self._role_context.on_start()

    def on_ready(self):
        self._role_context.on_ready()

    def on_register(self):
        self._role_context.context = self.context
        self._role_context.scheduler = self.scheduler
        self._role_handler._scheduler = self.scheduler
        self._role_context._aid = self.aid

    def add_role(self, role: Role):
        """Add a role to the agent. This will lead to the call of :func:`Role.setup`.

        :param role: the role to add
        """
        self._role_context.add_role(role)

    def remove_role(self, role: Role):
        """Remove a role permanently from the agent.

        :param role: [description]
        :type role: Role
        """
        self._role_context.remove_role(role)

    @property
    def roles(self) -> list[Role]:
        """Returns list of roles

        :return: list of roles
        """
        return self._role_handler.roles

    def handle_message(self, content, meta: dict[str, Any]):
        self._role_context.handle_message(content, meta)

    async def shutdown(self):
        await self._role_handler.on_stop()
        await super().shutdown()


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
        self._behavior_global_event_handlers: list[tuple] = []
        self._behavior_agent_event_handlers: list[tuple] = []

    def _bind(self, context: RoleContext) -> None:
        """Method used internal to set the context, do not override!

        :param context: the role context
        """
        self._context = context
        # Apply class-level @on_message / @on_event / @periodic
        # decorators before user-defined ``setup`` runs, so the explicit
        # setup body can add to (not remove from) the declarative wiring.
        from mango.agent.decorators import apply_dispatch

        apply_dispatch(self)

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

    def on_start(self) -> None:
        """Called when container started in which the agent is contained"""

    def on_ready(self):
        """Called after the start of all container using activate"""

    def handle_message(self, content: Any, meta: dict):
        pass

    def on_step(self, env, clock, step_size_s: float) -> None:
        """Called on every simulation step (only in SimulationWorld).

        :param env: the simulation environment
        :param clock: the current simulation clock
        :param step_size_s: seconds advanced in this step
        """

    def on_global_event(self, event: Any) -> None:
        """Called when a global event is emitted from the environment.

        :param event: the event object
        """

    def on_agent_event(self, event: Any) -> None:
        """Called when a targeted agent event is emitted.

        :param event: the event object
        """
