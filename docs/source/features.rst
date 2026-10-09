.. _features-docs:

================
Feature overview
================

mango is a small set of parts that fit together: agents, which can be composed
of roles, talk through messages along a topology and run tasks on a clock;
containers or a simulation world run them, and tracing observes all of it.
Start from one of the parts below or from
:ref:`what you want to do <features-goals>`, and follow the cards into the
guide.

.. raw:: html
   :file: _static/features/feature-map.svg
   :class: mango-feature-map-frame

.. only:: html

   .. div:: sd-text-center mango-feature-map-caption

      Each part of the map links to its features below.

   .. container:: mango-feature-jump

      * :ref:`Agents <features-agents>`
      * :ref:`Roles <features-roles>`
      * :ref:`Messaging <features-messaging>`
      * :ref:`Topologies <features-topologies>`
      * :ref:`Scheduling <features-scheduling>`
      * :ref:`Containers <features-containers>`
      * :ref:`Simulation <features-simulation>`
      * :ref:`Tracing <features-tracing>`

.. only:: latex

   mango consists of eight parts, each described in one of the sections below:

   * :ref:`features-agents`: autonomous actors with addresses, lifecycle and
     handlers.
   * :ref:`features-roles`: compose agent behaviour from reusable parts.
   * :ref:`features-messaging`: from one-off sends to multi-hop conversations.
   * :ref:`features-topologies`: who talks to whom, as a graph.
   * :ref:`features-scheduling`: proactive tasks on real or simulated time.
   * :ref:`features-containers`: host agents, encode and transport their
     messages.
   * :ref:`features-simulation`: deterministic time, simulated network,
     recorded results.
   * :ref:`features-tracing`: follow every message, event and task back to its
     cause.


.. _features-goals:

Find by goal
============

Know what you want to do, but not where it lives? Pick the closest goal.

.. list-table::
   :class: mango-goal-table
   :widths: 60 40
   :header-rows: 1

   * - I want to …
     - Start with
   * - :ref:`get a multi-agent system running in a few lines <express-setup>`
     - ``run_with_tcp`` · ``run_with_mqtt``
   * - :ref:`split an agent into reusable building blocks <role-api-role-class>`
     - ``Role`` · ``agent_composed_of``
   * - :ref:`ask many agents and wait for a quorum <transactions-gather>`
     - ``gather``
   * - :ref:`run a negotiation or auction over many messages <transactions-conversations>`
     - ``open_conversation``
   * - :ref:`give each agent its neighbours without wiring addresses <topology-building>`
     - ``create_topology``
   * - :ref:`route around neighbours that went silent <topology-link-health>`
     - ``EdgeHealth`` · ``live_neighbours``
   * - :ref:`do something every few seconds <role-periodic>`
     - ``@periodic``
   * - :ref:`run timed tasks on simulated instead of wall-clock time <ClockDocs>`
     - ``ExternalClock``
   * - :ref:`run agents on several machines <container-docs>`
     - ``create_tcp_container``
   * - :ref:`couple mango to an external simulator <agents-container-container-types>`
     - ``create_ec_container``
   * - :ref:`use every CPU core for heavy agents <agent-process>`
     - ``as_agent_process``
   * - :ref:`send my own classes between containers <codecs-json>`
     - ``@json_serializable``
   * - :ref:`simulate hours of agent time in seconds <simulation-discrete-event>`
     - ``discrete_step_until``
   * - :ref:`model message delays and packet loss <simulation-communication>`
     - ``SimpleCommunicationSimulation``
   * - :ref:`add handlers to agents without changing their classes <simulation-behavior-in>`
     - ``behavior_in``
   * - :ref:`record values over time and plot them <simulation-recording>`
     - ``record_agent`` · ``plot_agents``
   * - :ref:`find out why a message never arrived <tracing-message-topology>`
     - ``message_topology``
   * - :ref:`find the handler that raised an exception <tracing-failing-step>`
     - ``message.failed`` · ``task.failed``


.. _features-agents:

Agents
======

An agent is an autonomous actor with its own address, inbox and lifecycle. You
subclass ``Agent``, declare typed message handlers and register it in a
container or a simulation world.

