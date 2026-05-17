"""Multi-hop conversation primitive.

A *conversation* groups a sequence of messages that share a single id
so participants can volley back and forth without each reply being
consumed on receipt (the way ``tracking_id`` is in
:meth:`AgentDelegates.send_tracked_message`).  Use it for protocols
that span many hops — gossip, auctions, holonic ADMM coordination.

The handle is an async context manager that yields a
:class:`Conversation` carrying the id, a user-controlled state dict,
and a receive queue::

    async with self.context.open_conversation(
        timeout=10.0,
        state={"target": -5.0, "delta": 0.0},
    ) as conv:
        await conv.send(neighbour, GossipStep(...))
        async for msg, meta in conv:
            update(conv.state, msg)
            if conv.state["delta"] >= conv.state["target"]:
                conv.converge()
                continue
            await conv.send(pick_next_hop(), GossipStep(...))

Responders join the existing exchange via the inbound ``meta``::

    @on_message(GossipStep)
    async def on_step(self, msg, meta):
        async with self.context.join_conversation(meta) as conv:
            ...

Two control methods end the iteration:

* :meth:`Conversation.converge` — graceful: any messages already on
  the queue still deliver, then the iterator exits.
* :meth:`Conversation.cancel` — abrupt: queued messages are discarded
  and the iterator exits on the next pull.  Also called by the context
  manager when its clock-aware timeout fires.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Carried alongside ``tracking_id`` because the latter is consumed by
# the single-shot reply machinery; a conversation message may legitimately
# bear both (e.g. when a participant calls ``reply_to`` inside a session).
CONVERSATION_ID_KEY = "conversation_id"


class Conversation:
    """A live, agent-owned conversation handle.

    The ``state`` dict is user-controlled — mango never reads it.
    """

    # Queue sentinel that signals end-of-iteration.  A class-level
    # singleton so identity checks are unambiguous.
    _END = object()

    def __init__(
        self,
        *,
        owner,  # RoleContext
        conversation_id: str,
        state: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._owner = owner
        self._conversation_id = conversation_id
        self.state: dict[str, Any] = dict(state) if state else {}
        self._timeout = timeout
        self._queue: asyncio.Queue = asyncio.Queue()
        self._converged = False
        self._cancelled = False

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def closed(self) -> bool:
        return self._converged or self._cancelled

    def converge(self) -> None:
        """Signal graceful end-of-iteration.

        Messages already on the queue still deliver; the iterator exits
        once the queue drains.  Subsequent inbound messages are dropped.
        Idempotent.
        """
        if self.closed:
            return
        self._converged = True
        # Wake an idle ``__anext__`` if the queue was empty; otherwise
        # the sentinel simply lands behind anything still pending and
        # ends iteration after it drains.
        self._queue.put_nowait((self._END, None))

    def cancel(self) -> None:
        """Signal abrupt end-of-iteration.

        Drops any queued messages and ends the iterator on the next
        pull.  Also called by the context manager when its timeout
        fires.  Idempotent.
        """
        if self._cancelled:
            return
        self._cancelled = True
        self._queue.put_nowait((self._END, None))

    async def send(self, receiver_addr, content: Any, **kwargs) -> bool:
        """Send *content* tagged with this conversation's id."""
        return await self._owner.send_message(
            content,
            receiver_addr=receiver_addr,
            **{CONVERSATION_ID_KEY: self._conversation_id, **kwargs},
        )

    async def broadcast(self, receivers, content: Any, **kwargs) -> None:
        """Send *content* to every address in *receivers*."""
        for addr in receivers:
            await self.send(addr, content, **kwargs)

    # -- agent-facing hook --------------------------------------------
    def _on_inbound(self, content: Any, meta: dict) -> None:
        """Called by the agent's router when a matching id arrives."""
        if self.closed:
            return
        self._queue.put_nowait((content, meta))

    # -- iteration ----------------------------------------------------
    def __aiter__(self):
        return self

    async def __anext__(self):
        # cancel() is abrupt: anything still on the queue is dropped.
        if self._cancelled:
            raise StopAsyncIteration
        content, meta = await self._queue.get()
        if content is self._END:
            raise StopAsyncIteration
        return content, meta
