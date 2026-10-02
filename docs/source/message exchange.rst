================
Message exchange
================

mango agents communicate by passing messages through their container.  This
page covers the full messaging API: receiving, all send variants, routing, and
the optional FIPA ACL layer.

.. grid:: 1 2 3 3
   :gutter: 3

   .. grid-item-card:: ``@on_message``
      :shadow: sm

      **Receive**
      ^^^
      One handler per message type, on an agent or a role.

   .. grid-item-card:: ``send_message``
      :shadow: sm

      **Basic send**
      ^^^
      One-to-one delivery.  The foundation of all messaging.

   .. grid-item-card:: ``send_messages``
      :shadow: sm

      **Broadcast**
      ^^^
      Same content to a list of recipients.

   .. grid-item-card:: ``schedule_instant_message``
      :shadow: sm

      **Non-async send**
      ^^^
      Fire-and-forget from synchronous callbacks.

   .. grid-item-card:: ``reply_to``
      :shadow: sm

      **Reply**
      ^^^
      Auto-extracts sender address and preserves tracking context.

   .. grid-item-card:: ``send_tracked_message``
      :shadow: sm

      **Request / response**
      ^^^
      Attaches a ``tracking_id`` and calls a handler on the matching reply.

   .. grid-item-card:: Forwarding rules
      :shadow: sm

      **Routing**
      ^^^
      Declare proxy rules so an agent relays messages without boilerplate.

----

Receiving messages
==================

:func:`~mango.on_message` subscribes a method to a message type.  The agent
hands it every message whose content is an instance of that type, together
with the message's ``meta``:

.. testcode::

    from mango import Agent, on_message

    class SimpleReceivingAgent(Agent):
        def __init__(self):
            super().__init__()

        @on_message(str)
        def handle_text(self, content, meta):
            print(f'{self.aid} received a message with content {content} and '
                f'meta {meta}')

Declare one handler per message type instead of a chain of ``isinstance``
checks.  A handler may be ``async def``: the agent runs it as an instant
task, so it can ``await`` a send of its own without blocking the inbox.
Agents and roles use the same decorator.  For messages that a type cannot
tell apart, see `Alternatives`_ below.

The ``meta`` dict is populated by the container before delivery.  Several
fields are always present:

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Field
     - Value
   * - ``sender_addr``
     - Protocol address of the sending container (``tuple`` for TCP,
       ``str`` for MQTT)
   * - ``sender_id``
     - AID of the sending agent
   * - ``receiver_id``
     - AID of the intended recipient
   * - ``network_protocol``
     - ``"tcp"`` or ``"mqtt"``
   * - ``priority``
     - Integer priority; lower number = higher priority (default ``0``)

Additional fields are set by the sender via ``kwargs`` and by the ACL layer
when an :class:`~mango.messages.message.ACLMessage` is unpacked.

.. tip::

    Use :func:`~mango.sender_addr` to build an :class:`~mango.AgentAddress`
    from ``meta``; it handles the list-vs-tuple discrepancy that JSON decoding
    can introduce:

    .. code-block:: python

        from mango import sender_addr

        @on_message(Request)
        async def handle_request(self, content, meta):
            reply_to = sender_addr(meta)   # AgentAddress, safe to use with send_message

Alternatives
------------

A type subscription fits when the content object says what the message
means.  When it does not, these take over.

**``handle_message``** receives every message the agent gets, unfiltered,
after the decorated handlers have run.  Override it when the meaning sits in
``meta`` rather than in the content type, or when the agent has to see all
traffic:

.. code-block:: python

    class LoggingAgent(Agent):
        def handle_message(self, content, meta):
            print(f"{meta['sender_id']} -> {self.aid}: {content}")

It is also the only place to notice a message that no handler claimed; a
decorated agent drops those silently.

**``subscribe_message``** is the explicit form of ``@on_message`` inside a
role.  It takes a condition function instead of a type, accepts a
:class:`~mango.MessagePreprocessor`, and can be registered at runtime.  See
`Role-based message dispatch`_ below and :doc:`role-api`.

