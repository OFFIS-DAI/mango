=======================
Transactional messaging
=======================

Many multi-agent protocols are not fire-and-forget: an agent asks a question
and needs the answer, polls a group of peers and waits for a quorum, or runs a
back-and-forth exchange that spans many hops.  mango provides three primitives
for this, each a step up in scope:

.. list-table::
   :widths: 25 40 35
   :header-rows: 1

   * - Primitive
     - Use it when…
     - Waits for
   * - :meth:`~mango.Agent.send_tracked_message`
     - you send one request and want the one matching reply.
     - a single reply (a callback fires).
   * - :meth:`~mango.Agent.gather`
     - you ask *many* peers the same thing in one round.
     - a quorum of replies, or a timeout.
   * - :meth:`~mango.Agent.open_conversation`
     - the exchange runs over many messages / many hops.
     - as long as you keep the conversation open.

All three primitives are available on plain agents (``self.gather(...)``)
and inside roles (``self.context.gather(...)``) alike.
The single tracked request/reply pair (``send_tracked_message`` / ``reply_to``)
is covered on the :doc:`message exchange <message exchange>` page.  This page
covers the two multi-message primitives.
Both are clock-aware: their timeouts run on the agent's scheduler clock, so
they behave identically under real time (:class:`~mango.util.clock.AsyncioClock`)
and simulation time (:class:`~mango.util.clock.ExternalClock`).


Collecting replies with gather
==============================

:meth:`~mango.Agent.gather` sends one message to many receivers and
returns their replies as a dict keyed by the responding agent's
:class:`~mango.AgentAddress`.  It is the right tool for one-round request
scatter/gather — a price poll, a capability query, a distributed sum:

.. code-block:: python

    from mango import Role, sender_addr

    class Coordinator(Role):
        async def poll_prices(self, sellers):
            replies = await self.context.gather(
                PriceRequest(),
                sellers,                 # iterable of AgentAddress
                reply_type=PriceOffer,   # ignore any non-PriceOffer traffic
                timeout=2.0,             # scheduler-clock seconds
            )
            # replies: {AgentAddress: PriceOffer}
            best = min(replies.values(), key=lambda offer: offer.price)
            return best

Responders need no special API — they just reply, echoing the ``tracking_id``.
:meth:`~mango.Agent.reply_to` does that automatically:

.. code-block:: python

    class Seller(Role):
        @on_message(PriceRequest)
        async def on_request(self, content, meta):
            await self.context.reply_to(PriceOffer(price=self.price), meta)

**Quorum and partial results.**  By default ``gather`` waits for *all*
receivers or times out.  Lower ``min_fraction`` to return as soon as a fraction
has answered — useful when stragglers should not hold up progress:

.. code-block:: python

    # return as soon as ⌈0.5 · N⌉ replies are in (majority quorum)
    replies = await self.context.gather(Ping(), peers, min_fraction=0.5)

On timeout, ``gather`` returns whatever arrived so far rather than raising — so
always be prepared for fewer entries than receivers.  The first reply from each
sender wins; late duplicates are dropped so the mapping is stable.

.. list-table::
   :widths: 25 20 55
   :header-rows: 1

   * - Parameter
     - Default
     - Meaning
   * - ``reply_type``
     - ``None``
     - Only accept replies of this type/tuple; others are dropped.
   * - ``timeout``
     - ``5.0``
     - Scheduler-clock seconds before returning partial results.
   * - ``min_fraction``
     - ``1.0``
     - Return early once ``⌈min_fraction · len(receivers)⌉`` replied.


Multi-hop conversations
=======================

A *conversation* groups a sequence of messages under one shared id so
participants can volley back and forth without every reply being consumed on
receipt.  Reach for it when a single tracked reply or one ``gather`` round is
not enough — gossip, auctions, multi-round negotiation, iterative distributed
optimisation.

The initiator opens a conversation, sends into it, and iterates the replies as
they arrive:

