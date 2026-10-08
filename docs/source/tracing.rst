.. _tracing-docs:

====================================
Tracing and debugging with structlog
====================================

When an agent algorithm misbehaves, the hard question is usually *why* a piece
of code ran: which message, event or task started it, and what happened before
that. mango can answer this with a **trace**: a structured log of how work
flows through your agents, written with `structlog <https://www.structlog.org>`_.

Every piece of work in a mango agent is started by one of three triggers:

* a **message** arriving in the agent's inbox,
* an **event**: a role event emitted with ``emit_event``, or a global or
  targeted event of the :doc:`simulation world <simulation>`,
* a **scheduled task** becoming due.

With tracing enabled, mango logs every step of these triggers and of the tasks
they start. Each record has an ``id`` and a ``cause``: the id of the trigger or
task during which it happened. Following the ``cause`` links leads from any
record back to the start of the chain. Your own structlog calls inside handlers
and tasks automatically get the same ``agent`` and ``cause`` fields, so they
appear in the same trace, in line with mango's records.

The trace records the *process*, not your data: message contents and events
are logged by type only (see `Changing what is recorded`_). To save content,
such as agent states or values over time, use the separate recording functions
of the simulation world (:ref:`simulation-recording`).

Example
-------

Two agents play ping-pong, and each of the three triggers occurs once:

1. The ``Pinger`` schedules a **task** that sends a ``Ping``.
2. The ``PongRole`` handles the ``Ping`` **message** and emits a ``PingCounted``
   **event** on its agent.
3. The event handler answers with a ``Pong``, which the ``Pinger`` handles.

Each step also writes its own log line with ``log.info(...)``.

.. testsetup:: tracing

    import os
    import tempfile

    _tracing_cwd = os.getcwd()
    _tracing_tmp = tempfile.TemporaryDirectory()
    os.chdir(_tracing_tmp.name)

.. testcode:: tracing

    import asyncio
    from dataclasses import dataclass

    import structlog

    from mango import (
        Agent,
        AgentAddress,
        Role,
        activate,
        agent_composed_of,
        create_tcp_container,
        on_event,
        on_message,
        sender_addr,
    )

    log = structlog.get_logger()


    @dataclass
    class Ping:
        number: int


    @dataclass
    class Pong:
        number: int


    @dataclass
    class PingCounted:
        number: int
        reply_to: AgentAddress


    class Pinger(Agent):
        def __init__(self, peer):
            super().__init__()
            self.peer = peer

        def on_ready(self):
            # trigger 1: a scheduled task
            self.schedule_instant_task(self.send_ping(1))

        async def send_ping(self, number):
            log.info("ping.sending", number=number)
            await self.send_message(Ping(number), self.peer)

        @on_message(Pong)
        def handle_pong(self, content, meta):
            log.info("pong.received", number=content.number)


    class PongRole(Role):
        @on_message(Ping)
        def handle_ping(self, content, meta):
            # trigger 2: a message
            log.info("ping.received", number=content.number)
            self.context.emit_event(PingCounted(content.number, sender_addr(meta)), self)

        @on_event(PingCounted)
        def answer(self, event, source):
            # trigger 3: an event
            log.info("ping.counted", number=event.number)
            self.context.schedule_instant_message(Pong(event.number), event.reply_to)


    async def main():
        container = create_tcp_container(addr=("127.0.0.1", 5555))
        ponger = container.register(agent_composed_of(PongRole()))
        container.register(Pinger(ponger.addr))
        async with activate(container, trace="trace.jsonl"):
            await asyncio.sleep(0.1)


    asyncio.run(main())

Only ``trace="trace.jsonl"`` is specific to tracing: mango records the run to
``trace.jsonl`` while the container is active. When the container shuts down,
mango writes the viewer ``trace.html`` next to it (see `Viewing a trace`_) and
prints where both files are.

The trace now contains one line per record. This is the line your ``answer``
handler wrote (shortened):

.. code-block:: text

    {"event": "ping.counted", "number": 1, "agent": "agent0", "cause": "role_event-4",
     "sim_time": 1791385427.47, "level": "info", "timestamp": "2026-10-07T15:03:47.470897Z"}

