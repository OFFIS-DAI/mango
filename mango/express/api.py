import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from ..agent.core import Agent
from ..agent.role import Role, RoleAgent
from ..container.core import AgentAddress, Container
from ..container.factory import create_mqtt, create_tcp
from ..messages.codecs import Codec

logger = logging.getLogger(__name__)


class ContainerActivationManager:
    def __init__(self, containers: list[Container]) -> None:
        self._containers = containers

    async def __aenter__(self):
        await asyncio.gather(*[c.start() for c in self._containers])
        for container in self._containers:
            container.on_ready()
        if len(self._containers) == 1:
            return self._containers[0]
        return self._containers

    async def __aexit__(self, exc_type, exc, tb):
        await asyncio.gather(*[c.shutdown() for c in self._containers])


class RunWithContainer(ABC):
    def __init__(self, num: int, *agents: tuple[Agent, dict]) -> None:
        self._num = num
        self._agents = agents
        self.__activation_cm = None

    @abstractmethod
    def create_container_list(self, num) -> list[Container]:
        pass

    async def after_start(self, container_list, agents):
        pass

    async def __aenter__(self):
        actual_number_container = self._num
        if self._num > len(self._agents):
            actual_number_container = len(self._agents)
        container_list = self.create_container_list(actual_number_container)
        for i, agent_tuple in enumerate(self._agents):
            container_id = i % actual_number_container
            container = container_list[container_id]
            actual_agent = agent_tuple[0]
            agent_params = agent_tuple[1]
            container.register(
                actual_agent, suggested_aid=agent_params.get("aid", None)
            )
        self.__activation_cm = activate(container_list)
        await self.__activation_cm.__aenter__()
        await self.after_start(container_list, self._agents)
        if len(container_list) == 1:
            return container_list[0]
        return container_list

    async def __aexit__(self, exc_type, exc, tb):
        await self.__activation_cm.__aexit__(exc_type, exc, tb)


class RunWithTCPManager(RunWithContainer):
    def __init__(
        self,
        num: int,
        *agents: Agent | tuple[Agent, dict],
        addr: tuple[str, int] = ("127.0.0.1", 5555),
        codec: None | Codec = None,
        auto_port: bool = False,
    ) -> None:
        agents = [
            agent if isinstance(agent, tuple) else (agent, dict())
            for agent in agents[0]
        ]
        super().__init__(num, *agents)

        self._addr = addr
        self._codec = codec
        self.__auto_port = auto_port

    def create_container_list(self, num):
        return [
            create_tcp(
                (self._addr[0], 0 if self.__auto_port else self._addr[1] + i),
                codec=self._codec,
            )
            for i in range(num)
        ]


class RunWithMQTTManager(RunWithContainer):
    def __init__(
        self,
        num: int,
        *agents: Agent | tuple[Agent, dict],
        broker_addr: tuple[str, int] = ("127.0.0.1", 5555),
        codec: None | Codec = None,
    ) -> None:
        agents = [
            agent if isinstance(agent, tuple) else (agent, dict())
            for agent in agents[0]
        ]
        super().__init__(num, *agents)

        self._broker_addr = broker_addr
        self._codec = codec

    def create_container_list(self, num):
        return [
            create_mqtt(
                (self._broker_addr[0], self._broker_addr[1] + i),
                client_id=f"client{i}",
                codec=self._codec,
            )
            for i in range(num)
        ]

    async def after_start(self, container_list, agents):
        for i, agent_tuple in enumerate(agents):
            container_id = i % len(container_list)
            container = container_list[container_id]
            actual_agent = agent_tuple[0]
            agent_params = agent_tuple[1]
            topics = agent_params.get("topics", [])
            for topic in topics:
                await container.subscribe_for_agent(aid=actual_agent.aid, topic=topic)


def activate(*containers: Container) -> ContainerActivationManager:
    """
    Create and return an async activation context manager.
    This can be used with the `async with` syntax to run code while the container(s) are active.
    The containers are started first, after your code under `async with` will run, and
    at the end the container will shut down (even when an error occurs).

    Example:

    .. code-block:: python

        # Single container
        async with activate(container) as container:
            # do your stuff

        # Multiple container
        async with activate(container_list) as container_list:
            # do your stuff

    :return: The context manager to be used as described
    :rtype: ContainerActivationManager
    """
    if isinstance(containers[0], list):
        containers = containers[0]
    return ContainerActivationManager(list(containers))


def run_with_tcp(
    num: int,
    *agents: Agent | tuple[Agent, dict],
    addr: tuple[str, int] = ("127.0.0.1", 5555),
    codec: None | Codec = None,
    auto_port: bool = False,
) -> RunWithTCPManager:
    """
    Create and return an async context manager, which can be used to run the given
    agents in `num` automatically created tcp container. The agents are distributed
    evenly.

    .. code-block:: python

        async with run_with_tcp(2, Agent(), Agent(), (Agent(), dict(aid="my_agent_id"))) as c:
            # do your stuff

    :param num: number of tcp container
    :type num: int
    :param addr: the starting addr of the containers, defaults to ("127.0.0.1", 5555)
    :type addr: tuple[str, int], optional
    :param codec: the codec for the containers, defaults to None
    :type codec: None | Codec, optional
    :param auto_port: set if the port should be chosen automatically
    :type auto_port: bool
    :return: the async context manager to run the agents with
    :rtype: RunWithTCPManager
    """
    return RunWithTCPManager(num, agents, addr=addr, codec=codec, auto_port=auto_port)


