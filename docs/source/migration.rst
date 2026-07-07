=========
Migration
=========

This page documents breaking API changes between major mango releases.

mango 1.2.x → 2.0.0
====================

ACL message helpers removed
-----------------------------

``send_acl_message`` and ``schedule_instant_acl_message`` have been removed.
In 2.0 all message types share the same internal routing envelope, so explicit
ACL wrappers are no longer needed.

.. code-block:: python

    # Before (1.2.x)
    await agent.send_acl_message(content, receiver_addr=addr)

    # After (2.0)
    await agent.send_message(content, receiver_addr=addr)

    # If you explicitly need an ACLMessage wrapper:
    from mango import create_acl
    await agent.send_message(create_acl(content), receiver_addr=addr)

Container factory consolidation
---------------------------------

``create_container`` has been removed.  Use the type-specific factory instead:

.. code-block:: python

    # Before (1.2.x)
    from mango import create_container
    c = await create_container(connection_type='tcp', addr=('127.0.0.1', 5555))

    # After (2.0)
    from mango import create_tcp_container
    c = create_tcp_container(addr=('127.0.0.1', 5555))  # synchronous!

Container startup moved to ``activate``
-----------------------------------------

The TCP server / MQTT client no longer starts inside the factory method.
Call ``activate`` (or ``container.start()``) to start the network layer.
This guarantees cleanup even when exceptions occur:

.. code-block:: python

    # Before (1.2.x)
    c = await create_container(...)
    # … do work …
    await c.shutdown()

    # After (2.0)
    from mango import activate
    async with activate(c):
        # … do work …
    # shutdown is automatic

Agents created independently of containers
-------------------------------------------

Agents are now created first, then registered with a container.  This is what
enables ``run_with_tcp`` and similar helpers to accept agents before any
container exists.

.. code-block:: python

    # Before (1.2.x)
    agent = MyAgent(container)   # container passed to __init__

    # After (2.0)
    agent = MyAgent()
    container.register(agent, suggested_aid="optional_id")

``AgentAddress`` for routing
------------------------------

The ``receiver_addr`` / ``receiver_id`` pair has been replaced by a single
:class:`~mango.AgentAddress`.  Use ``agent.addr`` or
:func:`~mango.sender_addr` to obtain addresses:

.. code-block:: python

    # Before (1.2.x)
    await agent.send_message(content, receiver_addr=('127.0.0.1', 5555),
                             receiver_id='agent0')

    # After (2.0)
    await agent.send_message(content, receiver_addr=other_agent.addr)

    # Replying to a received message:
    from mango import sender_addr
    await agent.send_message(reply, receiver_addr=sender_addr(meta))


mango 0.4.0 → 1.0.0
====================

* **Import paths changed** — ``Agent``, ``Container``, all role classes, and
  the container factory are now importable from the top-level ``mango``
  package::

      from mango import Agent, RoleAgent, Role

* **``handle_msg`` renamed** to ``handle_message`` in both ``Agent`` and
  ``Role``.

* **``send_message`` signature cleaned up** — the ``create_acl`` and
  ``acl_metadata`` parameters have been removed; use ``send_acl_message``
  instead (removed in 2.0, see above).  The ``mqtt_kwargs`` parameter has
  been removed; use plain ``**kwargs``.

* **``DateTimeScheduledTask`` removed** — use ``TimestampScheduledTask``
  with a Unix timestamp instead.

* **Context and scheduler are no longer public attributes** — use the
  scheduling convenience methods (e.g. ``schedule_periodic_task``) or access
  ``_context`` / ``_scheduler`` from within an agent subclass.