**Guide:** :doc:`agents-container`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Agents and agent IDs
      :link: agent-docs
      :link-type: ref
      :shadow: sm

      Subclass ``Agent`` and register it with a container, which gives it a
      unique agent ID, its ``aid``.
      +++
      ``Agent`` · ``register``

   .. grid-item-card:: Lifecycle callbacks
      :link: agents-container-lifecycle
      :link-type: ref
      :shadow: sm

      Set up, send first messages and clean up at the right moment, from
      registration to shutdown.
      +++
      ``on_register`` · ``on_ready`` · ``on_stop``

   .. grid-item-card:: Typed message handlers
      :link: agent-handlers
      :link-type: ref
      :shadow: sm

      One handler per message type, sync or async, narrowed with a predicate
      and ordered by priority.
      +++
      ``@on_message`` · ``handle_message``

   .. grid-item-card:: Names, colours and categories
      :link: simulation-agent-description
      :link-type: ref
      :shadow: sm

      Give agents a readable name, colour and category; the name labels plots,
      and ``behavior_in`` can match agents by name or colour.
      +++
      ``AgentDescription`` · ``update_description``


.. _features-roles:

Roles
=====

Roles split an agent into small, reusable responsibilities that share its
inbox, scheduler and context. They coordinate through events and shared models,
and can be added, removed or paused at runtime.

**Guide:** :doc:`role-api`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Compose agents from roles
      :link: role-api-role-class
      :link-type: ref
      :shadow: sm

      Split behaviour into small ``Role`` classes, each with its own lifecycle,
      and combine them into one agent.
      +++
      ``RoleAgent`` · ``agent_composed_of``

   .. grid-item-card:: Declarative wiring
      :link: role-decorators
      :link-type: ref
      :shadow: sm

      Declare message, event and timer handlers with decorators instead of
      subscribing by hand.
      +++
      ``@on_message`` · ``@on_event`` · ``@periodic``

   .. grid-item-card:: Role message handling
      :link: role-api-handling-messages
      :link-type: ref
      :shadow: sm

      Route messages to roles by type, predicate and priority, gate them with
      preprocessors and observe what is sent.
      +++
      ``subscribe_message`` · ``subscribe_send``

   .. grid-item-card:: Inter-role events
      :link: inter-role-events
      :link-type: ref
      :shadow: sm

      Let the roles of one agent signal each other with typed, in-process
      events instead of messages.
      +++
      ``emit_event`` · ``subscribe_event``

   .. grid-item-card:: Shared data and models
      :link: role-api-sharing-data
      :link-type: ref
      :shadow: sm

      Share state between roles, or use typed models that notify every
      subscribed role on update.
      +++
      ``get_or_create_model`` · ``subscribe_model``

   .. grid-item-card:: Change roles at runtime
      :link: role-api-deactivation
      :link-type: ref
      :shadow: sm

      Pause a role, with its handlers and scheduled tasks, and resume it later;
      add and remove roles while the agent runs.
      +++
      ``context.deactivate`` · ``context.add_role``


.. _features-messaging:

Messaging & conversations
=========================

All communication uses one send path, from a single ``send_message`` up to
request/response, quorum-based ``gather`` and multi-hop conversations. Messages
inside one container stay in-process; forwarding rules and FIPA ACL cover
proxies and interoperability.

**Guide:** :doc:`message exchange` · :doc:`transactions`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Send to one or many
      :link: message-exchange-sending
      :link-type: ref
      :shadow: sm

      Send any serialisable object to one agent or a list of agents, also from
      synchronous callbacks.
      +++
      ``send_message`` · ``send_messages``

   .. grid-item-card:: Replies and request/response
      :link: message-exchange-tracked
      :link-type: ref
      :shadow: sm

      Answer the sender without looking up its address, or run a callback when
      the reply to your request arrives.
      +++
      ``reply_to`` · ``send_tracked_message``

   .. grid-item-card:: Gather with quorum and timeout
      :link: transactions-gather
      :link-type: ref
      :shadow: sm

      Ask many peers in one call; return once a quorum answers, or with partial
      results on timeout.
      +++
      ``gather``

   .. grid-item-card:: Multi-hop conversations
      :link: transactions-conversations
      :link-type: ref
      :shadow: sm

      Run negotiations, auctions or gossip as one conversation and iterate over
      its replies as they arrive.
      +++
      ``open_conversation`` · ``join_conversation``

   .. grid-item-card:: Routing and forwarding rules
      :link: message-exchange-routing
      :link-type: ref
      :shadow: sm

      Messages within a container skip codec and network; forwarding rules turn
      an agent into a proxy.
      +++
      ``add_forwarding_rule`` · ``forward_replies``

   .. grid-item-card:: FIPA ACL messages
      :link: message-exchange-acl
      :link-type: ref
      :shadow: sm

      Wrap content in a FIPA ACL envelope for interoperability with other agent
      platforms.
      +++
      ``create_acl`` · ``Performatives``