**Forwarding rules** relay a message without handling it at all (see
`Forwarding rules`_ below).  In a simulation, :func:`~mango.behavior_in` adds
a handler from the outside, without touching the agent class (see
:doc:`simulation`).

----

Sending messages
================

``send_message``: one-to-one
------------------------------

.. code-block:: python

    async def send_message(self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ) -> bool

``content`` can be any JSON-serialisable object (or a protobuf / ACL message
when the matching codec is configured).  ``receiver_addr`` must be an
:class:`~mango.AgentAddress`; use :attr:`~mango.Agent.addr`,
:func:`~mango.sender_addr`, or :func:`~mango.addr` to create one.

Extra ``kwargs`` are injected into ``meta`` on the receiving side and may be
interpreted by the protocol layer (e.g. ``priority``).

.. testcode::

    import asyncio
    from mango import run_with_tcp

    async def send_to_receiving():
        receiving_agent = SimpleReceivingAgent()
        sending_agent = SimpleReceivingAgent()

        async with run_with_tcp(1, receiving_agent, sending_agent) as cl:
            await sending_agent.send_message("Hey!", receiving_agent.addr)
            await asyncio.sleep(0.1)

    asyncio.run(send_to_receiving())

.. testoutput::

    agent0 received a message with content Hey! and meta {'sender_id': 'agent1', 'sender_addr': ('127.0.0.1', 5555), 'receiver_id': 'agent0', 'network_protocol': 'tcp', 'priority': 0}


``reply_to``: replying to a message
--------------------------------------

:meth:`~mango.Agent.reply_to` is the idiomatic way to answer a received
message.  It extracts the sender address from ``meta`` automatically and
preserves any ``tracking_id`` so tracked conversations keep working:

.. code-block:: python

    class EchoAgent(Agent):
        @on_message(str)
        async def handle_text(self, content, meta):
            await self.reply_to(f"Echo: {content}", meta)

When you need the :class:`~mango.AgentAddress` itself (for example to cache
it and send a message later), use :func:`~mango.sender_addr` directly:

.. code-block:: python

    class TrackingAgent(Agent):
        def __init__(self):
            super().__init__()
            self.known_peers = []

        @on_message(Hello)
        async def handle_hello(self, content, meta):
            self.known_peers.append(sender_addr(meta))
            await self.send_message("acknowledged", sender_addr(meta))


``send_messages``: broadcasting
----------------------------------

Send the same content to a list of addresses.  Returns a list of booleans,
one success flag per recipient in the same order:

.. code-block:: python

    async def broadcast(self, content, recipients):
        results = await self.send_messages(content, recipients)
        print(f"Delivered to {sum(results)}/{len(recipients)}")

    # usage
    await self.broadcast("update", [a.addr for a in peer_agents])


``schedule_instant_message``: non-async context
--------------------------------------------------

Inside synchronous callbacks (``on_ready``, ``on_register``, ``on_step``, …)
you cannot ``await`` directly.  Use
:meth:`~mango.Agent.schedule_instant_message` to schedule the send as a
background task and return immediately:

.. code-block:: python

    class StarterAgent(Agent):
        def on_ready(self):
            self.schedule_instant_message("start signal", coordinator.addr)

The return value is an :class:`asyncio.Task`; ``await`` it later if you need
to confirm delivery.

.. note::

    ``schedule_instant_message`` is equivalent to
    ``schedule_instant_task(self.send_message(…))``.  For non-message
    coroutines use ``schedule_instant_task`` directly.


``send_tracked_message``: request / response
----------------------------------------------

When a reply must be matched back to a specific outgoing request use
:meth:`~mango.Agent.send_tracked_message`.  It attaches a UUID
``tracking_id`` to the outgoing message and, when the matching reply arrives,
invokes *response_handler*:

.. code-block:: python

    class RequesterAgent(Agent):
        def on_ready(self):
            self.schedule_instant_task(self._do_request())

        async def _do_request(self):
            def on_response(content, meta):
                print(f"Got response: {content}")

            await self.send_tracked_message(
                "What is the answer?",
                receiver_addr=responder.addr,
                response_handler=on_response,
            )

On the responder side, :meth:`~mango.Agent.reply_to` preserves the
``tracking_id`` automatically; no extra work required:

.. code-block:: python

    class ResponderAgent(Agent):
        @on_message(str)
        async def handle_question(self, content, meta):
            await self.reply_to("42", meta)

.. note::

    Omitting *response_handler* still attaches a ``tracking_id`` so you can
    correlate replies manually via ``meta["tracking_id"]``.

----

Message routing
===============

When ``send_message`` is called the container first checks whether
``receiver_addr.protocol_addr`` matches its own address:

* **Same container**: the message is placed directly in the receiver agent's
  inbox queue.  No serialisation, no network round-trip.
* **Remote container**: the message is serialised with the configured codec
  and sent over TCP or MQTT.

On the receiving side, the container deserialises the payload and looks up the
agent whose AID matches ``receiver_addr.aid``, then pushes the message into
that agent's inbox.

.. note::

    The order of delivery is guaranteed within a single container (asyncio
    queue), but **not** across containers; network reordering can occur with
    TCP and definitely with MQTT.


Forwarding rules
----------------

A *forwarding rule* tells an agent to relay every message from a specific
sender to a different destination, without writing any ``handle_message``
logic.  This is useful for proxy, gateway, and mediator patterns.

Add a rule with :meth:`~mango.Agent.add_forwarding_rule`:

.. code-block:: python

    self.add_forwarding_rule(
        from_addr=coordinator.addr,   # match messages from this sender
        to_addr=backend.addr,         # relay them here
    )

Setting ``forward_replies=True`` makes the relay bidirectional: replies from
*to_addr* are forwarded back to the original *from_addr* transparently.

.. code-block:: python

    class GatewayAgent(Agent):
        """Transparent relay between a coordinator and a backend specialist."""

        def __init__(self, coordinator_addr, backend_addr):
            super().__init__()
            self._coordinator_addr = coordinator_addr
            self._backend_addr = backend_addr

        def on_ready(self):
            self.add_forwarding_rule(
                from_addr=self._coordinator_addr,
                to_addr=self._backend_addr,
                forward_replies=True,   # replies from backend go back to coordinator
            )

When a message arrives from ``coordinator_addr`` the gateway forwards it to
``backend_addr`` and never calls ``handle_message``.  If ``forward_replies``
is set and the backend later replies, the gateway transparently routes that
reply back to the coordinator.

Remove a rule with :meth:`~mango.Agent.delete_forwarding_rule`:

.. code-block:: python

    # Remove all rules with coordinator as the source
    self.delete_forwarding_rule(from_addr=coordinator.addr)

    # Remove only the rule pointing to a specific destination
    self.delete_forwarding_rule(from_addr=coordinator.addr, to_addr=backend.addr)

.. note::

    Forwarding rules are checked **before** ``handle_message`` is called.  If
    a rule matches, the message is forwarded and ``handle_message`` is *not*
    invoked for that message.


.. _role-message-dispatch:

Role-based message dispatch
----------------------------

When using the :doc:`role system <role-api>`, every role of a
:class:`~mango.RoleAgent` brings its own handlers, and one message can reach
several of them.  Each role declares what it handles:

.. code-block:: python

    from mango import Role, agent_composed_of, on_message

    class RequestRole(Role):
        @on_message(Request)
        def handle_request(self, content, meta):
            ...

    class StatusRole(Role):
        @on_message(StatusUpdate)
        def handle_status(self, content, meta):
            ...

    agent = agent_composed_of(RequestRole(), StatusRole())

The ``RoleAgent`` evaluates each registered condition in priority order (lower
number = higher priority, default ``0``) and calls every method whose
condition returns ``True``.  Multiple roles can handle the same message.