Its ``cause`` is the record mango wrote when the ``PingCounted`` event reached
the agent:

.. code-block:: text

    {"event": "role_event.received", "category": "event", "id": "role_event-4",
     "cause": "message-3", "agent": "agent0", "content": {"type": "PingCounted"},
     "source": "PongRole", ...}

Following ``cause`` from record to record answers the question "why did this
run?". This walks back from the ``Pinger``'s last log line:

.. testcode:: tracing

    from mango import read_trace

    records = read_trace("trace.jsonl")
    first = {}  # first record of every id
    for r in records:
        if "id" in r:
            first.setdefault(r["id"], r)

    record = next(r for r in records if r["event"] == "pong.received")
    while record is not None:
        print(f"{record['event']:<20} {record.get('agent')}")
        record = first.get(record.get("cause"))

.. testoutput:: tracing

    pong.received        agent1
    message.received     agent1
    message.sent         agent0
    task.scheduled       agent0
    role_event.received  agent0
    message.received     agent0
    message.sent         agent1
    task.scheduled       agent1

Read from the bottom up: the ``Pinger`` (``agent1``) scheduled a task that sent
the ``Ping``; ``agent0`` received it, which raised the role event, whose
handler scheduled the task sending the ``Pong``.

.. testcleanup:: tracing

    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    os.chdir(_tracing_cwd)
    _tracing_tmp.cleanup()

.. _tracing-run:

Tracing a run
-------------

``trace`` works the same for containers and for simulations:

.. code-block:: python

    async with activate(container, trace=True):        # containers
        ...

    async with create_world(trace=True) as world:      # a simulation world
        await world.step_until(3600)

    async with run_with_tcp(2, agent_a, agent_b, trace=True):  # the run_with_* helpers
        ...

* ``trace=True`` writes ``mango_trace.jsonl`` and the viewer
  ``mango_trace.html`` to the current directory, ``trace="run.jsonl"`` chooses
  the file (the viewer is written next to it), and a
  :class:`~mango.util.tracing.TraceConfig` sets everything else. Each run
  overwrites its files.
* Tracing starts before the containers start (in a world, before the agents'
  ``on_ready``) and ends when they shut down, also when the run fails.
* What mango records depends on the clock. With simulated time (a world, or
  containers with an ``ExternalClock``) it records all categories. In real
  time it records all but ``wait``
  (:data:`~mango.util.tracing.REAL_TIME_CATEGORIES`): those records come with
  every timer tick, add overhead that changes real timings, and matter mostly
  for simulations.
* During the run, structlog writes to the trace file. Afterwards your previous
  structlog configuration is back.

.. code-block:: python

    from mango import TraceConfig

    trace = TraceConfig(
        "high-load.jsonl",
        open_browser=True,                # open the viewer when the run ends
        categories={"message", "event"},  # see Changing what is recorded
        include_content=True,
    )
    async with activate(container, trace=trace):
        ...

``TraceConfig`` also takes the viewer's ``title`` and extra structlog
``processors``, and ``html=False`` skips the viewer. To trace only part of a
run, or into your own structlog configuration, use the building blocks that
``trace`` combines: :func:`~mango.enable_tracing` and
:func:`~mango.configure_structlog` (see `Changing what is recorded`_) and the
``mango-trace`` command.

.. _tracing-viewer:

Viewing a trace
---------------

A run traced with ``trace`` writes the viewer itself. The ``mango-trace``
command turns any trace into the same single HTML page, which needs no server
or network:

.. code-block:: bash

    mango-trace trace.jsonl           # writes trace.html next to the trace
    mango-trace trace.jsonl --open    # and opens it in the browser

``trace.html`` is an ordinary file: without ``--open``, open it in any
browser, for example by double-clicking it or dragging it into a browser window.
The page loads no scripts or data from elsewhere; it only uses the IBM Plex
fonts from Google Fonts when online and falls back to system fonts otherwise.

.. note::

   Under WSL, ``--open`` often finds no browser, because it looks for one
   inside Linux. To open the page in the Windows browser, run

   .. code-block:: bash

       explorer.exe "$(wslpath -w trace.html)"

The page lists all records and lets you