.. _features-topologies:

Topologies
==========

A topology is a ``networkx`` graph laid over your agents that tells each one
who its neighbours are, across container borders. It carries no messages
itself: agents use it to address and broadcast to their neighbours, to follow
link changes and to tell live neighbours from silent ones.

**Guide:** :doc:`topology`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Build a topology
      :link: topology-building
      :link-type: ref
      :shadow: sm

      Describe who talks to whom as a graph and have each agent's neighbour
      addresses injected for you.
      +++
      ``create_topology`` · ``add_edge``

   .. grid-item-card:: Ready-made graph shapes
      :link: topology-shapes
      :link-type: ref
      :shadow: sm

      Create a complete, star, ring or any networkx-graph topology in one call.
      +++
      ``complete_topology`` · ``graph_topology``

   .. grid-item-card:: Assign agents to nodes
      :link: topology-assigning-agents
      :link-type: ref
      :shadow: sm

      Fill nodes one by one, round-robin or by predicate; agents on one node
      form a local cluster.
      +++
      ``per_node`` · ``auto_assign``

   .. grid-item-card:: Query neighbours
      :link: topology-neighbours
      :link-type: ref
      :shadow: sm

      List neighbours from an agent or any of its roles, filtered by link
      state, characteristic or predicate.
      +++
      ``neighbors`` · ``topology_neighbors``

   .. grid-item-card:: Broadcast to neighbours
      :link: topology-broadcast
      :link-type: ref
      :shadow: sm

      Send one message to all, or a filtered set of, neighbours with a single
      awaited call.
      +++
      ``broadcast_to_neighbors``

   .. grid-item-card:: Link states and live changes
      :link: topology-link-states
      :link-type: ref
      :shadow: sm

      Mark links as normal, inactive or broken and change the graph while the
      agents run.
      +++
      ``State`` · ``modify_topology``

   .. grid-item-card:: Link health
      :link: topology-link-health
      :link-type: ref
      :shadow: sm

      Score every neighbour by how recently it was heard from, and send only to
      the live ones.
      +++
      ``EdgeHealth`` · ``live_neighbours``

   .. grid-item-card:: Connect topologies
      :link: topology-connecting
      :link-type: ref
      :shadow: sm

      Link independent topologies, such as regions, through connector agents
      without merging their graphs.
      +++
      ``connect_topologies`` · ``mark_as_connector``


.. _features-scheduling:

Scheduling & clocks
===================

Every agent has a scheduler for proactive work, from one-shot and periodic
tasks to calendar recurrences and process-pool jobs. Schedulers run on their
container's clock: wall time, or an external clock you advance yourself.

