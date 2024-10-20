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
