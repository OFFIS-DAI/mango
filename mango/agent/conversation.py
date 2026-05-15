"""Multi-hop conversation primitive.

A *conversation* is a logically-grouped sequence of messages threaded
by a shared id.  Mango's existing ``tracking_id`` mechanism solves the
single request/response case (see :meth:`AgentDelegates.send_tracked_message`
and :meth:`AgentDelegates.reply_to`), but multi-hop protocols — gossip,
auctions, holonic ADMM coordination — need the same id to route many
messages over time *without* being consumed on first reply.

:class:`Conversation` is that abstraction: an async context manager
that holds a conversation id, a clock-aware timeout, mutable state,
and a receive queue.  Anyone in the conversation (initiator or joiner)
can ``async for msg, meta in conversation`` and ``await
conversation.send(addr, payload)``.

Two entry points on :class:`mango.RoleContext`:

* :meth:`RoleContext.open_conversation` — initiator: generates a new id
  and starts a fresh conversation.
* :meth:`RoleContext.join_conversation` — joiner: re-uses the id from
  an inbound message's ``meta`` so the responder participates in the
  same exchange.

Both return a :class:`Conversation`.  The timeout is enforced via the
agent's scheduler clock so simulation and real-time modes behave the
same way.

Example — gossip-style initiator::

    async with self.context.open_conversation(
        timeout=10.0,
        state={"target": -5.0, "delta": 0.0, "lambda": 0.01},
    ) as conv:
        await conv.send(neighbours[0], GossipStep(payload=...))
        async for msg, meta in conv:
            update(conv.state, msg)
            if conv.state["delta"] >= conv.state["target"]:
                conv.converge()  # exits the loop on next iteration
                continue
            next_hop = pick(...)
            await conv.send(next_hop, GossipStep(payload=...))

Example — joiner side::

    @on_message(GossipStep)
    async def on_step(self, msg, meta):
        async with self.context.join_conversation(meta) as conv:
            ...  # same async for loop as above
"""

from __future__ import annotations

import asyncio
from typing import Any

# Metadata key used to carry the conversation id alongside ``tracking_id``.
# Distinct from ``tracking_id`` because the latter is consumed by the
# single-shot reply machinery; a conversation may legitimately carry
# both (e.g. when a participant uses ``reply_to`` inside a session).
CONVERSATION_ID_KEY = "conversation_id"


class Conversation:
    """Active conversation handle owned by one agent.

    The state dict is purely user-controlled — mango does not interpret
    it.  ``converge()`` ends the async iteration after the current
    message; ``cancel()`` ends it immediately with no further yields.
    Both are idempotent.
    """

    def __init__(
        self,
        *,
        owner,
        conversation_id: str,
        state: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._owner = owner  # RoleContext
        self._conversation_id = conversation_id
        self.state: dict[str, Any] = dict(state) if state else {}
        self._timeout = timeout
        self._queue: asyncio.Queue = asyncio.Queue()
        self._converged: bool = False
        self._cancelled: bool = False
        self._timeout_handle = None  # asyncio.Task | None

    # -- introspection ------------------------------------------------
    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def closed(self) -> bool:
        return self._converged or self._cancelled

    # -- public control -----------------------------------------------
    def converge(self) -> None:
        """Mark the conversation as converged.  The async iterator
        will terminate after delivering any already-queued message."""
        self._converged = True
        # Wake the receiver if it's idle waiting on the queue.
        self._queue.put_nowait((_SENTINEL_END, None))

    def cancel(self) -> None:
        """Drop the conversation immediately — discards any queued
        messages and terminates the async iterator on the next pull."""
        self._cancelled = True
        self._queue.put_nowait((_SENTINEL_END, None))

    # -- send helpers -------------------------------------------------
    async def send(self, receiver_addr, content: Any, **kwargs) -> bool:
        """Send *content* tagged with this conversation's id."""
        meta_extra = {CONVERSATION_ID_KEY: self._conversation_id}
        meta_extra.update(kwargs)
        return await self._owner.send_message(
            content,
            receiver_addr=receiver_addr,
            **meta_extra,
        )

    async def broadcast(self, receivers, content: Any, **kwargs) -> None:
        """Send *content* to every receiver in *receivers*."""
        for addr in receivers:
            await self.send(addr, content, **kwargs)

    # -- internal hooks (used by RoleContext) -------------------------
    def _on_inbound(self, content: Any, meta: dict) -> None:
        if self.closed:
            return
        self._queue.put_nowait((content, meta))

    def _fire_timeout(self) -> None:
        if self.closed:
            return
        # Treat timeout as cancellation — partial state remains
        # accessible to the caller after exiting the context.
        self._cancelled = True
        self._queue.put_nowait((_SENTINEL_END, None))

    # -- iteration ----------------------------------------------------
    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._cancelled:
            raise StopAsyncIteration
        content, meta = await self._queue.get()
        if content is _SENTINEL_END:
            raise StopAsyncIteration
        if self._converged:
            # Deliver this final message, then end on the next pull by
            # putting a sentinel back into the queue.
            self._queue.put_nowait((_SENTINEL_END, None))
        return content, meta


# Sentinel placed in the queue to end the async iterator.  A module-
# level singleton so identity checks are cheap and unambiguous.
_SENTINEL_END = object()