**Guide:** :doc:`scheduling`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Task types
      :link: scheduling-task-types
      :link-type: ref
      :shadow: sm

      Run work now, at a timestamp, periodically, on a calendar rule, once a
      condition holds, or after another task.
      +++
      ``schedule_recurrent_task``

   .. grid-item-card:: Periodic tasks
      :link: role-periodic
      :link-type: ref
      :shadow: sm

      Run a method at a fixed period on the agent's clock, optionally only
      while a predicate holds.
      +++
      ``@periodic``

   .. grid-item-card:: Suspendable tasks
      :link: scheduling-suspendable-tasks
      :link-type: ref
      :shadow: sm

      Tag tasks with a source, then suspend or resume all tasks of that source
      with one call.
      +++
      ``scheduler.suspend`` · ``scheduler.resume``

   .. grid-item-card:: Process-pool tasks
      :link: scheduling-process-tasks
      :link-type: ref
      :shadow: sm

      Offload CPU-bound tasks to a managed process pool so they do not block
      the event loop.
      +++
      ``schedule_periodic_process_task``

   .. grid-item-card:: External clock
      :link: clockdocs
      :link-type: ref
      :shadow: sm

      Advance time yourself, so timed and periodic tasks follow simulated time
      and run faster than real time.
      +++
      ``ExternalClock`` · ``set_time``

   .. grid-item-card:: Distributed clock
      :link: scheduling-distributed-clock
      :link-type: ref
      :shadow: sm

      Keep several containers on one simulated time that advances only when
      every container is done.
      +++
      ``DistributedClockManager``


.. _features-containers:

Containers & codecs
===================

A container hosts agents and connects them to the outside world: it routes
messages, encodes them with its codec and sends them over TCP or MQTT. You
choose where agents run: in one process, in separate OS processes, or under an
external co-simulator.

**Guide:** :doc:`agents-container` · :doc:`codecs`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: TCP, MQTT and co-simulation containers
      :link: agents-container-container-types
      :link-type: ref
      :shadow: sm

      Host agents behind TCP sockets or an MQTT broker, or let an external
      simulator drive their time and messages.
      +++
      ``create_tcp_container``

   .. grid-item-card:: Start and stop containers
      :link: agents-container-starting-stopping
      :link-type: ref
      :shadow: sm

      Start several containers together and shut every agent down cleanly, even
      after an exception.
      +++
      ``activate``

   .. grid-item-card:: Express setup
      :link: express-setup
      :link-type: ref
      :shadow: sm

      Create containers, spread your agents across them and run everything with
      a single call.
      +++
      ``run_with_tcp`` · ``run_with_mqtt``

   .. grid-item-card:: Agent processes
      :link: agent-process
      :link-type: ref
      :shadow: sm

      Run CPU-heavy agents in their own OS processes, past the GIL; they keep
      messaging like any other agent.
      +++
      ``as_agent_process``

   .. grid-item-card:: Custom types with the JSON codec
      :link: codecs-json
      :link-type: ref
      :shadow: sm

      Send your own classes between containers with a decorator or a custom
      serialiser, with optional fixed type ids.
      +++
      ``@json_serializable`` · ``add_serializer``

   .. grid-item-card:: Protobuf codec
      :link: codecs-protobuf
      :link-type: ref
      :shadow: sm

      A compact, schema-first wire format, including protobuf payloads inside
      ACL messages.
      +++
      ``PROTOBUF`` · ``add_serializer``


.. _features-simulation:

Simulation world
================

The simulation world replaces containers and the network with one in-process
world on a clock you control. It models message delay and loss, provides a 2-D
environment and records data after every step, while your agent and role
classes stay unchanged.

**Guide:** :doc:`simulation` · :doc:`simulation-ev-tutorial`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Fixed-step simulation
      :link: simulation-fixed-step
      :link-type: ref
      :shadow: sm

      Run all agents in one in-process world and advance its clock in fixed
      steps, with an ``on_step`` hook.
      +++
      ``create_world`` · ``step_simulation``

   .. grid-item-card:: Discrete-event stepping
      :link: simulation-discrete-event
      :link-type: ref
      :shadow: sm

      Jump straight to the next message arrival or task wake-up instead of
      ticking through idle time.
      +++
      ``discrete_step_until``

   .. grid-item-card:: Express simulation setup
      :link: simulation-express
      :link-type: ref
      :shadow: sm

      Create a world, register your agents and start them with one call, the
      same way ``run_with_tcp`` sets up containers.
      +++
      ``run_with_simulation``

   .. grid-item-card:: Message delay and loss
      :link: simulation-communication
      :link-type: ref
      :shadow: sm

      Model fixed, per-link, random or graph-distance-based delays and packet
      loss.
      +++
      ``SimpleCommunicationSimulation``

   .. grid-item-card:: Spatial environment and events
      :link: simulation-spatial-environment
      :link-type: ref
      :shadow: sm

      Place and move agents in a 2-D area, query distances, and send global or
      per-agent events.
      +++
      ``DefaultEnvironment`` · ``Area2D``

   .. grid-item-card:: Attach behaviour from outside
      :link: simulation-behavior-in
      :link-type: ref
      :shadow: sm

      Add message and event handlers to agents matched by type, role, name or
      colour, without touching their classes.
      +++
      ``behavior_in``

   .. grid-item-card:: Data recording
      :link: simulation-recording
      :link-type: ref
      :shadow: sm

      Record world values, agent values and positions automatically after every
      step.
      +++
      ``record_world`` · ``record_agent``

   .. grid-item-card:: Plots and message timelines
      :link: simulation-visualization
      :link-type: ref
      :shadow: sm

      Plot recordings with matplotlib, or draw a timeline of who messaged whom
      and when.
      +++
      ``plot_agents`` · ``show_communication_data``