* filter by category (Messages, Events, Tasks, Waiting, Run, Your logs) and by
  log level, by agent, by free text, or by ``field=value`` on any field,
  including the fields of your own log lines (``number=1``),
* select a record to highlight everything that started it and everything it
  led to, with the chain shown step by step in a side panel,
* jump to errors: failures recorded by mango and your own ``error`` log lines;
  a trace with an error opens with it selected.

The page is a snapshot of the trace at the time ``mango-trace`` ran; it does
not update itself. After a new run, run ``mango-trace`` again and reload the
page in the browser.

From Python, use :func:`mango.util.trace_viewer.write_html`.

Records and Lanes
~~~~~~~~~~~~~~~~~

The records are shown in one of two views; ``v`` switches between them.

**Records** is the table of all records. In a simulation its first column,
*Sim time*, shows the agents' clock (``sim_time``). The ``+ms`` column shows
the wall-clock milliseconds since the trace started, which in a simulation are
often only a few milliseconds for a whole simulated day; when the window is
narrow, it is left out in favour of the other columns.

**Lanes** follows several agents side by side, like a sequence diagram:

* every followed agent gets a lane with its records on a lifeline; records
  without an agent, such as the run records and ``event.emitted``, sit in a
  narrow *no agent* lane,
* records of one instant line up in one band across all lanes, and the bands
  run down in time order; idle stretches become a labelled gap such as
  "+4 min",
* a message runs from its ``message.sent`` across to the receiver's lane and
  down beside its lifeline into the ``message.received``, so the time it took
  shows as the drop; combs fan an event out to the agents that received it,
  and bars on the lifelines show running tasks, dotted while a task waits,
* a lost message gets a red × in the receiver's lane where it should have
  arrived; a message still in flight when the trace ended gets a short dashed
  stub,
* the selected record's cause chain is drawn as one line through the lanes,
  numbered wherever it moves on to another agent.

*Packed* places the records of one instant by cause, so an effect is always
below what caused it; *File order* shows one record per row, in the order they
were written. *Fit* squeezes all lanes into the width of the page.

Choose the agents to follow with the *Lanes* picker (``a``), with *Follow
chain* (``f``: the agents of the selection's cause chain and of what it led
to), or by clicking agents on the map. Going to a record of an agent you do not
follow, from the side panel, with ``[`` and ``]`` (cause and first effect) or
with ``e`` (next error), adds its lane, with *Undo*.

.. _tracing-message-topology:

The message topology
~~~~~~~~~~~~~~~~~~~~

Next to the records, the *Topology* map shows who sent messages to whom: a node
per agent and an arrow for every direction in which at least one message was
sent, wider for more messages. A connection that lost messages carries a red
cut. A message without a receipt counts as *in flight*, not lost, if it was
sent within one typical delivery time of the end of the trace and its receiver
received nothing after it; such connections carry a hollow ring instead.

The map and the records stay in sync:

* selecting a record highlights its agent and the numbered route of its cause
  chain, with the same numbers as in the side panel and in the lanes,
* clicking an agent filters the records to it in Records and follows it in
  Lanes; Shift+click follows it together with all its message partners,
* clicking a connection shows only that connection's messages; a chip in the
  filter bar removes the filter again, and in Lanes the two agents face each
  other,
* hovering an agent or a connection highlights its records, and while filters
  are set, connections without a matching message are drawn faint.

When many agents share a name with a number, such as ``household-1`` to
``household-50``, and their full names do not fit around the ring, the map
labels them by their number and says so in its corner (``N = household-N``).

*List* shows the same connections as a table with the numbers of messages
sent, received, lost and in flight, the content types, and the ``sim_time`` of
the first and the last message.

The map is the graph :func:`mango.message_topology
<mango.util.tracing.message_topology>` returns for the trace, with one
addition: for older traces without ``receiver_id`` the viewer also takes the
receiver from the ``aid='...'`` in the ``receiver`` field.

In Python, ``message_topology`` returns a ``networkx.DiGraph`` with the agent
ids as nodes, as :func:`mango.topology_to_aid_graph` does for a configured
topology. That makes it easy to check whether the agents talked as designed:

