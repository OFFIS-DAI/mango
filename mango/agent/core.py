"""
This module implements the base class for agents (:class:`Agent`).

Every agent must live in a container. Containers are responsible for making
 connections to other agents.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..messages.message import AgentAddress
from ..util import tracing
from ..util.clock import Clock
from ..util.scheduling import (
    ScheduledProcessTask,
    ScheduledTask,
    Scheduler,
    sleeping_wait,
)
from ..util.seeding import name_key
from .conversation import CONVERSATION_ID_KEY, Conversation, _ConversationContext
from .decorators import apply_agent_periodic, apply_agent_subscriptions

if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy

    from mango.express.health import TopologyHealth

logger = logging.getLogger(__name__)

#: Prefix of the per-agent random stream names (``"agent/<aid>"``).
AGENT_STREAM_PREFIX = "agent"

_UNSET = object()


def _reserved_name_error(
    name: str, meaning: str, hint: str, owner: str = "mango.Agent"
) -> AttributeError:
    """The error for assigning one of mango.Agent's (or *owner*'s) reserved attribute names."""
    return AttributeError(f"{name!r} is reserved by {owner} ({meaning}); {hint}")


@dataclass
class AgentDescription:
    """Metadata describing an agent (name, category, color, unique ID)."""

    name: str = ""
    category: str = "agent"
    color: str = "gray"
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ForwardingRule:
    """Rule for automatic message forwarding.

    When the agent receives a message from *from_addr* it is automatically
    forwarded to *to_addr*.  If *forward_replies* is ``True``, replies from
    *to_addr* are forwarded back to *from_addr*.
    """

    from_addr: AgentAddress
    to_addr: AgentAddress
    forward_replies: bool = False


class State(Enum):
    NORMAL = 0
    INACTIVE = 1
    BROKEN = 2
    UNKNOWN = 3
    EXT_CONNECTION = 4


class TopologyNeighbor:
    """A neighbor in the topology graph.

    Addresses are resolved lazily so that topologies can be built before
    agents are registered in a container.

    :param agent: the neighbor agent (address resolved at access time)
    :param description: the agent's :class:`AgentDescription`
    :param characteristic: an optional label for the agent's role in its node
        (e.g. ``"leader"``); empty string means no characteristic
    """

    def __init__(
        self,
        agent: Any,
        description: AgentDescription,
        characteristic: str = "",
    ) -> None:
        self._agent = agent
        self.description = description
        self.characteristic = characteristic

    @property
    def address(self) -> AgentAddress:
        """The neighbor's current :class:`AgentAddress` (resolved lazily)."""
        return self._agent.addr


class TopologyService:
    """Stores neighborhood data injected by the topology system.

    An instance is attached to each agent via
    :meth:`~mango.agent.core.AgentDelegates.service_of_type`.
    """

    def __init__(self) -> None:
        self._tid_to_state_to_neighbors: dict[
            str, dict[State, list[TopologyNeighbor]]
        ] = {}
        self._tid_to_node_id: dict[str, int] = {}
        self._tid_to_characteristic: dict[str, str] = {}
        self._tid_to_connectors: dict[str, list[tuple[str, TopologyNeighbor]]] = {}
        self._marked_connector_for: list[str] = []
        # Optional :class:`TopologyHealth` instance per topology; see
        # :mod:`mango.express.health`.  Populated by ``Topology.inject``
        # for every topology that was configured with ``edge_health``.
        # The same instance is shared across every agent in the
        # topology (it stores scores for all directed edges).
        self._tid_to_health: dict[str, TopologyHealth] = {}

    def neighbors(
        self,
        state: State = State.NORMAL,
        *,
        tid: str = "default",
        has_characteristic: str | None = None,
        include_connectors: tuple[str, ...] | list[str] = (),
        match_func: Any = None,
    ) -> list[AgentAddress]:
        """Return addresses of neighbors in topology *tid* with edge *state*.

        :param state: only return neighbors reachable via edges in this state
        :param tid: topology identifier
        :param has_characteristic: if given, only neighbors with this characteristic
        :param include_connectors: also include connectors of these connection types
        :param match_func: optional predicate ``(AgentDescription) -> bool``
        """
        state_map = self._tid_to_state_to_neighbors.get(tid, {})
        result: list[AgentAddress] = []
        for n in state_map.get(state, []):
            if (
                has_characteristic is not None
                and n.characteristic != has_characteristic
            ):
                continue
            if match_func is not None and not match_func(n.description):
                continue
            result.append(n.address)
        for conn_type, n in self._tid_to_connectors.get(tid, []):
            if conn_type in include_connectors:
                if match_func is None or match_func(n.description):
                    result.append(n.address)
        return result

    def node_id(self, tid: str = "default") -> int:
        """Return the node ID this agent occupies in topology *tid*."""
        if tid not in self._tid_to_node_id:
            raise KeyError(f"No node ID registered for topology '{tid}'")
        return self._tid_to_node_id[tid]

    def characteristic(self, tid: str = "default") -> str:
        """Return this agent's characteristic label in topology *tid*."""
        return self._tid_to_characteristic.get(tid, "")

    def connectors(
        self,
        tid: str = "default",
        *,
        include_connectors: tuple[str, ...] | list[str] = (),
        match_func: Any = None,
    ) -> list[AgentAddress]:
        """Return addresses of connector agents for topology *tid*."""
        result: list[AgentAddress] = []
        for conn_type, n in self._tid_to_connectors.get(tid, []):
            if include_connectors and conn_type not in include_connectors:
                continue
            if match_func is not None and not match_func(n.description):
                continue
            result.append(n.address)
        return result

    def connection_types(self, tid: str = "default") -> list[str]:
        """Return the connection type labels for connectors in topology *tid*."""
        return [ct for ct, _ in self._tid_to_connectors.get(tid, [])]

    # ------------------------------------------------------------------
    # Edge-health queries (see mango.express.health)
    # ------------------------------------------------------------------
    def has_health(self, tid: str = "default") -> bool:
        """True when topology *tid* has continuous link-health enabled."""
        return tid in self._tid_to_health

    def health_runtime(self, tid: str = "default") -> TopologyHealth | None:
        """Return the :class:`TopologyHealth` runtime for *tid*, or None."""
        return self._tid_to_health.get(tid)


