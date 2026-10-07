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

Installation
------------

structlog is an optional dependency, installed with the ``trace`` extra:

.. code-block:: bash

    pip install "mango-agents[trace]"     # with pip
    uv add "mango-agents[trace]"          # in a project managed with uv
    uv sync --extra trace                 # when working on mango itself


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
        configure_structlog,
        create_tcp_container,
        disable_tracing,
        enable_tracing,
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
        async with activate(container):
            await asyncio.sleep(0.1)


    configure_structlog("trace.jsonl")  # where structlog writes to
    enable_tracing()                    # let mango log its records
    asyncio.run(main())
    disable_tracing()

Only the last four lines are specific to tracing. ``configure_structlog`` sets
up structlog to write JSON lines to ``trace.jsonl``; ``enable_tracing`` makes
mango log its records through it.

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

    disable_tracing()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    os.chdir(_tracing_cwd)
    _tracing_tmp.cleanup()

Viewing a trace
---------------

The ``mango-trace`` command turns a trace into a single HTML page that needs no
server or network:

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

Changing what is recorded
-------------------------

``enable_tracing`` decides what mango records:

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

Turning it off
--------------

Tracing is off unless you call :func:`mango.enable_tracing`. To stop it
during a run:

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
