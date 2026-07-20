"""
SimulationContainer – the container implementation backing
:class:`~mango.simulation.world.SimulationWorld`.

Implements the mango container contract for a clock-driven simulation:
``send_message`` queues messages with a delivery time computed by the
communication simulation; the world delivers them while stepping.
"""

import bisect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..agent.core import Agent, AgentAddress
from ..container.core import Container
from ..messages.codecs import JSON
from ..util.clock import ExternalClock
from .communication import CommunicationSimulation, MessagePackage

logger = logging.getLogger(__name__)

SIMULATION_ADDR = "simulation"


@dataclass
class MessageTransaction:
    """Records a message that was delivered during the simulation."""

    sender_id: str | None
    receiver_id: str
    sent_time: float
    arriving_time: float
    content: Any


class SimulationContainer(Container):
    """Local container for agents living in a :class:`SimulationWorld`.

    Unlike networked containers there is no explicit ``start()`` phase:
    the container is running from construction on, and messages are held
    in a pending queue until the world steps the clock past their
    delivery time.
    """

    def __init__(
        self,
        clock: ExternalClock,
        communication_sim: CommunicationSimulation,
        on_agent_registered: Callable[..., None] | None = None,
    ):
        super().__init__(
            addr=SIMULATION_ADDR,
            name=SIMULATION_ADDR,
            codec=JSON(),
            clock=clock,
        )
        self.communication_sim = communication_sim
        self._on_agent_registered = on_agent_registered
        self.running = True

        # Pending message queue: sorted list of (delivery_time, seq, sent_time, content, meta)
        # seq is a monotonically increasing tie-breaker so bisect.insort never
        # needs to compare content or meta (which may not be orderable).
        self._pending_messages: list[tuple[float, int, float, Any, dict]] = []
        self._msg_seq: int = 0

        self.recorded_messages: list[MessageTransaction] = []

    def on_register(self, agent: Agent, aid: str, **kwargs) -> None:
        if self._on_agent_registered is not None:
            self._on_agent_registered(agent, aid, **kwargs)

    def deregister(self, aid: str) -> None:
        self._agents.pop(aid, None)

    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: str | None = None,
        **kwargs,
    ) -> bool:
        """Send a message, applying communication simulation.

        Messages are queued with a delivery time determined by the
        communication simulation.  They are delivered during the next
        simulation step.
        """
        meta: dict[str, Any] = {
            "sender_id": sender_id,
            "sender_addr": self.addr,
            "receiver_id": receiver_addr.aid,
            "receiver_addr": self.addr,
            "network_protocol": "simulation",
        }
        meta.update(kwargs)

        sent_time = self.clock.time
        package = MessagePackage(
            sender_id=sender_id,
            receiver_id=receiver_addr.aid,
            sent_time=sent_time,
            content=(content, meta),
        )
        result = self.communication_sim.calculate_communication(
            current_time=sent_time,
            messages=[package],
        ).package_results[0]

        if not result.reached:
            logger.debug(
                "Message from %s to %s dropped (loss simulation)",
                sender_id,
                receiver_addr.aid,
            )
            return False

        delivery_time = sent_time + result.delay_s
        seq = self._msg_seq
        self._msg_seq += 1
        bisect.insort(
            self._pending_messages,
            (delivery_time, seq, sent_time, content, meta),
        )
        return True

    async def _deliver_messages_due(self, up_to_time: float) -> int:
        """Deliver all pending messages with delivery_time <= up_to_time.

        Returns the number of messages delivered.
        """
        delivered = 0
        remaining: list[tuple[float, int, float, Any, dict]] = []

        for delivery_time, seq, sent_time, content, meta in self._pending_messages:
            if delivery_time <= up_to_time:
                receiver_id = meta.get("receiver_id")
                agent = self._agents.get(receiver_id)
                if agent is not None:
                    await agent.inbox.put((0, content, meta))
                    self.recorded_messages.append(
                        MessageTransaction(
                            sender_id=meta.get("sender_id"),
                            receiver_id=receiver_id,
                            sent_time=sent_time,
                            arriving_time=delivery_time,
                            content=content,
                        )
                    )
                    delivered += 1
                else:
                    logger.warning(
                        "Unknown receiver '%s'; dropping message", receiver_id
                    )
            else:
                remaining.append((delivery_time, seq, sent_time, content, meta))

        self._pending_messages = remaining
        return delivered

    def _determine_next_step_size(self) -> float | None:
        """Return seconds to the next scheduled event, or None if none."""
        candidates: list[float] = []

        if self._pending_messages:
            next_msg_arrival = self._pending_messages[0][0]
            candidates.append(max(0.0, next_msg_arrival - self.clock.time))

        next_task = self.clock.get_next_activity()
        if next_task is not None:
            candidates.append(max(0.0, next_task - self.clock.time))

        if not candidates:
            return None
        return min(candidates)

    async def as_agent_process(self, agent_creator, mirror_container_creator=None):
        raise NotImplementedError(
            "Agent subprocesses are not supported in a simulation container"
        )

    def as_agent_process_lazy(self, agent_creator, mirror_container_creator=None):
        raise NotImplementedError(
            "Agent subprocesses are not supported in a simulation container"
        )

    async def shutdown(self) -> None:
        """Shut down all agents."""
        self.running = False
        for agent in list(self._agents.values()):
            try:
                await agent.shutdown()
            except Exception:
                logger.exception("Error shutting down agent '%s'", agent.aid)