def _addr_from_meta(meta: dict) -> AgentAddress:
    """Build an :class:`AgentAddress` from a received ``meta``.

    The JSON codec decodes a ``(host, port)`` protocol address back into a
    *list*, which is unhashable and never compares equal to the tuple form
    kept internally.  Normalise it to a tuple so the result is usable as a
    dict key and equality-comparable against topology neighbour addresses.
    """
    protocol_addr = meta.get("sender_addr")
    if isinstance(protocol_addr, list):
        protocol_addr = tuple(protocol_addr)
    return AgentAddress(protocol_addr=protocol_addr, aid=meta.get("sender_id"))


async def _race_first_completed(*awaitables) -> None:
    """Run *awaitables* concurrently; return when the first finishes,
    cancelling the rest.  Used by :meth:`AgentDelegates.gather` to race
    the collector's "we're done" signal against a clock-aware timer.
    """
    tasks = [asyncio.ensure_future(a) for a in awaitables]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


class _GatherCollector:
    """Aggregates multi-reply tracked responses for :meth:`AgentDelegates.open_gather`.

    Replies are stored under the responding agent's
    :class:`AgentAddress` so the caller can match each response to its
    source.  :meth:`wait` resolves once *expected* distinct replies
    have arrived, or when :meth:`finish` is called explicitly (which
    :meth:`AgentDelegates.gather` uses to honour the timeout / quorum
    policy).
    """

    def __init__(
        self,
        *,
        expected: int,
        reply_type: type | tuple[type, ...] | None = None,
    ) -> None:
        self._expected = max(0, int(expected))
        self._reply_type = reply_type
        self.responses: dict[AgentAddress, Any] = {}
        self._done = asyncio.Event()

    def on_reply(self, content: Any, meta: dict) -> None:
        if self._reply_type is not None and not isinstance(content, self._reply_type):
            return
        addr = _addr_from_meta(meta)
        # First reply per sender wins: late duplicates (e.g. retries)
        # are dropped so the caller sees a stable mapping.
        if addr in self.responses:
            return
        self.responses[addr] = content
        if self._expected and len(self.responses) >= self._expected:
            self._done.set()

    def finish(self) -> None:
        """Force the collector to resolve (used on timeout / quorum hit)."""
        self._done.set()

    async def wait(self) -> None:
        await self._done.wait()


class AgentContext:
    def __init__(self, container) -> None:
        self._container = container
        self._services = {}

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self._container.clock.time

    @property
    def clock(self) -> Clock:
        return self._container.clock

    @property
    def addr(self):
        return self._container.addr

    def register(self, agent, suggested_aid):
        return self._container.register(agent, suggested_aid=suggested_aid)

    def rng(self, *names: str | int) -> numpy.random.Generator:
        """Return the container's reproducible random stream *names*.

        See :meth:`mango.container.core.Container.rng`.

        :param names: ``/``-separated stream name, or several segments
        :return: the cached stream

        Example::

            market_rng = agent.context.rng("market")
        """
        return self._container.rng(*names)

    def deregister(self, aid):
        if self._container.running:
            self._container.deregister(aid)

    def service_of_type(self, type: type, default: Any = None) -> Any:
        if type not in self._services:
            self._services[type] = type() if default is None else default
        return self._services[type]

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ) -> bool:
        """
        See container.send_message(...)
        """
        tracing._message_sent(
            content, sender_id, receiver_addr, kwargs, self._container.clock
        )
        return await self._container.send_message(
            content, receiver_addr=receiver_addr, sender_id=sender_id, **kwargs
        )


def _check_receiver_addr(receiver_addr: Any) -> None:
    """Fail in the caller's frame when *receiver_addr* is no address.

    Sends often run later (an instant task), where a wrong receiver would
    fail far away from the user code that passed it.
    """
    if hasattr(receiver_addr, "aid") and hasattr(receiver_addr, "protocol_addr"):
        return
    hint = (
        "; for an agent id use its address (e.g. agent.addr or "
        "AgentAddress(protocol_addr, aid)), and reply with sender_addr(meta)"
        if isinstance(receiver_addr, str)
        else ""
    )
    raise TypeError(
        f"receiver_addr must be an AgentAddress, got {type(receiver_addr).__name__} "
        f"{receiver_addr!r}{hint}."
    )