def run_with_mqtt(
    num: int,
    *agents: tuple[Agent, dict],
    broker_addr: tuple[str, int] = ("127.0.0.1", 1883),
    codec: None | Codec = None,
) -> RunWithMQTTManager:
    """Create and return an async context manager, which can be used to run the given
    agents in `num` automatically created mqtt container. The agents are distributed according
    to the topic

    The function takes a list of agents which shall run, it is possible to provide a tuple
    (Agent, dict), the dict supports "aid" for the suggested_aid and "topics" as list of topics the agent
    wants to subscribe to.

    :param num: _description_
    :type num: int
    :param broker_addr: Address of the broker the container shall connect to, defaults to ("127.0.0.1", 1883)
    :type broker_addr: tuple[str, int], optional
    :param codec: _description_, defaults to None
    :type codec: None | Codec, optional, The codec of the container
    :return: the async context manager
    :rtype: RunWithMQTTManager
    """
    return RunWithMQTTManager(num, agents, broker_addr=broker_addr, codec=codec)


class RunWithSimulationManager:
    """Async context manager that sets up and tears down a :class:`~mango.simulation.world.SimulationWorld`."""

    def __init__(
        self,
        *agents: Agent | tuple[Agent, dict],
        start_time: float = 0.0,
        communication_sim=None,
        environment=None,
    ) -> None:
        self._agent_tuples = [
            agent if isinstance(agent, tuple) else (agent, {}) for agent in agents
        ]
        self._start_time = start_time
        self._communication_sim = communication_sim
        self._environment = environment
        self._world = None

    async def __aenter__(self):
        from ..simulation.world import create_world

        self._world = create_world(
            start_time=self._start_time,
            communication_sim=self._communication_sim,
            environment=self._environment,
        )
        for agent, params in self._agent_tuples:
            self._world.register(agent, suggested_aid=params.get("aid"))
        await self._world.__aenter__()
        return self._world

    async def __aexit__(self, exc_type, exc, tb):
        if self._world is not None:
            await self._world.__aexit__(exc_type, exc, tb)


def run_with_simulation(
    *agents: Agent | tuple[Agent, dict],
    start_time: float = 0.0,
    communication_sim=None,
    environment=None,
) -> RunWithSimulationManager:
    """Create and return an async context manager backed by a :class:`~mango.simulation.world.SimulationWorld`.

    Agents are registered in a single simulation world.  Pass a custom
    *communication_sim* or *environment* to override the defaults.

    .. code-block:: python

        from mango import run_with_simulation, step_simulation

        async with run_with_simulation(MyAgent(), MyAgent()) as world:
            await step_simulation(world, step_size_s=1.0)

    :param agents: agent instances or ``(agent, {"aid": "preferred_id"})`` tuples
    :param start_time: initial simulation clock time in seconds (default ``0.0``)
    :param communication_sim: custom communication simulation; defaults to
        :class:`~mango.simulation.communication.SimpleCommunicationSimulation`
        with zero delay and no loss
    :param environment: custom environment; defaults to
        :class:`~mango.simulation.environment.DefaultEnvironment`
    :return: async context manager that yields the :class:`~mango.simulation.world.SimulationWorld`
    :rtype: RunWithSimulationManager
    """
    return RunWithSimulationManager(
        *agents,
        start_time=start_time,
        communication_sim=communication_sim,
        environment=environment,
    )