.. code-block:: python

    from mango import message_topology, topology_to_aid_graph

    observed = message_topology("trace.jsonl")
    for sender, receiver, edge in observed.edges(data=True):
        print(sender, "->", receiver, edge["messages"], "sent,", edge["received"], "received")

    configured = topology_to_aid_graph(topology)
    unused = [link for link in configured.edges if not observed.has_edge(*link)]

Press ``?`` for all keyboard shortcuts and a key to the marks in Lanes; with
nothing selected, the side panel shows the same key while Lanes is open. The
address of the page keeps the selected record, the view, the followed lanes and
the connection filter, so a copied link opens the same picture. The page still
loads nothing from elsewhere and works offline.

.. _tracing-failing-step:

Finding a failing step
----------------------

When a handler or task raises, mango records the failure with
``level="error"`` and the exception, under the id of the trigger or task that
failed. If ``Pinger.handle_pong`` raised a ``ValueError``, the trace would
contain:

.. code-block:: text

    {"event": "message.failed", "category": "message", "id": "message-7",
     "error": "ValueError('unexpected pong 1')", "agent": "agent1",
     "cause": "message-7", "level": "error", ...}

The ``message.received`` record with the same id shows which message caused
it, and its ``cause`` chain shows how that message came about. Failures of
scheduled tasks are recorded as ``task.failed``.

What is recorded
----------------

Every record has these fields:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Field
     - Meaning
   * - ``event``
     - What happened, e.g. ``message.received`` or the name you logged.
   * - ``category``
     - ``run``, ``message``, ``event``, ``task`` or ``wait`` for mango's
       records; missing on your own log lines.
   * - ``id``
     - Id of the trigger or task the record belongs to. All records of one
       task share its id (``task.scheduled``, ``task.started``,
       ``task.finished``, ...).
   * - ``cause``
     - Id of the trigger or task during which the record was written.
       Missing for work started outside any agent code (e.g. ``on_ready``).
   * - ``agent``
     - Id of the agent.
   * - ``sim_time``
     - The agent's clock time. Simulated time in a
       :doc:`simulation <simulation>`, Unix time otherwise.
   * - ``level``
     - Log level: ``debug`` for mango's records, ``error`` for failures, the
       level you logged with for your own lines.
   * - ``timestamp``
     - Wall-clock time (UTC), added by structlog.

mango writes these records, grouped by category:

.. list-table::
   :widths: 15 35 50
   :header-rows: 1

   * - Category
     - Records
     - Notes
   * - ``message``
     - ``message.sent``, ``message.received``, ``message.failed``,
       ``handler.called``
     - ``message.received`` names the send as its ``cause``.
       ``handler.called`` names each handler a message reaches.
   * - ``event``
     - ``event.emitted``, ``global_event.received``,
       ``agent_event.received``, ``role_event.received`` (and ``.failed``),
       ``handler.called``
     - A simulation event is logged once as ``event.emitted`` and then once
       per receiving agent.
   * - ``task``
     - ``task.scheduled``, ``task.started``, ``task.cycle``,
       ``task.finished``, ``task.failed``, ``task.cancelled``
     - ``task.scheduled`` says what the task waits for (``waits_for``: a
       timestamp, period, recurrence, condition or awaitable).
       ``task.cycle`` counts runs of periodic and recurrent tasks.
   * - ``wait``
     - ``task.waiting``, ``task.resumed``
     - A task starts and stops waiting for a timer, a condition check, or a
       reply (e.g. in ``gather``).
   * - ``run``
     - ``trace.started``, ``trace.stopped``, ``container.started``,
       ``world.started``
     - mango and Python version, container type, address and codec, clock
       type, registered agents.

.. _tracing-own-logging:

Adding your own logging
-----------------------

Log with structlog anywhere in your agents and roles:

.. code-block:: python

    import structlog

    log = structlog.get_logger()

    class Trader(Agent):
        @on_message(Offer)
        def handle_offer(self, content, meta):
            log.info("offer.received", price=content.price, accepted=content.price < 10)

Inside handlers and tasks the line gets ``agent``, ``cause`` and ``sim_time``.
Choose event names that read well in a list (``offer.received``,
``bid.rejected``) and pass values as keyword arguments, not inside the message
string, so they can be filtered on.

