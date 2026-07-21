"""
SimulationWorld – a self-contained simulation world for mango.

Mirrors the ``World`` type from Mango.jl.  Agents registered in a
SimulationWorld share an :class:`~mango.util.clock.ExternalClock` and can
be stepped forward in discrete or fixed-size time increments.

The world is a facade: agent registration and message transport are
handled by a :class:`~mango.simulation.container.SimulationContainer`
subcomponent, while the world drives the simulation loop, the
environment, and the recording infrastructure.

Typical usage::

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(MyAgent())

        async with world:
            await world.step(step_size_s=1.0)
            await world.step(step_size_s=1.0)

    asyncio.run(run())

Or for a fully automated discrete-event run::

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(MyAgent())
        async with world:
            await world.step_until(max_advance_time_s=60.0)

    asyncio.run(run())

The module-level :func:`step_simulation` and :func:`discrete_step_until`
are thin aliases for :meth:`SimulationWorld.step` and
:meth:`SimulationWorld.step_until`.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mango.agent.core import Agent, AgentAddress
from mango.messages.codecs import Codec
from mango.util.clock import ExternalClock
from mango.util.termination_detection import tasks_complete_or_sleeping

from .communication import CommunicationSimulation, SimpleCommunicationSimulation
from .container import MessageTransaction, SimulationContainer
from .environment import DefaultEnvironment, Environment, WorldObserver
from .recording import (
    AgentsRecording,
    WorldRecording,
    collect_agent_data,
    collect_data,
    position_history,
    record_agent,
    record_agent_having,
    record_position,
    record_world,
)

logger = logging.getLogger(__name__)

DISCRETE_EVENT: float = -1.0
AGENT_PREFIX: str = "agent"

__all__ = [
    "AGENT_PREFIX",
    "AgentsRecording",
    "DISCRETE_EVENT",
    "MessageTransaction",
    "SimulationContainer",
    "SimulationResult",
    "SimulationWorld",
    "WorldRecording",
    "collect_agent_data",
    "collect_data",
    "create_world",
    "discrete_step_until",
    "position_history",
    "record_agent",
    "record_agent_having",
    "record_position",
    "record_world",
    "step_simulation",
]


@dataclass
class SimulationResult:
    """Return value of :meth:`SimulationWorld.step`."""

    time_elapsed_s: float
    step_size_s: float
    messages_delivered: int


class _AgentDispatchObserver(WorldObserver):
    """Forwards global events to every agent in the world."""

    def __init__(self, world: "SimulationWorld"):
        self._world = world

    def dispatch_global_event(self, clock, event: Any) -> None:
        for agent in self._world.agents.values():
            for _cond, _handler in agent._behavior_global_event_handlers:
                if _cond(event):
                    _handler(agent, event)
            agent.on_global_event(event)
            if hasattr(agent, "roles"):
                for role in agent.roles:
                    for _cond, _handler in role._behavior_global_event_handlers:
                        if _cond(event):
                            _handler(role, event)
                    role.on_global_event(event)


class SimulationWorld:
    """A local, clock-driven simulation world.

    Do not instantiate directly; use :func:`create_world` instead.

    The world is a facade over a
    :class:`~mango.simulation.container.SimulationContainer` (which agents
    register against and which handles message transport), the
    :class:`~mango.simulation.environment.Environment`, and the recording
    infrastructure.  Stepping the simulation is done via :meth:`step` and
    :meth:`step_until`.
    """

    def __init__(
        self,
        clock: ExternalClock,
        communication_sim: CommunicationSimulation,
        environment: Environment | None = None,
    ):
        self.environment: Environment = environment or DefaultEnvironment()
        self._container = SimulationContainer(
            clock=clock,
            communication_sim=communication_sim,
            on_agent_registered=self._install_in_environment,
        )

        # Recording infrastructure
        self.data_collections: dict[str, WorldRecording] = {}
        self.data_agent_collections: dict[str, AgentsRecording] = {}
        self._data_collectors: list[Callable] = []

        self._initialized: bool = False

        # Wire environment to agent dispatcher
        self.environment.add_observer(_AgentDispatchObserver(self))

    # ------------------------------------------------------------------
    # Container facade
    # ------------------------------------------------------------------

    @property
    def container(self) -> SimulationContainer:
        return self._container

    @property
    def clock(self) -> ExternalClock:
        return self._container.clock

    @property
    def communication_sim(self) -> CommunicationSimulation:
        return self._container.communication_sim

    @communication_sim.setter
    def communication_sim(self, value: CommunicationSimulation) -> None:
        self._container.communication_sim = value

    @property
    def addr(self) -> str:
        return self._container.addr

    @property
    def name(self) -> str:
        return self._container.name

    @property
    def codec(self) -> Codec:
        return self._container.codec

    @property
    def agents(self) -> dict[str, Agent]:
        return self._container._agents

    @property
    def running(self) -> bool:
        return self._container.running

    @running.setter
    def running(self, value: bool) -> None:
        self._container.running = value

    @property
    def ready(self) -> bool:
        return self._container.ready

    @property
    def recorded_messages(self) -> list[MessageTransaction]:
        return self._container.recorded_messages

    @recorded_messages.setter
    def recorded_messages(self, value: list[MessageTransaction]) -> None:
        self._container.recorded_messages = value

    def register(
        self, agent: Agent, suggested_aid: str | None = None, **install_kwargs
    ) -> Agent:
        """Register *agent* with the world and return it.

        :param agent: agent instance to register
        :param suggested_aid: optional preferred agent ID
        :param install_kwargs: forwarded to the environment's ``install`` hook
        :return: the registered agent (same object)
        """
        return self._container.register(
            agent, suggested_aid=suggested_aid, **install_kwargs
        )

    def deregister(self, aid: str) -> None:
        self._container.deregister(aid)

    def is_aid_available(self, aid: str) -> bool:
        return self._container.is_aid_available(aid)

    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: str | None = None,
        **kwargs,
    ) -> bool:
        """Send a message, applying communication simulation.

        Messages are queued with a delivery time determined by the
        communication simulation.  They are delivered during the next call
        to :meth:`step`.
        """
        return await self._container.send_message(
            content, receiver_addr=receiver_addr, sender_id=sender_id, **kwargs
        )

    async def shutdown(self) -> None:
        """Shut down all agents."""
        await self._container.shutdown()

    def __getitem__(self, key: str | int) -> Agent:
        if isinstance(key, int):
            return list(self.agents.values())[key]
        return self.agents[key]

    def _install_in_environment(self, agent: Agent, aid: str, **install_kwargs) -> None:
        install = getattr(self.environment, "install", None)
        if callable(install):
            install(agent, agent_id=aid, **install_kwargs)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def new_world_recording(self, key: str) -> WorldRecording:
        if key in self.data_collections:
            raise ValueError(
                f"A recording is already registered for key '{key}'; "
                "use a distinct key per recording."
            )
        recording = WorldRecording()
        self.data_collections[key] = recording
        return recording

    def new_agents_recording(self, key: str) -> AgentsRecording:
        if key in self.data_agent_collections:
            raise ValueError(
                f"A recording is already registered for key '{key}'; "
                "use a distinct key per recording."
            )
        recording = AgentsRecording()
        self.data_agent_collections[key] = recording
        return recording

    def add_data_collector(
        self, collector: Callable[["SimulationWorld"], None]
    ) -> None:
        self._data_collectors.append(collector)

    def _do_recordings(self) -> None:
        for collector in self._data_collectors:
            collector(self)

    # ------------------------------------------------------------------
    # Simulation loop
    # ------------------------------------------------------------------

    def _initialize_if_needed(self) -> None:
        if not self._initialized:
            self._container.on_ready()
            self.environment.initialize(list(self.agents.values()), self.clock)
            self._initialized = True
            self._do_recordings()

    async def _wait_for_agents(self) -> None:
        await tasks_complete_or_sleeping(self._container)

    async def step(
        self,
        step_size_s: float = DISCRETE_EVENT,
        max_advance_time_s: float = -1.0,
    ) -> SimulationResult | None:
        """Advance the simulation by *step_size_s* seconds.

        When *step_size_s* is ``DISCRETE_EVENT`` (the default), the step size
        is determined automatically as the time until the next scheduled
        event (message arrival or agent task wakeup).

        :param step_size_s: step size in seconds, or ``DISCRETE_EVENT``
        :param max_advance_time_s: abort if the determined discrete step would
            exceed this value; ``-1`` means no limit
        :return: :class:`SimulationResult`, or ``None`` if there is nothing to do

        Example::

            result = await world.step(step_size_s=1.0)
            result = await world.step()  # discrete-event
        """
        self._initialize_if_needed()

        # Allow freshly scheduled tasks (e.g. from on_ready / on_start) to reach
        # their first sleeping or done point.  This is necessary so that periodic
        # tasks register their first wakeup with the ExternalClock before we
        # determine the discrete step size.
        await self._wait_for_agents()

        actual_step = step_size_s

        if actual_step == DISCRETE_EVENT:
            actual_step = self._container._determine_next_step_size()
            if actual_step is None:
                return None
            if max_advance_time_s >= 0 and actual_step > max_advance_time_s:
                return None

        new_time = self.clock.time + actual_step

        # ---- call on_step hooks ----
        for agent in list(self.agents.values()):
            agent.on_step(self.environment, self.clock, actual_step)
            if hasattr(agent, "roles"):
                for role in agent.roles:
                    role.on_step(self.environment, self.clock, actual_step)

        # ---- step environment ----
        self.environment.step(self.clock, actual_step)

        # ---- advance clock (wakes up sleeping tasks) ----
        self.clock.set_time(new_time)

        # ---- convergence loop: process messages and tasks ----
        total_delivered = 0
        state_changed = True
        while state_changed:
            # Wait for agents to settle
            await self._wait_for_agents()

            # Deliver messages whose delivery_time has now arrived
            delivered = await self._container._deliver_messages_due(new_time)
            total_delivered += delivered
            state_changed = delivered > 0

            # Yield control so delivered messages are processed
            if state_changed:
                await asyncio.sleep(0)

        self._do_recordings()

        return SimulationResult(
            time_elapsed_s=actual_step,
            step_size_s=actual_step,
            messages_delivered=total_delivered,
        )

    async def step_until(self, max_advance_time_s: float) -> list[SimulationResult]:
        """Run a discrete-event simulation until *max_advance_time_s* has elapsed.

        The simulation stops when the world clock has advanced by
        *max_advance_time_s* from its current position, or when there are no
        more events to process.

        :param max_advance_time_s: maximum total time to simulate in seconds
        :return: list of :class:`SimulationResult` from each step

        Example::

            results = await world.step_until(max_advance_time_s=3600.0)
        """
        self._initialize_if_needed()
        start_time = self.clock.time
        max_time = start_time + max_advance_time_s
        results: list[SimulationResult] = []

        while self.clock.time < max_time:
            remaining = max_time - self.clock.time
            result = await self.step(
                step_size_s=DISCRETE_EVENT,
                max_advance_time_s=remaining,
            )
            if result is None:
                break
            results.append(result)

        return results

    async def __aenter__(self) -> "SimulationWorld":
        self._initialize_if_needed()
        # Allow all freshly scheduled tasks (e.g. from on_ready) to start and
        # reach their first sleeping/done point before the caller proceeds.
        await self._wait_for_agents()
        return self

    async def __aexit__(self, *_) -> None:
        await self.shutdown()


def create_world(
    start_time: float = 0.0,
    communication_sim: CommunicationSimulation | None = None,
    environment: Environment | None = None,
) -> SimulationWorld:
    """Create a :class:`SimulationWorld`.

    :param start_time: initial simulation time in seconds (default 0)
    :param communication_sim: communication simulation to use; defaults to
        :class:`~mango.simulation.communication.SimpleCommunicationSimulation`
        with zero delay and no loss
    :param environment: environment to use; defaults to
        :class:`~mango.simulation.environment.DefaultEnvironment`
    :return: a ready-to-use :class:`SimulationWorld`

    Example::

        world = create_world(
            start_time=0.0,
            communication_sim=SimpleCommunicationSimulation(default_delay_s=0.1),
        )
    """
    clock = ExternalClock(start_time=start_time)
    sim = communication_sim or SimpleCommunicationSimulation()
    return SimulationWorld(clock=clock, communication_sim=sim, environment=environment)


async def step_simulation(
    world: SimulationWorld,
    step_size_s: float = DISCRETE_EVENT,
    max_advance_time_s: float = -1.0,
) -> SimulationResult | None:
    """Alias for :meth:`SimulationWorld.step` (which is preferred)."""
    return await world.step(
        step_size_s=step_size_s, max_advance_time_s=max_advance_time_s
    )


async def discrete_step_until(
    world: SimulationWorld,
    max_advance_time_s: float,
) -> list[SimulationResult]:
    """Alias for :meth:`SimulationWorld.step_until` (which is preferred)."""
    return await world.step_until(max_advance_time_s)