def behavior_in(
    world,
    func,
    *,
    on_message=None,
    on_global_event=None,
    on_agent_event=None,
    agent_types=(),
    role_types=(),
    has_roles=(),
    match_names=(),
    match_colors=(),
    preprocessor=None,
):
    """Attach message handlers and event subscriptions to a matched set of agents.

    This is a simulation-only helper that lets you inject behavior into agents
    without modifying their class definitions.  All matching criteria are
    optional; if none are given, every agent in *world* is matched.

    The handler *func* is called with the matched agent (or role, when
    *role_types* is used) as the first argument:

    - ``on_message``: ``func(agent, content, meta)``
    - ``on_global_event``: ``func(agent, event)``
    - ``on_agent_event``: ``func(agent, event)``

    When *role_types* is provided the first argument is the matched role
    instead of the agent.

    :param world: the :class:`~mango.simulation.world.SimulationWorld`
    :param func: handler callable
    :param on_message: message type to match (``isinstance`` check), or
        ``None`` to skip message subscription
    :param on_global_event: event type to match for global events
    :param on_agent_event: event type to match for targeted agent events
    :param agent_types: restrict to agents that are instances of these types
    :param role_types: attach handler to matching *roles* (first arg is role)
    :param has_roles: restrict to agents that have at least one of these roles
    :param match_names: restrict to agents whose ``name`` is in this collection
    :param match_colors: restrict to agents whose ``color`` is in this collection
    :param preprocessor: optional
        :class:`~mango.agent.role.MessagePreprocessor` for message handling

    Example::

        from mango import behavior_in

        behavior_in(
            world,
            lambda agent, content, meta: print(agent.aid, content),
            on_message=str,
            agent_types=MyAgent,
        )
    """
    if isinstance(agent_types, type):
        agent_types = (agent_types,)
    if isinstance(role_types, type):
        role_types = (role_types,)
    if isinstance(has_roles, type):
        has_roles = (has_roles,)
    if isinstance(match_names, str):
        match_names = (match_names,)
    if isinstance(match_colors, str):
        match_colors = (match_colors,)

    no_filter = (
        not agent_types and not has_roles and not match_names and not match_colors
    )

    def _matches_agent(agent):
        if no_filter:
            return True
        if agent_types and isinstance(agent, tuple(agent_types)):
            return True
        if has_roles:
            agent_roles = getattr(agent, "roles", [])
            if any(isinstance(r, tuple(has_roles)) for r in agent_roles):
                return True
        if match_names and agent.name in match_names:
            return True
        if match_colors and agent.color in match_colors:
            return True
        return False

    for agent in list(world.agents.values()):
        if not _matches_agent(agent):
            continue

        if role_types:
            agent_roles = getattr(agent, "roles", [])
            for role in agent_roles:
                if isinstance(role, tuple(role_types)):
                    _attach_behavior_to_role(
                        agent,
                        role,
                        func,
                        on_message,
                        on_global_event,
                        on_agent_event,
                        preprocessor,
                    )
        else:
            _attach_behavior_to_agent(
                agent, func, on_message, on_global_event, on_agent_event, preprocessor
            )


def _attach_behavior_to_agent(
    agent, func, on_message, on_global_event, on_agent_event, preprocessor
):
    if on_message is not None:
        cond = (lambda t: lambda c, m: isinstance(c, t))(on_message)
        agent._behavior_message_subs.append((cond, func, preprocessor))
    if on_global_event is not None:
        cond = (lambda t: lambda e: isinstance(e, t))(on_global_event)
        agent._behavior_global_event_handlers.append((cond, func))
    if on_agent_event is not None:
        cond = (lambda t: lambda e: isinstance(e, t))(on_agent_event)
        agent._behavior_agent_event_handlers.append((cond, func))


def _attach_behavior_to_role(
    agent, role, func, on_message, on_global_event, on_agent_event, preprocessor
):
    if on_message is not None:
        if hasattr(agent, "_role_handler"):

            def _method(content, meta, _role=role):
                func(_role, content, meta)

            agent._role_handler.subscribe_message(
                role,
                _method,
                (lambda t: lambda c, m: isinstance(c, t))(on_message),
                preprocessor=preprocessor,
            )
    if on_global_event is not None:
        cond = (lambda t: lambda e: isinstance(e, t))(on_global_event)
        role._behavior_global_event_handlers.append((cond, func))
    if on_agent_event is not None:
        cond = (lambda t: lambda e: isinstance(e, t))(on_agent_event)
        role._behavior_agent_event_handlers.append((cond, func))


class ComposedAgent(RoleAgent):
    pass


def agent_composed_of(
    *roles: Role, register_in: None | Container = None, suggested_aid: None | str = None
) -> ComposedAgent:
    """
    Create an agent composed of the given `roles`. If a container is provided,
    the created agent is automatically registered with the container `register_in`.


    :param register_in: container in which the created agent is registered,
                                        if provided
    :type register_in: None | Container
    :param suggested_aid: the suggested aid for registration
    :type suggested_aid: str
    :return: the composed agent
    :rtype: ComposedAgent
    """
    agent = ComposedAgent()
    for role in roles:
        agent.add_role(role)
    if register_in is not None:
        register_in.register(agent, suggested_aid=suggested_aid)
    return agent


class PrintingAgent(Agent):
    def handle_message(self, content, meta: dict[str, Any]):
        print(f"Received: {content} with {meta}")


def sender_addr(meta: dict) -> AgentAddress:
    """
    Extract the sender_addr from the meta dict.

    Args:
        meta (dict): the meta you received

    Returns:
        AgentAddress: Extracted agent address to be used for replying to messages
    """
    # convert decoded addr list to tuple for hashability
    return AgentAddress(
        tuple(meta["sender_addr"])
        if isinstance(meta["sender_addr"], list)
        else meta["sender_addr"],
        meta["sender_id"],
    )


def addr(protocol_addr: Any, aid: str) -> AgentAddress:
    """
    Create an Address from the topic.

    Args:
        protocol_addr (Any): the container part of the addr, e.g. topic for mqtt, or host/port for tcp, ...
        aid (str): the agent id

    Returns:
        AgentAddress: the address
    """
    return AgentAddress(protocol_addr, aid)