class AgentDelegates:
    def __init__(self) -> None:
        self.context: AgentContext = None
        self.scheduler: Scheduler = None
        self._aid = None
        self._description: AgentDescription = AgentDescription()
        self._forwarding_rules: list[ForwardingRule] = []
        self._transaction_handlers: dict[str, tuple] = {}
        # Multi-shot reply collectors keyed by tracking_id.  Used by
        # :meth:`gather` (and any caller of :meth:`open_gather`) to
        # aggregate replies from many receivers under a single id
        # without consuming the entry on the first reply.  See
        # :meth:`_handle_tracked_reply`.
        self._gather_collectors: dict[str, _GatherCollector] = {}
        # Open conversation handles keyed by conversation_id (see
        # :mod:`mango.agent.conversation`).  Several handles may be open
        # for the same id (e.g. an ``@on_message`` handler that re-fires
        # while an earlier join is still open); every matching inbound
        # message is fanned out to all of them.
        self._conversations: dict[str, list[Conversation]] = {}
        self._behavior_message_subs: list[tuple] = []
        self._behavior_global_event_handlers: list[tuple] = []
        self._behavior_agent_event_handlers: list[tuple] = []

    def on_start(self):
        """Called when container started in which the agent is contained"""

    def on_ready(self):
        """Called when all container has been started using activate(...)."""

    def on_step(self, env, clock, step_size_s: float) -> None:
        """Called on every simulation step (only in SimulationWorld).

        :param env: the simulation environment
        :param clock: the current simulation clock
        :param step_size_s: seconds advanced in this step
        """

    def on_global_event(self, event: Any) -> None:
        """Called when a global event is emitted from the environment.

        Override to react to environment-wide broadcasts.

        :param event: the event object
        """

    def on_agent_event(self, event: Any) -> None:
        """Called when a targeted agent event is emitted.

        Override to react to events directed at this specific agent.

        :param event: the event object
        """

    # ------------------------------------------------------------------
    # Reproducible randomness
    # ------------------------------------------------------------------

    def _agent_stream(
        self, operation: str, suffix: str | None
    ) -> numpy.random.Generator:
        agent = self._bound_agent(operation)
        context = agent.context
        if agent.aid is None or context is None or context._container is None:
            raise RuntimeError(
                f"{operation} is not available before the agent is registered: "
                "the agent's random stream is keyed by its aid, which exists "
                "after registration (e.g. in on_register, on_start or on_ready)."
            )
        # the aid is one key segment, never split on "/": aids "g" and "g/x"
        # must not share streams ("agent/g/x" would be g's sub-stream "x")
        segments: tuple[str | int, ...] = (AGENT_STREAM_PREFIX, name_key(agent.aid))
        if suffix is not None:
            segments += (suffix,)
        return context.rng(*segments)

    @property
    def rng(self) -> numpy.random.Generator:
        """The agent's reproducible random stream (a ``numpy.random.Generator``).

        The stream is keyed by ``"agent"`` and the aid in the container's
        :class:`~mango.util.seeding.SeedTree`, so it depends only on the root
        seed and the aid: adding or removing other agents never changes it.
        For an aid without ``/`` it is ``world.rng(f"agent/{aid}")``; in
        general (the aid is always a single segment) it is
        ``world.rng("agent", name_key(aid))``. The same object is returned on
        every access. Roles share their agent's stream.

        Agents registered with generated aids (``agent0``, ``agent1``, ...)
        get streams that depend on registration order; pass ``suggested_aid``
        for streams that stay stable when the scenario changes.

        Assigning ``self.rng = value`` replaces the attribute for this object,
        like a plain attribute (``None`` included, so the lazy pattern
        ``self.rng = None`` ... ``if self.rng is None:`` keeps working);
        ``del self.rng`` returns to the seeded stream.

        :raises RuntimeError: before the agent is registered (unless a value
            was assigned)

        Example::

            class EV(Agent):
                def on_ready(self):
                    self.arrival_h = self.rng.normal(17.5, 1.0)
        """
        override = self.__dict__.get("_rng_override", _UNSET)
        if override is not _UNSET:
            return override
        agent = self._bound_agent("rng")
        if agent is not self:
            return agent.rng
        # Hot path (agents draw from self.rng every step): the stream is a
        # pure function of the container and the aid, so reuse it while both
        # are unchanged instead of re-deriving the stream key on every access.
        context = self.context
        container = None if context is None else context._container
        cached = self.__dict__.get("_rng_cache")
        if (
            cached is not None
            and cached[0] is container
            and cached[1] == self.aid
            and container is not None
        ):
            return cached[2]
        stream = self._agent_stream("rng", None)
        self._rng_cache = (container, self.aid, stream)
        return stream

    @rng.setter
    def rng(self, value: Any) -> None:
        self._rng_override = value

    @rng.deleter
    def rng(self) -> None:
        self.__dict__.pop("_rng_override", None)

    def rng_for(self, name: str) -> numpy.random.Generator:
        """Return an independent named sub-stream of the agent's stream.

        The stream is the agent's stream key plus *name* (for an aid without
        ``/``: ``"agent/<aid>/<name>"``); use it to keep separate concerns
        (e.g. arrivals and demand) from shifting each other's draws.

        :param name: ``/``-separated sub-stream name
        :return: the cached ``numpy.random.Generator`` of that sub-stream
        :raises RuntimeError: before the agent is registered

        Example::

            demand_kwh = self.rng_for("demand").uniform(5.0, 40.0)
        """
        return self._agent_stream("rng_for", name)

    # ------------------------------------------------------------------
    # Agent description helpers
    # ------------------------------------------------------------------

    @property
    def description(self) -> AgentDescription:
        """Return the agent's :class:`AgentDescription` (reserved name)."""
        return self._description

    @description.setter
    def description(self, value: Any) -> None:
        raise _reserved_name_error(
            "description",
            "the agent's name, color and category",
            "use self.update_description(...) to change it, or choose another "
            "attribute name, e.g. self.summary",
        )

    @property
    def name(self) -> str:
        """Human-readable name of this agent (read-only, reserved name).

        Change it with :meth:`update_description`; assigning ``self.name``
        raises an ``AttributeError`` that says so.
        """
        return self._description.name

    @name.setter
    def name(self, value: Any) -> None:
        raise _reserved_name_error(
            "name",
            "the agent's display name",
            "use self.update_description(name=...) to change it, or choose "
            "another attribute name, e.g. self.label",
        )

    @property
    def color(self) -> str:
        """Visual color tag for this agent (read-only, reserved name).

        Change it with :meth:`update_description`.
        """
        return self._description.color

    @color.setter
    def color(self, value: Any) -> None:
        raise _reserved_name_error(
            "color",
            "the agent's visual tag",
            "use self.update_description(color=...) to change it, or choose "
            "another attribute name, e.g. self.team",
        )

    @property
    def category(self) -> str:
        """Category tag for this agent (read-only, reserved name).

        Change it with :meth:`update_description`.
        """
        return self._description.category

    @category.setter
    def category(self, value: Any) -> None:
        raise _reserved_name_error(
            "category",
            "the agent's category tag",
            "use self.update_description(category=...) to change it, or choose "
            "another attribute name, e.g. self.kind",
        )

    @property
    def uid(self) -> str:
        """Unique identifier (UUID string) of this agent (read-only, reserved name)."""
        return self._description.uid

    @uid.setter
    def uid(self, value: Any) -> None:
        raise _reserved_name_error(
            "uid",
            "the agent's read-only unique id",
            "choose another attribute name, e.g. self.person_id",
        )

    def update_description(
        self,
        name: str | None = None,
        color: str | None = None,
        category: str | None = None,
    ) -> None:
        """Update one or more description fields.

        :param name: new human-readable name
        :param color: new color tag
        :param category: new category tag
        """
        if name is not None:
            self._description.name = name
        if color is not None:
            self._description.color = color
        if category is not None:
            self._description.category = category

    # ------------------------------------------------------------------
    # Forwarding rules
    # ------------------------------------------------------------------

    def add_forwarding_rule(
        self,
        from_addr: AgentAddress,
        to_addr: AgentAddress,
        forward_replies: bool = False,
    ) -> None:
        """Add an automatic message-forwarding rule.

        After calling this, every message received from *from_addr* is
        automatically forwarded to *to_addr*.  When *forward_replies* is
        ``True``, replies originating from *to_addr* are forwarded back to
        *from_addr*.

        :param from_addr: source address to match
        :param to_addr: destination to forward to
        :param forward_replies: whether replies should be forwarded back
        """
        self._forwarding_rules.append(
            ForwardingRule(
                from_addr=from_addr, to_addr=to_addr, forward_replies=forward_replies
            )
        )

    def delete_forwarding_rule(
        self,
        from_addr: AgentAddress,
        to_addr: AgentAddress | None = None,
    ) -> None:
        """Remove previously added forwarding rule(s).

        :param from_addr: source address of the rule to remove
        :param to_addr: if given, only remove rules that also match this
            destination; otherwise remove all rules matching *from_addr*
        """
        self._forwarding_rules = [
            r
            for r in self._forwarding_rules
            if not (
                r.from_addr == from_addr and (to_addr is None or r.to_addr == to_addr)
            )
        ]

    def _bound_agent(self, operation: str) -> Agent:
        """Return the agent whose registries and scheduler serve *operation*.

        Identity on an agent itself; delegating contexts (e.g.
        ``RoleContext``) override this to return their owning agent, because
        message routing consults the *agent's* registries, so tracked
        handlers, gather collectors and conversations must be
        registered there, not on the delegate.
        """
        return self

    # ------------------------------------------------------------------
    # Tracked / reply-to messaging
    # ------------------------------------------------------------------

    async def send_tracked_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        response_handler=None,
        **kwargs,
    ):
        """Send a message and optionally register a response handler.

        A ``tracking_id`` is attached to the message so that the reply can
        be matched.  When *response_handler* is provided it will be called as
        ``response_handler(reply_content, reply_meta)`` when the matching
        reply arrives.

        :param content: message content
        :param receiver_addr: target agent address
        :param response_handler: optional ``(content, meta) -> None`` callback
        :return: the asyncio.Task for the sent message
        """
        tracking_id = str(uuid.uuid4())
        if response_handler is not None:
            agent = self._bound_agent("send_tracked_message")
            agent._transaction_handlers[tracking_id] = (response_handler,)
        return await self.send_message(
            content,
            receiver_addr=receiver_addr,
            tracking_id=tracking_id,
            **kwargs,
        )

    async def reply_to(
        self,
        content: Any,
        received_meta: dict,
        **kwargs,
    ) -> bool:
        """Convenience helper to reply to a received message.

        Extracts the sender address from *received_meta* and sends *content*
        back, preserving any ``tracking_id`` for transaction matching and any
        ``conversation_id`` so the reply routes into an open conversation.

        :param content: reply content
        :param received_meta: the ``meta`` dict from the received message
        :return: result of :meth:`send_message`
        """
        sender_id = received_meta.get("sender_id")
        sender_addr = received_meta.get("sender_addr")
        tracking_id = received_meta.get("tracking_id")
        conversation_id = received_meta.get(CONVERSATION_ID_KEY)
        reply_addr = AgentAddress(protocol_addr=sender_addr, aid=sender_id)
        extra: dict = {"reply": True}
        if tracking_id:
            extra["tracking_id"] = tracking_id
        if conversation_id:
            extra[CONVERSATION_ID_KEY] = conversation_id
        extra.update(kwargs)
        return await self.send_message(content, receiver_addr=reply_addr, **extra)

    def _handle_tracked_reply(self, content: Any, meta: dict) -> bool:
        """Internal: check if *meta* contains a tracked reply; call handler.

        Returns ``True`` if the message was handled as a tracked reply.
        Two flavours are supported:

        * Single-shot ``_transaction_handlers`` entries (created by
          :meth:`send_tracked_message`).  Popped on first matching
          reply.
        * Multi-shot ``_gather_collectors`` entries (created by
          :meth:`open_gather`).  Not popped: every matching reply
          is delivered to the collector until the caller closes it.
        """
        tracking_id = meta.get("tracking_id")
        if not tracking_id or not meta.get("reply"):
            return False
        if tracking_id in self._transaction_handlers:
            (handler,) = self._transaction_handlers.pop(tracking_id)
            handler(content, meta)
            return True
        collector = self._gather_collectors.get(tracking_id)
        if collector is not None:
            collector.on_reply(content, meta)
            return True
        return False

    def _route_to_conversation(self, content: Any, meta: dict) -> None:
        """Deliver *content*/*meta* to every open conversation handle whose
        id matches ``meta[CONVERSATION_ID_KEY]``.  No-op when the message
        has no conversation id or no matching handle exists.
        """
        conv_id = meta.get(CONVERSATION_ID_KEY)
        if not conv_id:
            return
        for conv in list(self._conversations.get(conv_id, ())):
            conv._on_inbound(content, meta)

    def _register_conversation(self, conv: Conversation) -> None:
        """Register a conversation handle for inbound routing.

        Called by the conversation context manager on entry; several
        handles may share one id, and messages fan out to all of them.
        """
        self._conversations.setdefault(conv.conversation_id, []).append(conv)

    def _unregister_conversation(self, conv: Conversation) -> None:
        """Remove one previously registered handle.  Idempotent."""
        handles = self._conversations.get(conv.conversation_id)
        if handles is None:
            return
        try:
            handles.remove(conv)
        except ValueError:
            pass
        if not handles:
            self._conversations.pop(conv.conversation_id, None)

    def _nudge_topology_health(self, meta: dict) -> None:
        """Multiplicatively recover edge scores on every received message.

        Called once per inbox dequeue (before role-level dispatch).  No-op
        unless this agent participates in at least one topology that
        was created with ``edge_health=...``.  All clock reads go
        through the agent's scheduler so the decay model is identical
        under real-time and simulation clocks.
        """
        svc = self.service_of_type(TopologyService, None)
        if svc is None or not svc._tid_to_health:
            return
        sender_id = meta.get("sender_id")
        if not sender_id:
            return
        peer_addr = _addr_from_meta(meta)
        scheduler = getattr(self, "scheduler", None)
        if scheduler is None or scheduler.clock is None:
            return
        now = scheduler.clock.time
        my_addr = self.addr
        for tid, health in svc._tid_to_health.items():
            # Only nudge if the sender is actually a neighbour of this
            # agent in that topology.  This keeps unrelated traffic
            # (cross-topology messages, broadcast lists) from inflating
            # scores for non-neighbours.
            if any(
                n.address == peer_addr
                for ns in svc._tid_to_state_to_neighbors.get(tid, {}).values()
                for n in ns
            ):
                health.nudge(my_addr, peer_addr, now)

    def open_gather(
        self,
        tracking_id: str,
        *,
        expected: int,
        reply_type: type | tuple[type, ...] | None = None,
    ) -> _GatherCollector:
        """Register a multi-reply collector for *tracking_id*.

        Returns an opaque collector handle; the caller awaits
        :meth:`_GatherCollector.wait` and must call
        :meth:`close_gather` (or use :meth:`gather`, which
        does both) to release the registration.

        :param expected: number of replies that satisfies the collector
            without waiting for the timeout.  Used by ``gather`` to
            return as soon as every recipient has answered.
        :param reply_type: when set, only replies whose ``content`` is
            an instance of *reply_type* are accepted; everything else
            is dropped silently.  Filters out tracking-id collisions
            with unrelated reply traffic.
        """
        collectors = self._bound_agent("open_gather")._gather_collectors
        if tracking_id in collectors:
            raise ValueError(
                f"gather collector already open for tracking_id={tracking_id!r}"
            )
        collector = _GatherCollector(expected=expected, reply_type=reply_type)
        collectors[tracking_id] = collector
        return collector

    def close_gather(self, tracking_id: str) -> None:
        """Release a previously opened gather collector."""
        self._bound_agent("close_gather")._gather_collectors.pop(tracking_id, None)

    async def gather(
        self,
        content: Any,
        receivers: Iterable[AgentAddress],
        *,
        reply_type: type | tuple[type, ...] | None = None,
        timeout: float = 5.0,
        min_fraction: float = 1.0,
    ) -> dict[AgentAddress, Any]:
        """Send *content* to every address in *receivers* and collect replies.

        Returns a dict mapping each responding agent's
        :class:`AgentAddress` to its reply content.  Responders are
        expected to use :meth:`reply_to` (or otherwise echo
        ``tracking_id`` with ``reply=True``); every existing
        request/response pair in mango follows that convention, so
        responders work unchanged.

        :param receivers: iterable of :class:`AgentAddress` targets.
        :param reply_type: optional class/tuple.  Replies of any other
            type are silently dropped, filtering out tracking-id
            collisions with unrelated traffic.
        :param timeout: cap on how long to wait, measured on the
            agent's scheduler clock.  When it elapses, returns
            whatever replies have arrived so far.
        :param min_fraction: between 0 and 1.  Returns as soon as
            ``ceil(min_fraction * len(receivers))`` replies have
            arrived.  Defaults to 1.0: wait for all, time out
            otherwise.
        """
        agent = self._bound_agent("gather")
        receivers = list(receivers)
        if not receivers:
            return {}
        expected = max(1, math.ceil(min_fraction * len(receivers)))

        tracking_id = str(uuid.uuid4())
        collector = agent.open_gather(
            tracking_id, expected=expected, reply_type=reply_type
        )
        try:
            for addr in receivers:
                await self.send_message(
                    content, receiver_addr=addr, tracking_id=tracking_id
                )
            # Timeout follows the agent's scheduler clock so behaviour
            # is identical under AsyncioClock (real time) and
            # ExternalClock (simulation).  asyncio.wait_for would block
            # real seconds in sim mode.
            # Both racers resolve externally (reply message / clock step),
            # so the wait counts as sleeping for stepped simulations.
            with sleeping_wait():
                await _race_first_completed(
                    collector.wait(),
                    agent.scheduler.clock.sleep(timeout),
                )
        finally:
            agent.close_gather(tracking_id)
        return dict(collector.responses)

    def open_conversation(
        self,
        *,
        state: dict | None = None,
        timeout: float | None = None,
    ) -> _ConversationContext:
        """Open a fresh multi-hop conversation as the initiator.

        Returns an async context manager whose body yields a
        :class:`~mango.agent.conversation.Conversation`.  A new id is
        generated; other agents reply by echoing it (every call to
        :meth:`Conversation.send` and every :meth:`reply_to` carries it
        automatically).

        The optional *timeout* is enforced via the agent's scheduler
        clock so behaviour is identical under :class:`AsyncioClock`
        (real time) and :class:`ExternalClock` (simulation).  Unlike
        :meth:`gather`, it defaults to ``None`` because conversations are
        long-lived by design, so no arbitrary deadline is imposed.
        """
        return self._make_conversation(
            conversation_id=str(uuid.uuid4()), state=state, timeout=timeout
        )

    def join_conversation(
        self,
        meta: dict,
        *,
        state: dict | None = None,
        timeout: float | None = None,
    ) -> _ConversationContext:
        """Join an existing conversation as a non-initiator.

        Reads the id from *meta* (the dict passed to message handlers)
        and registers a local handle so subsequent messages tagged with
        that id route here too.  Typical use: a gossip forwarder
        processes a multi-message exchange from the responder side.

        Every join owns an independent handle with its own *state* and
        *timeout*; when several handles for the same id are open on one
        agent, each inbound message is delivered to all of them.
        """
        conv_id = meta.get(CONVERSATION_ID_KEY)
        if not conv_id:
            raise ValueError(
                "join_conversation requires a meta dict carrying "
                f"{CONVERSATION_ID_KEY!r}"
            )
        return self._make_conversation(
            conversation_id=conv_id, state=state, timeout=timeout
        )

    def _make_conversation(
        self,
        *,
        conversation_id: str,
        state: dict | None,
        timeout: float | None,
    ) -> _ConversationContext:
        return _ConversationContext(
            owner=self,
            conv=Conversation(
                owner=self,
                conversation_id=conversation_id,
                state=state,
                timeout=timeout,
            ),
        )

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self.context.current_timestamp

    @property
    def aid(self):
        """The agent id, assigned at registration (read-only, reserved name)."""
        return self._aid

    @aid.setter
    def aid(self, value: Any) -> None:
        raise _reserved_name_error(
            "aid",
            "the agent id, assigned by the container at registration",
            "pass suggested_aid=... to register() to choose it, or choose "
            "another attribute name, e.g. self.index",
        )

    @property
    def addr(self):
        """Return the address of the agent as AgentAddress

        Returns:
            _type_: AgentAddress
        """
        if self.context is None or self.context._container is None:
            return None
        return AgentAddress(self.context.addr, self.aid)

    @addr.setter
    def addr(self, value: Any) -> None:
        raise _reserved_name_error(
            "addr",
            "the agent's address, derived from its container and aid",
            "choose another attribute name, e.g. self.home_addr",
        )

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ) -> bool:
        """
        See container.send_message(...)
        """
        _check_receiver_addr(receiver_addr)
        return await self.context.send_message(
            content, receiver_addr=receiver_addr, sender_id=self.aid, **kwargs
        )

    async def send_messages(
        self,
        content,
        receiver_addrs: list[AgentAddress],
        **kwargs,
    ) -> list[bool]:
        """Send the same content to multiple recipients.

        :param content: message content (sent to every recipient)
        :param receiver_addrs: list of target :class:`AgentAddress` instances
        :return: list of send results (one per recipient, in order)
        """
        results = []
        for addr in receiver_addrs:
            results.append(
                await self.send_message(content, receiver_addr=addr, **kwargs)
            )
        return results

    def schedule_instant_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ):
        """Send a message without waiting for it: the send runs in the next loop turn.

        Equivalent to ``self.schedule_instant_task(self.send_message(...))``,
        and the way to send from synchronous code such as ``on_step`` or
        ``handle_message``. In a :class:`~mango.simulation.SimulationWorld`
        (whose sending never suspends) no task is created: one event-loop
        callback sends the message at the same moment and in the same order,
        which is several times cheaper per message.

        :param content: The content of the message
        :param receiver_addr: The address passed to the container
        :param kwargs: Additional parameters to provide protocol specific settings
        :returns: an awaitable resolved with the result of ``send_message``
            (an ``asyncio.Task``, or an ``asyncio.Future`` on the fast path)

        Example::

            def on_step(self, env, clock, step_size_s):
                for peer in self.peers:
                    self.schedule_instant_message({"price": self.price}, peer)
        """
        _check_receiver_addr(receiver_addr)
        context = self.context
        send_soon = getattr(getattr(context, "_container", None), "_send_soon", None)
        if (
            send_soon is not None
            and type(self).send_message is type(self)._plain_send_message
            and "send_message" not in self.__dict__
            and type(context).send_message is AgentContext.send_message
            and not tracing.is_tracing()
        ):
            return send_soon(
                self._send_now, content, receiver_addr, kwargs, self.scheduler
            )
        return self.schedule_instant_task(
            self.send_message(content, receiver_addr=receiver_addr, **kwargs)
        )

    def _send_now(self, content, receiver_addr: AgentAddress, kwargs: dict) -> bool:
        """What :meth:`send_message` does, synchronously (fast path of
        :meth:`schedule_instant_message`; tracing takes the regular path)."""
        return self.context._container._enqueue_message(
            content, receiver_addr, self.aid, kwargs
        )

    def schedule_conditional_process_task(
        self,
        coroutine_creator,
        condition_func,
        lookup_delay=0.1,
        on_stop=None,
        src=None,
    ):
        """Schedule a process task when a specified condition is met.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param condition_func: function for determining whether the confition is fullfiled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_conditional_process_task(
            coroutine_creator=coroutine_creator,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_conditional_task(
        self, coroutine, condition_func, lookup_delay=0.1, on_stop=None, src=None
    ):
        """Schedule a task when a specified condition is met.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the confition is fullfiled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_conditional_task(
            coroutine=coroutine,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_timestamp_task(
        self, coroutine, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified  unix timestamp.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param timestamp: timestamp defining when the task should start
        :type timestamp: timestamp
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_timestamp_task(
            coroutine=coroutine, timestamp=timestamp, on_stop=on_stop, src=src
        )

    def schedule_timestamp_process_task(
        self, coroutine_creator, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified unix timestamp dispatched to another process.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param timestamp: unix timestamp defining when the task should start
        :type timestamp: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_timestamp_process_task(
            coroutine_creator=coroutine_creator,
            timestamp=timestamp,
            on_stop=on_stop,
            src=src,
        )

    def schedule_periodic_process_task(
        self, coroutine_creator, delay, on_stop=None, src=None
    ):
        """Schedule an open end periodically executed task in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_periodic_process_task(
            coroutine_creator=coroutine_creator, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_periodic_task(self, coroutine_func, delay, on_stop=None, src=None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_periodic_task(
            coroutine_func=coroutine_func, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_recurrent_process_task(
        self, coroutine_creator, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_recurrent_process_task(
            coroutine_creator=coroutine_creator,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_recurrent_task(
        self, coroutine_func, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_recurrent_task(
            coroutine_func=coroutine_func,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_instant_process_task(self, coroutine_creator, on_stop=None, src=None):
        """Schedule an instantly executed task in another processes.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator:
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_instant_process_task(
            coroutine_creator=coroutine_creator, on_stop=on_stop, src=src
        )

    def schedule_instant_task(self, coroutine, on_stop=None, src=None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine:
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_instant_task(
            coroutine=coroutine, on_stop=on_stop, src=src
        )

    def schedule_process_task(self, task: ScheduledProcessTask, src=None):
        """Schedule a task with asyncio in another process. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledScheduledProcessTaskTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self.scheduler.schedule_process_task(task, src=src)

    def schedule_task(self, task: ScheduledTask, src=None):
        """Schedule a task with asyncio. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self.scheduler.schedule_task(task, src=src)

    async def tasks_complete(self, timeout=1):
        """Wait for all scheduled tasks to complete using a timeout.

        :param timeout: waiting timeout. Defaults to 1.
        """
        await self.scheduler.tasks_complete(timeout=timeout)

    def service_of_type(self, type: type, default: Any = None) -> Any:
        """Return the service registered for ``type``, creating it if absent.

        If no service of ``type`` is registered yet, *default* is registered
        and returned; when *default* is ``None`` a new ``type()`` instance is
        created instead.

        :param type: the type of the service
        :type type: type
        :param default: the value to register if none exists; ``None`` creates
            a ``type()`` instance
        :type default: Any (optional)
        :return: the service
        :rtype: Any
        """
        return self.context.service_of_type(type, default=default)

    def neighbors(
        self,
        state: State = State.NORMAL,
        *,
        tid: str = "default",
        has_characteristic: str | None = None,
        include_connectors: tuple | list = (),
        match_func=None,
    ) -> list[AgentAddress]:
        """Return neighbor addresses from the topology.

        :param state: filter by edge state (default :attr:`~mango.State.NORMAL`)
        :param tid: topology identifier (default ``"default"``)
        :param has_characteristic: only include neighbors with this characteristic
        :param include_connectors: also include connector agents of these types
        :param match_func: optional ``(AgentDescription) -> bool`` predicate
        :return: list of :class:`~mango.AgentAddress`
        """
        svc = self.service_of_type(TopologyService)
        return svc.neighbors(
            state,
            tid=tid,
            has_characteristic=has_characteristic,
            include_connectors=include_connectors,
            match_func=match_func,
        )


#: The ``send_message`` whose effect ``_send_now`` reproduces; a subclass that
#: overrides ``send_message`` keeps its override (no fast path).
AgentDelegates._plain_send_message = AgentDelegates.send_message


class AgentInbox(asyncio.Queue):
    """The agent's message queue: an ``asyncio.Queue`` that reports new messages.

    ``on_put`` (a callable without arguments, or ``None``) is called after
    every message is added; containers use it to know which agents have work
    without looking at every agent (see :mod:`mango.util.termination_detection`).

    Example::

        inbox = AgentInbox()
        inbox.on_put = lambda: print("message")
        inbox.put_nowait((0, "hello", {}))   # prints "message"
    """

    def __init__(self) -> None:
        super().__init__()
        self.on_put: Any = None

    def put_nowait(self, item: Any) -> None:
        super().put_nowait(item)
        if self.on_put is not None:
            self.on_put()


class Agent(ABC, AgentDelegates):
    """Base class for all agents.

    Subclasses must call ``super().__init__()`` first in their ``__init__``.

    **Names reserved by mango.Agent.** These attributes belong to the
    framework; assigning them raises an ``AttributeError`` that names the
    fix, so pick another name for your own state:

    ``aid``, ``addr``
        the agent id and address, assigned at registration
    ``name``, ``color``, ``category``, ``uid``, ``description``
        the agent's description; change it with :meth:`update_description`
    ``inbox``, ``context``, ``scheduler``
        the message queue, the container context and the task scheduler
    ``rng``
        the agent's seeded random stream (assignable, see :attr:`rng`)

    Methods such as ``send_message``, ``schedule_*_task``, ``neighbors``,
    ``on_step`` and ``handle_message`` are part of the API as well.

    Example::

        class Resident(Agent):
            def __init__(self, team):
                super().__init__()
                self.team = team  # not self.color: that name is reserved

            def on_register(self):
                self.update_description(color=self.team)
    """

    #: Whether this agent owns the co-located event bus ``@on_event``
    #: subscribes to.  Only :class:`~mango.RoleAgent` does.
    _has_event_bus = False

    # The message queue, container context and scheduler live in private
    # slots behind properties so that a subclass assigning e.g.
    # ``self.inbox = []`` gets a clear error instead of a crash much later in
    # mango's message loop. Values of the right type (or None) are accepted,
    # so mango itself (and tests) can still swap them.

    @property
    def inbox(self) -> asyncio.Queue:
        """The agent's message queue (reserved name)."""
        return self._mango_inbox

    @inbox.setter
    def inbox(self, value: Any) -> None:
        if not isinstance(value, asyncio.Queue):
            raise _reserved_name_error(
                "inbox",
                "the agent's incoming message queue",
                "choose another attribute name, e.g. self.received",
            )
        self._mango_inbox = value
        self._mango_activity_changed()

    @property
    def context(self) -> AgentContext:
        """The agent's container context (reserved name)."""
        return self._mango_context

    @context.setter
    def context(self, value: Any) -> None:
        if value is not None and not isinstance(value, AgentContext):
            raise _reserved_name_error(
                "context",
                "the agent's container context",
                "choose another attribute name, e.g. self.situation",
            )
        self._mango_context = value

    @property
    def scheduler(self) -> Scheduler:
        """The agent's task scheduler, created at registration (reserved name)."""
        return self._mango_scheduler

    @scheduler.setter
    def scheduler(self, value: Any) -> None:
        if value is not None and not isinstance(value, Scheduler):
            raise _reserved_name_error(
                "scheduler",
                "the agent's task scheduler",
                "choose another attribute name, e.g. self.planner",
            )
        self._mango_scheduler = value
        self._mango_activity_changed()

    #: The container's activity tracker this agent reports to (set at
    #: registration; see :mod:`mango.util.termination_detection`).
    _mango_activity: Any = None

    def _mango_activity_changed(self) -> None:
        """Re-attach a swapped inbox or scheduler to the container's activity tracker."""
        activity = self.__dict__.get("_mango_activity")
        if activity is not None:
            activity.attach(self)

    def __getattr__(self, name: str) -> Any:
        # Only reached when normal lookup fails, so it costs nothing on the
        # hot path. Turns the confusing errors caused by a forgotten
        # super().__init__() into one that names the fix.
        if name.startswith("__") or "_mango_inbox" in self.__dict__:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            )
        cls = type(self).__name__
        raise AttributeError(
            f"{cls!r} object has no attribute {name!r}: the agent was not "
            f"initialised by mango.Agent. Call super().__init__() first in "
            f"{cls}.__init__."
        )

    def __init__(
        self,
    ):
        """
        Initialize an agent
        """

        super().__init__()

        self.inbox = AgentInbox()
        self.context = AgentContext(None)

        apply_agent_subscriptions(self)
        # A purely declarative agent has no ``handle_message`` of its own;
        # calling the base one would raise NotImplementedError and kill the
        # inbox task, so it is skipped for those agents only.
        self._dispatches_to_handle_message = (
            not self._behavior_message_subs
            or type(self).handle_message is not Agent.handle_message
        )

    @property
    def observable_tasks(self):
        return self.scheduler.observable

    @observable_tasks.setter
    def observable_tasks(self, value: bool):
        self.scheduler.observable = value

    @property
    def suspendable_tasks(self):
        return self.scheduler.suspendable

    @suspendable_tasks.setter
    def suspendable_tasks(self, value: bool):
        self.scheduler.suspendable = value

    def on_register(self):
        """
        Hook-in to define behavior of the agent directly after it got registered by a container
        """

    def _do_register(self, container, aid):
        self._aid = aid
        self.context._container = container
        self.scheduler = Scheduler(
            suspendable=True, observable=True, clock=container.clock, owner=aid
        )
        # Populate description aid if not yet set
        if not self._description.name:
            self._description.name = aid
        self.on_register()

    def _do_start(self):
        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self._check_inbox_task.add_done_callback(self._raise_exceptions)
        self._stopped = asyncio.Future()

        self.on_start()

    def _do_ready(self):
        # @periodic tasks start here, not at registration: on_ready is the
        # first point at which sending messages from a task body is safe.
        apply_agent_periodic(self)
        self.on_ready()

    def _raise_exceptions(self, fut: asyncio.Future):
        """
        Inline function used as a callback to raise exceptions
        :param fut: The Future object of the task
        """
        try:
            if fut.exception() is not None:
                logger.error(
                    "Agent %s: Caught the following exception in _check_inbox: ",
                    self.aid,
                    fut.exception(),
                )
                raise fut.exception()
        except asyncio.CancelledError:
            pass

    async def _check_inbox(self):
        """Task for waiting on new message in the inbox"""

        try:
            logger.debug("Agent %s: Start waiting for messages", self.aid)
            while True:
                # run in infinite loop until it is cancelled from outside
                message = await self.inbox.get()
                logger.debug("Agent %s: Received message;%s", self.aid, message)

                # message should be tuples of (priority, content, meta)
                priority, content, meta = message
                meta["priority"] = priority

                if not tracing.is_tracing():
                    self._dispatch_message(content, meta)
                else:
                    with tracing._trigger(
                        "message",
                        "message",
                        agent=self.aid,
                        clock=self.scheduler.clock,
                        cause=meta.get(tracing.TRACE_MESSAGE_ID_KEY),
                        sender=meta.get("sender_id"),
                        content=content,
                    ):
                        self._dispatch_message(content, meta)

                # signal to the Queue that the message is handled
                self.inbox.task_done()
        except Exception as exc:
            self._inbox_failure = exc
            if self.__dict__.get("_mango_inbox_failure_reported") and getattr(
                self.context._container, "running", False
            ):
                # a simulation world raises it (once, with this traceback
                # chained) at its next settling point: one report, not one
                # traceback per failing agent
                logger.debug(
                    "The check inbox task of %s failed!", self.aid, exc_info=True
                )
            else:
                logger.exception("The check inbox task of %s failed!", self.aid)

    def _dispatch_message(self, content: Any, meta: dict) -> None:
        """Hand a received message to forwarding, replies and handlers."""
        # Check forwarding rules
        forwarded = self._check_forwarding_rules(content, meta)

        if not forwarded:
            # Update topology link-health (if any topology this
            # agent belongs to has ``edge_health`` enabled).
            # Done before user-defined dispatch so the role's
            # ``@on_message`` handler sees a freshly-nudged
            # neighbour score should it read one.
            self._nudge_topology_health(meta)
            # Push to any matching open conversation in addition
            # to (not instead of) normal dispatch: a role can
            # react via ``@on_message`` and pull from the
            # conversation iterator on the same message.
            self._route_to_conversation(content, meta)
            # Check tracked reply handlers
            if not self._handle_tracked_reply(content, meta):
                for _cond, _handler, _proc in self._behavior_message_subs:
                    if _cond(content, meta):
                        tracing._handler_called("message", _handler)
                        if _proc is not None:
                            _proc.handle(
                                self,
                                lambda c, m, h=_handler: h(self, c, m),
                                content,
                                meta,
                            )
                        else:
                            _handler(self, content, meta)
                if self._dispatches_to_handle_message:
                    tracing._handler_called("message", self.handle_message)
                    # positional, like roles: the parameter names are free
                    self.handle_message(content, meta)

    def _check_forwarding_rules(self, content: Any, meta: dict) -> bool:
        """Apply forwarding rules; returns True if a rule matched."""
        sender_id = meta.get("sender_id")
        sender_addr = meta.get("sender_addr")

        for rule in self._forwarding_rules:
            # Forward if message comes from the rule's from_addr
            if (
                rule.from_addr.aid == sender_id
                and rule.from_addr.protocol_addr == sender_addr
            ):
                self.schedule_instant_task(
                    self.send_message(
                        content,
                        receiver_addr=rule.to_addr,
                        forwarded=True,
                        forwarded_from_id=sender_id,
                        forwarded_from_addr=sender_addr,
                    )
                )
                return True
            # Forward replies back if forward_replies is enabled
            if (
                rule.forward_replies
                and rule.to_addr.aid == sender_id
                and rule.to_addr.protocol_addr == sender_addr
                and meta.get("reply")
                and meta.get("forwarded_from_id")
            ):
                from_id = meta.get("forwarded_from_id")
                from_addr_str = meta.get("forwarded_from_addr")
                orig_addr = AgentAddress(protocol_addr=from_addr_str, aid=from_id)
                self.schedule_instant_task(
                    self.send_message(
                        content,
                        receiver_addr=orig_addr,
                        reply=True,
                        tracking_id=meta.get("tracking_id"),
                    )
                )
                return True
        return False

    def handle_message(self, content, meta: dict[str, Any]):
        """
        Has to be implemented by the user.
        This method is called when a message is received at the agents inbox.
        :param content: The deserialized message object
        :param meta: Meta details of the message. In case of mqtt this dict
        includes at least the field 'topic'
        """
        raise NotImplementedError

    async def on_stop(self):
        """Can be used as lifecycle callback when the agent is stopped"""

    async def shutdown(self):
        """Shutdown all tasks that are running
        and deregister from the container"""
        await self.on_stop()

        # Backstop: cancel any conversations still open (e.g. one opened
        # with timeout=None whose peer never replied) so their iterators
        # unblock and the registry does not leak past agent lifetime.
        for handles in list(self._conversations.values()):
            for conv in list(handles):
                conv.cancel()
        self._conversations.clear()

        if not self._stopped.done():
            self._stopped.set_result(True)
        self.context.deregister(self.aid)
        try:
            # Shutdown reactive inbox task
            self._check_inbox_task.remove_done_callback(self._raise_exceptions)
            self._check_inbox_task.cancel()
            await self._check_inbox_task
        except asyncio.CancelledError:
            pass
        try:
            await self.scheduler.shutdown()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Agent %s: Shutdown successful", self.aid)