:meth:`~mango.RoleContext.subscribe_message` registers the same subscription
explicitly, in the role's ``setup``.  Use it when the condition is not a type
check, when a :class:`~mango.MessagePreprocessor` is needed, or when the
subscription depends on runtime state:

.. code-block:: python

    class RequestRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.handle_request,
                lambda content, meta: isinstance(content, Request)
                and meta.get("priority", 0) == 0,
            )

        def handle_request(self, content, meta):
            ...

.. tip::

    Subscriptions with a higher *priority* value are checked **last**.  Use
    priorities to implement fallback handlers:

    .. code-block:: python

        # Low-priority catch-all: fires if no other role claimed the message
        self.context.subscribe_message(self, self.catch_all,
                                        lambda c, m: True, priority=100)

----

ACL messages
============

mango supports the `FIPA ACL <http://www.fipa.org/specs/fipa00061/SC00061G.html>`_
standard through :func:`~mango.create_acl` and the
:class:`~mango.messages.message.Performatives` enum.  An ACL message wraps
the content and carries additional envelope fields such as a *performative*,
*conversation_id*, and *ontology*.

Use :func:`~mango.create_acl` to build the message, then send it with the
normal ``send_message``:

.. code-block:: python

    import asyncio
    from mango import Agent, create_acl, on_message, run_with_tcp, sender_addr
    from mango.messages.message import Performatives

    def performative(kind):
        """Match messages whose ACL performative is *kind*."""
        return lambda self, content, meta: meta.get("performative") == kind

    class BuyerAgent(Agent):
        def on_ready(self):
            self.schedule_instant_task(self._send_cfp())

        async def _send_cfp(self):
            acl = create_acl(
                {"item": "widget", "max_price": 50},
                receiver_addr=seller.addr,
                sender_addr=self.addr,
                acl_metadata={
                    "performative": Performatives.cfp,
                    "conversation_id": "negotiation-42",
                },
            )
            await self.send_message(acl, seller.addr)

        @on_message(dict, where=performative(Performatives.propose))
        def handle_proposal(self, content, meta):
            print(f"Received proposal: {content['price']}")

    class SellerAgent(Agent):
        @on_message(dict, where=performative(Performatives.cfp))
        async def handle_cfp(self, content, meta):
            reply = create_acl(
                {"price": 45},
                receiver_addr=sender_addr(meta),
                sender_addr=self.addr,
                acl_metadata={
                    "performative": Performatives.propose,
                    "conversation_id": meta.get("conversation_id"),
                },
            )
            await self.send_message(reply, sender_addr(meta))

.. note::

    When an :class:`~mango.messages.message.ACLMessage` is delivered, the
    container unpacks its fields into the ``meta`` dict automatically, so
    ``meta["performative"]``, ``meta["conversation_id"]`` and the rest are
    readable in a handler without unwrapping the object.  The ``where``
    filter above uses that to give each performative its own method;
    ``handle_message`` is the alternative when one method should sort them
    out.

The full list of FIPA performatives is available as
:class:`~mango.messages.message.Performatives`:

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Performative
     - Meaning
   * - ``cfp`` / ``call_for_proposal``
     - Initiate a negotiation: invite proposals
   * - ``propose``
     - Respond with a concrete offer
   * - ``accept_proposal`` / ``reject_proposal``
     - Accept or reject a received proposal
   * - ``request``
     - Ask another agent to perform an action
   * - ``inform``
     - Convey a fact or result
   * - ``agree`` / ``refuse``
     - Confirm or decline a request
   * - ``failure``
     - Report that a requested action could not be performed
   * - ``not_understood``
     - Indicate the message was not understood
   * - ``cancel``
     - Withdraw a previous request
   * - ``subscribe`` / ``query_if`` / ``query_ref``
     - Subscription and query patterns

Pass ``is_anonymous_acl=True`` to :func:`~mango.create_acl` to omit sender
address information from the envelope (useful when anonymity is required).