.. _features-tracing:

Tracing & debugging
===================

Tracing observes the whole system, in real time and in simulation: every
message, event and scheduled task, linked to the work that caused it. A single
``trace=`` argument writes the trace and an HTML viewer to explore it. Tracing
uses structlog, which is installed with mango.

**Guide:** :doc:`tracing`

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Cause-chain tracing
      :link: tracing-docs
      :link-type: ref
      :shadow: sm

      Record every message, event and scheduled task together with the work
      that caused it.
      +++
      ``cause`` · ``read_trace``

   .. grid-item-card:: Trace a run with one argument
      :link: tracing-run
      :link-type: ref
      :shadow: sm

      One argument on ``activate``, ``create_world`` or a ``run_with_*`` helper
      writes the trace and its viewer.
      +++
      ``trace=True`` · ``TraceConfig``

   .. grid-item-card:: Interactive trace viewer
      :link: tracing-viewer
      :link-type: ref
      :shadow: sm

      Filter records, highlight cause chains and follow agents side by side, in
      one self-contained HTML file.
      +++
      ``mango-trace`` · ``trace_viewer.write_html``

   .. grid-item-card:: Observed message topology
      :link: tracing-message-topology
      :link-type: ref
      :shadow: sm

      See who actually messaged whom, with lost messages marked, and compare it
      with your topology.
      +++
      ``message_topology``

   .. grid-item-card:: Find the failing step
      :link: tracing-failing-step
      :link-type: ref
      :shadow: sm

      Go from an error to the handler or task that raised it and the message
      that started it.
      +++
      ``message.failed`` · ``task.failed``

   .. grid-item-card:: Your own logs in the trace
      :link: tracing-own-logging
      :link-type: ref
      :shadow: sm

      Log with structlog in handlers and tasks; your lines get the agent, the
      cause and the clock time (simulated time in a simulation).
      +++
      ``structlog.get_logger``

   .. grid-item-card:: Choose what is recorded
      :link: tracing-changing-recorded
      :link-type: ref
      :shadow: sm

      Record only some categories, add a short repr of message contents, and
      choose where and how records are written.
      +++
      ``enable_tracing`` · ``configure_structlog``

   .. grid-item-card:: Trace only part of a run
      :link: tracing-turning-off
      :link-type: ref
      :shadow: sm

      Trace one block of code with a context manager, or stop tracing while the
      program keeps running.
      +++
      ``trace_session`` · ``disable_tracing``


Where to go next
================

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: Getting started
      :link: getting_started
      :link-type: doc
      :text-align: center
      :shadow: sm

      Install mango and write your first agent in minutes.

   .. grid-item-card:: Solar agents tutorial
      :link: tutorial
      :link-type: doc
      :text-align: center
      :shadow: sm

      Two solar plants and a controller: containers, messages, codecs,
      scheduling and roles, step by step.

   .. grid-item-card:: EV simulation tutorial
      :link: simulation-ev-tutorial
      :link-type: doc
      :text-align: center
      :shadow: sm

      Electric vehicles in a simulation world: space, movement, messaging
      and recorded results.

   .. grid-item-card:: API Reference
      :link: api_ref/index
      :link-type: doc
      :text-align: center
      :shadow: sm

      Complete reference for every public function and class.