mango binds ``agent`` and ``cause`` with :mod:`structlog.contextvars`. Do not
bind these two keys yourself; other keys you bind with
``structlog.contextvars.bind_contextvars`` appear on mango's records too.

.. _tracing-changing-recorded:

Changing what is recorded
-------------------------

``enable_tracing`` decides what mango records; with ``trace``, the same
settings are fields of :class:`~mango.util.tracing.TraceConfig`:

.. code-block:: python

    # only messages and tasks, no events, waits or run information
    enable_tracing(categories={"message", "task"})

    # also log a shortened repr (200 characters) of message contents and events
    enable_tracing(include_content=True)

Only use ``include_content`` when the contents are safe to write to a file.

``configure_structlog`` decides where and how records are written:

.. code-block:: python

    configure_structlog()                          # to stderr instead of a file
    configure_structlog("trace.log", json_output=False)  # readable console format

    def add_run_name(logger, method_name, event_dict):
        event_dict["run"] = "high-load"
        return event_dict

    def drop_handler_calls(logger, method_name, event_dict):
        if event_dict.get("event") == "handler.called":
            raise structlog.DropEvent
        return event_dict

    # extra structlog processors, run before rendering
    configure_structlog("trace.jsonl", processors=[add_run_name, drop_handler_calls])

Only the JSON-lines output can be read with :func:`mango.read_trace` and
``mango-trace``.

``configure_structlog`` is a convenience and configures structlog for the
whole program. If your application already configures structlog, keep your
configuration and add ``merge_contextvars`` to the processors, so log lines
get ``agent`` and ``cause``, and optionally :func:`mango.util.tracing.add_sim_time`:

.. code-block:: python

    import structlog
    from mango.util.tracing import add_sim_time

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_sim_time,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
    )
    enable_tracing()

mango logs its records through ``structlog.get_logger("mango.trace")``, so a
structlog setup on top of the standard library's ``logging`` can route or
filter them by that logger name.

Impact on runtime
-----------------

**Tracing off** (the default, or after ``disable_tracing()``): every hook checks
one flag and returns, and no context is bound. In a benchmark sending 20,000
messages between two agents with empty handlers, a message took about
2.2 µs instead of 2.0 µs without the tracing code. Handlers that do real work
make this difference smaller still.

**Tracing on**: every record is rendered and written by structlog. In the same
benchmark, a message took about 50 µs, for three records (``message.sent``,
``message.received``, ``handler.called``) of about 270 bytes each. The cost
depends on the renderer and the output: JSON to a local file is the cheapest
of the options above, the console format is slower. Limiting ``categories``
reduces the number of records, and with it the cost.

Tracing on also changes timing: agents run slower, so the order in which
concurrent work interleaves can differ from an untraced run. Your own structlog
calls cost the same whether mango's tracing is on or off.

Use tracing to debug and to understand runs, not in production runs or
performance measurements.

.. _tracing-turning-off:

Turning it off
--------------

Tracing is off unless you pass ``trace`` or call
:func:`mango.enable_tracing`. To stop it during a run:

.. code-block:: python

    disable_tracing()

To trace only part of a program, use the context manager:

.. code-block:: python

    from mango import trace_session

    with trace_session(categories={"message"}):
        asyncio.run(main())

``disable_tracing`` stops mango's records only. Your own log lines are still
written according to the structlog configuration.

Because mango logs its records at ``debug`` level and failures at ``error``,
structlog's level filter also controls them. Filtering at ``INFO`` drops
mango's records but keeps your ``info`` lines and all failures; filtering at
``WARNING`` silences your ``info`` lines as well:

.. code-block:: python

    import logging
    import structlog

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

With tracing on and the records filtered out, mango still prepares each
record, so use ``disable_tracing`` to save the runtime.

Limitations
-----------

* Agents in subprocesses (:ref:`agent-process`) and process tasks run in
  other processes and are not traced (yet); their ``task.scheduled`` and
  completion are recorded in the parent process.
* While tracing is on, every message's ``meta`` carries an extra
  ``_trace_msg_id`` key that links the receipt to its send.
* Ids (``message-3``, ``task-5``) are unique within one process, not across
  separate runs.