.. code-block:: python

    from mango import Role, sender_addr

    class Negotiator(Role):
        async def run(self, peer):
            async with self.context.open_conversation(
                timeout=10.0,
                state={"round": 0},
            ) as conv:
                await conv.send(peer, Offer(price=100))
                async for content, meta in conv:
                    conv.state["round"] += 1
                    if self.acceptable(content) or conv.state["round"] >= 5:
                        conv.converge()          # graceful stop
                        continue
                    await conv.send(peer, Offer(price=content.price - 5))

The handle yielded by ``open_conversation`` is a
:class:`~mango.Conversation`:

.. list-table::
   :widths: 32 68
   :header-rows: 1

   * - Member
     - Description
   * - ``await conv.send(addr, content, **kwargs)``
     - Send tagged with this conversation's id; returns the send result.
   * - ``await conv.broadcast(addrs, content)``
     - Send the same content to several receivers; returns a dict of
       per-receiver send results keyed by address.
   * - ``conv.state``
     - A user-owned ``dict`` for carrying protocol state; mango never reads it.
   * - ``async for content, meta in conv``
     - Iterate inbound messages of this conversation until it closes.
   * - ``conv.converge()``
     - **Graceful** stop: already-queued messages still deliver, then the
       iterator ends.
   * - ``conv.cancel()``
     - **Abrupt** stop: queued messages are dropped; the iterator ends on the
       next pull.
   * - ``conv.closed``
     - ``True`` once converged or cancelled.

.. note::

   A conversation message is delivered to matching ``@on_message`` handlers
   *and* pushed to the conversation iterator — the iterator receives it in
   addition to, not instead of, normal dispatch.  Replying with
   :meth:`~mango.Agent.reply_to` inside a conversation keeps the id, so the
   reply routes back into the initiator's iterator automatically.

Responding side
---------------

A responder joins the existing exchange from inside an ``@on_message`` handler
using the inbound ``meta`` (which carries the conversation id).  The simplest
responders reply once and let the ``with`` block close the handle:

.. code-block:: python

    class Peer(Role):
        @on_message(Offer)
        async def on_offer(self, content, meta):
            async with self.context.join_conversation(meta) as conv:
                await conv.send(sender_addr(meta), Offer(price=content.price + 3))

You may also keep the joined conversation open and iterate it for follow-ups.
Every join owns an independent handle with its own ``state`` and ``timeout``;
when several handles for the same id are open on one agent (e.g. a re-firing
handler while an earlier join is still iterating), each inbound message is
delivered to *all* of them.

``conv.state`` lives on the handle, so it does not persist across separate
``join_conversation`` blocks — a responder that accumulates state over many
handler invocations should keep it on the role (or agent) instance instead.

Ending an exchange
------------------

Two control methods end iteration, plus the timeout:

* :meth:`~mango.agent.conversation.Conversation.converge` — graceful: drain
  what is already queued, then stop.  Use it when the protocol reached a
  result.
* :meth:`~mango.agent.conversation.Conversation.cancel` — abrupt: drop the
  queue and stop.  Use it to abandon.
* **Timeout** — pass ``timeout=`` to ``open_conversation`` /
  ``join_conversation``.  When it elapses (measured on the scheduler clock) the
  conversation is cancelled automatically.  ``conv.state`` remains readable
  after the ``with`` block exits, so you can inspect the outcome.

Leaving the ``async with`` block also cancels the handle — an iterator that
escaped the block terminates instead of waiting forever.

.. warning::

   ``open_conversation`` defaults to ``timeout=None`` (no timeout) — unlike
   ``gather``, conversations are long-lived by design.  An un-timed
   conversation whose peer never replies will block its ``async for``
   forever — always set a ``timeout`` for protocols that can stall.  (Open
   conversations are cleaned up on agent shutdown as a backstop.)

.. note::

   ``conv.send`` also stamps the id onto content that has a
   ``conversation_id`` attribute (e.g. ACL messages created with
   :func:`~mango.create_acl`), so the id survives transports that send such
   content without the surrounding meta.

.. seealso::

   :doc:`simulation` — conversation and ``gather`` timeouts advance with
   simulation time under :func:`~mango.run_with_simulation`, so the same
   protocol code runs unchanged in real time and in a discrete-event world.
