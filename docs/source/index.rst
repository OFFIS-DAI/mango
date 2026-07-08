mango — modular python agent framework
=======================================

.. div:: sd-text-center sd-py-4

   **asyncio-native framework for multi-agent systems in Python**

   mango provides containers, agents, role composition, scheduling, and a
   discrete-event simulation world — covering the full spectrum from small
   prototypes to large distributed deployments under a single, consistent API.

   .. grid:: 3
      :class-container: sd-justify-content-center

      .. grid-item::

         .. button-ref:: getting_started
            :ref-type: doc
            :color: primary
            :shadow:

            Get started

      .. grid-item::

         .. button-ref:: tutorial
            :ref-type: doc
            :color: secondary
            :outline:

            Tutorials

      .. grid-item::

         .. button-link:: https://github.com/OFFIS-DAI/mango
            :color: secondary
            :outline:

            GitHub

   .. code-block:: bash

      pip install mango-agents

----

Features
--------

.. grid:: 1 2 2 3
   :gutter: 4

   .. grid-item-card::
      :shadow: sm

      **Containers**
      ^^^
      Local, TCP, and MQTT transports; external-coupling container for co-simulation.

   .. grid-item-card::
      :shadow: sm

      **Agents**
      ^^^
      Reactive and proactive behaviour; full lifecycle callbacks (``on_register``, ``on_ready``, ``on_stop``).

   .. grid-item-card::
      :shadow: sm

      **Role system**
      ^^^
      Compose agent behaviour from small, reusable ``Role`` classes with shared state and event subscriptions — wired declaratively with ``@on_message``, ``@on_event``, and ``@periodic``.

   .. grid-item-card::
      :shadow: sm

      **Transactional messaging**
      ^^^
      Multi-reply ``gather`` with quorum and timeout, and multi-hop conversations for gossip, auctions, and negotiation — clock-aware in real time and simulation.

   .. grid-item-card::
      :shadow: sm

      **Scheduling**
      ^^^
      Instant, periodic, timestamp, and conditional tasks in real-time (``AsyncioClock``) or simulation time (``ExternalClock``).

   .. grid-item-card::
      :shadow: sm

      **Simulation world**
      ^^^
      Discrete-event and fixed-step simulation; configurable message delay and loss; spatial environments and data recording.

   .. grid-item-card::
      :shadow: sm

      **Topologies**
      ^^^
      Distribute a ``networkx`` graph to agents so every agent knows its direct neighbours.

   .. grid-item-card::
      :shadow: sm

      **Codecs**
      ^^^
      Built-in JSON and protobuf serialisation; custom serialisers via ``add_serializer`` or the ``@json_serializable`` decorator.

   .. grid-item-card::
      :shadow: sm

      **FIPA ACL**
      ^^^
      Optional FIPA-compliant message wrapper for interoperability with other agent platforms.

----

Quick look
----------

.. tab-set::

   .. tab-item:: Agent

      .. code-block:: python

         import asyncio
         from mango import Agent, create_tcp_container, activate

         class HelloAgent(Agent):
             def on_ready(self):
                 self.schedule_instant_message("Hello, mango!", self.addr)

             def handle_message(self, content, meta):
                 print(content)  # → Hello, mango!

         async def main():
             c = create_tcp_container(addr=("127.0.0.1", 5555))
             c.register(HelloAgent())
             async with activate(c):
                 await asyncio.sleep(0.05)

         asyncio.run(main())

   .. tab-item:: Roles

      .. code-block:: python

         import asyncio
         from mango import Role, agent_composed_of, run_with_tcp

         class Ping:
             pass

         class PingRole(Role):
             def setup(self):
                 self.context.subscribe_message(
                     self,
                     self.handle_ping,
                     lambda content, meta: isinstance(content, Ping),
                 )

             def handle_ping(self, content, meta):
                 print("Ping received!")

         async def main():
             my_agent = agent_composed_of(PingRole())
             async with run_with_tcp(1, my_agent) as container:
                 await container.send_message(Ping(), my_agent.addr)
                 await asyncio.sleep(0.05)

         asyncio.run(main())

----

Where to go next
----------------

.. grid:: 1 2 2 4
   :gutter: 3

   .. grid-item-card:: Getting started
      :link: getting_started
      :link-type: doc
      :text-align: center
      :shadow: sm

      Install mango and write your first agent in minutes.

   .. grid-item-card:: Tutorials
      :link: tutorial
      :link-type: doc
      :text-align: center
      :shadow: sm

      End-to-end worked examples including simulation.

   .. grid-item-card:: User guide
      :link: agents-container
      :link-type: doc
      :text-align: center
      :shadow: sm

      In-depth guide to containers, roles, scheduling, and more.

   .. grid-item-card:: API Reference
      :link: api_ref/index
      :link-type: doc
      :text-align: center
      :shadow: sm

      Complete reference for every public function and class.

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Getting started

   installation
   getting_started

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Tutorials

   tutorial
   simulation-ev-tutorial

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: User guide

   agents-container
   message exchange
   transactions
   role-api
   scheduling
   topology
   simulation
   codecs

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Reference & contributing

   api_ref/index
   migration
   development
   privacy
   legals
   datenschutz


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
