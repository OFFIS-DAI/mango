import asyncio

import pytest
import structlog

from mango import (
    Agent,
    RoleAgent,
    SimpleCommunicationSimulation,
    activate,
    configure_structlog,
    create_tcp_container,
    create_world,
    disable_tracing,
    enable_tracing,
    is_tracing,
    message_topology,
    on_message,
    read_trace,
    step_simulation,
)
from mango.agent.role import Role

log = structlog.get_logger()


class Ping:
    def __init__(self, n):
        self.n = n


class Alarm:
    pass


@pytest.fixture
def trace_file(tmp_path):
    path = tmp_path / "trace.jsonl"
    configure_structlog(path)
    yield path
    disable_tracing()
    configure_structlog()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


def by_event(records, event):
    return [r for r in records if r["event"] == event]


def one(records, event, **fields):
    matches = [
        r
        for r in by_event(records, event)
        if all(r.get(k) == v for k, v in fields.items())
    ]
    assert len(matches) == 1, (event, fields, matches)
    return matches[0]


class PingAgent(Agent):
    """Answers a Ping by scheduling a task that sends the next Ping back."""

    @on_message(Ping)
    def on_ping(self, content, meta):
        log.info("user.ping", n=content.n)
        if content.n < 2:
            sender = self.world_agents[meta["sender_id"]]
            self.schedule_instant_task(self.send_message(Ping(content.n + 1), sender))


async def run_ping_pong():
    world = create_world()
    a = world.register(PingAgent(), "a")
    b = world.register(PingAgent(), "b")
    a.world_agents = b.world_agents = {"a": a.addr, "b": b.addr}
    async with world:
        await world.send_message(Ping(1), receiver_addr=b.addr, sender_id="a")
        for _ in range(4):
            await step_simulation(world, step_size_s=1.0)


@pytest.mark.asyncio
async def test_message_task_message_chain_is_linked(trace_file):
    enable_tracing()
    await run_ping_pong()
    disable_tracing()
    records = read_trace(trace_file)

    # b receives Ping(1); its handler logs and schedules the answering task
    received_b = one(records, "message.received", agent="b")
    handler = one(records, "handler.called", cause=received_b["id"])
    assert handler["handler"] == "PingAgent.on_ping"
    user_b = one(records, "user.ping", agent="b")
    assert user_b["cause"] == received_b["id"]
    assert user_b["n"] == 1
    assert "sim_time" in user_b

    task = one(records, "task.scheduled", agent="b")
    assert task["cause"] == received_b["id"]
    assert task["task_type"] == "InstantScheduledTask"

    # the task sends Ping(2) to a, whose receipt points at that send
    sent = one(records, "message.sent", cause=task["id"])
    assert sent["content"] == {"type": "Ping"}
    received_a = one(records, "message.received", agent="a")
    assert received_a["cause"] == sent["id"]
    assert one(records, "user.ping", agent="a")["cause"] == received_a["id"]

    assert one(records, "task.finished", id=task["id"])


@pytest.mark.asyncio
async def test_disabled_tracing_records_only_user_logs(trace_file):
    assert not is_tracing()
    await run_ping_pong()
    records = read_trace(trace_file)

    assert [r["event"] for r in records] == ["user.ping", "user.ping"]
    assert all("cause" not in r for r in records)


@pytest.mark.asyncio
async def test_categories_filter_records(trace_file):
    enable_tracing(categories={"message"})
    await run_ping_pong()
    disable_tracing()
    records = read_trace(trace_file)

    assert {r.get("category") for r in records} == {"message", None}
    # context is still bound for user logs
    assert all(
        r["cause"].startswith("message-") for r in by_event(records, "user.ping")
    )


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        enable_tracing(categories={"messages"})


@pytest.mark.asyncio
async def test_include_content(trace_file):
    enable_tracing(categories={"message"}, include_content=True)
    await run_ping_pong()
    disable_tracing()

    sent = by_event(read_trace(trace_file), "message.sent")[0]
    assert sent["content"]["type"] == "Ping"
    assert "Ping object" in sent["content"]["repr"]


@pytest.mark.asyncio
async def test_failing_task_is_recorded(trace_file):
    class FailingAgent(Agent):
        def handle_message(self, content, meta):
            pass

        async def fail(self):
            raise RuntimeError("bad step")

    enable_tracing()
    world = create_world()
    agent = world.register(FailingAgent())
    async with world:
        agent.schedule_instant_task(agent.fail())
        await step_simulation(world, step_size_s=1.0)
    disable_tracing()

    failed = by_event(read_trace(trace_file), "task.failed")
    assert len(failed) == 1
    assert "bad step" in failed[0]["error"]
    assert failed[0]["agent"] == agent.aid
    assert failed[0]["level"] == "error"


@pytest.mark.asyncio
async def test_periodic_task_cycles_and_waits(trace_file):
    class Periodic(Agent):
        def handle_message(self, content, meta):
            pass

        def on_ready(self):
            self.schedule_periodic_task(self.tick, delay=1.0)

        async def tick(self):
            log.info("user.tick")

    enable_tracing()
    world = create_world()
    world.register(Periodic())
    async with world:
        for _ in range(3):
            await step_simulation(world, step_size_s=1.0)
    disable_tracing()
    records = read_trace(trace_file)

    scheduled = one(records, "task.scheduled", task_type="PeriodicScheduledTask")
    assert scheduled["waits_for"] == "period"
    assert scheduled["delay"] == 1.0
    cycles = [r["cycle"] for r in by_event(records, "task.cycle")]
    assert cycles[:3] == [1, 2, 3]
    assert by_event(records, "task.waiting")
    ticks = by_event(records, "user.tick")
    assert ticks and all(t["cause"] == scheduled["id"] for t in ticks)


@pytest.mark.asyncio
async def test_global_and_agent_events(trace_file):
    class Listener(Agent):
        def handle_message(self, content, meta):
            pass

        def on_global_event(self, event):
            log.info("user.global", got=type(event).__name__)

        def on_agent_event(self, event):
            log.info("user.targeted")

    enable_tracing()
    world = create_world()
    l1 = world.register(Listener())
    l2 = world.register(Listener())
    world.environment.install(l1, agent_id=l1.aid)
    async with world:
        world.environment.emit_global_event(Alarm())
        world.environment.emit_agent_event(Alarm(), l1.aid)
    disable_tracing()
    records = read_trace(trace_file)

    emitted = by_event(records, "event.emitted")
    assert len(emitted) == 2
    global_id = emitted[0]["id"]
    received = by_event(records, "global_event.received")
    assert {r["agent"] for r in received} == {l1.aid, l2.aid}
    assert all(r["cause"] == global_id for r in received)
    for r in received:
        user = one(records, "user.global", cause=r["id"])
        assert user["agent"] == r["agent"]

    targeted = one(records, "agent_event.received")
    assert targeted["cause"] == emitted[1]["id"]
    assert emitted[1]["target"] == l1.aid
    assert one(records, "user.targeted")["cause"] == targeted["id"]


@pytest.mark.asyncio
async def test_role_event_inside_message_trigger(trace_file):
    class Emitter(Role):
        def setup(self):
            self.context.subscribe_message(
                self, self.on_ping, lambda c, m: isinstance(c, Ping)
            )

        def on_ping(self, content, meta):
            self.context.emit_event(Alarm(), self)

    class Listener(Role):
        def setup(self):
            self.context.subscribe_event(self, Alarm, self.on_alarm)

        def on_alarm(self, event, source):
            log.info("user.alarm")

    enable_tracing()
    world = create_world()
    agent = world.register(RoleAgent())
    agent.add_role(Emitter())
    agent.add_role(Listener())
    async with world:
        await world.send_message(Ping(1), receiver_addr=agent.addr)
        await step_simulation(world, step_size_s=1.0)
    disable_tracing()
    records = read_trace(trace_file)

    message = one(records, "message.received")
    role_event = one(records, "role_event.received")
    assert role_event["cause"] == message["id"]
    assert role_event["source"].endswith("Emitter")
    assert one(records, "user.alarm")["cause"] == role_event["id"]


@pytest.mark.asyncio
async def test_world_start_is_recorded(trace_file):
    enable_tracing(categories={"run"})
    world = create_world()
    world.register(PingAgent(), "a")
    async with world:
        await asyncio.sleep(0)
    disable_tracing()
    records = read_trace(trace_file)

    assert records[0]["event"] == "trace.started"
    started = one(records, "world.started")
    assert started["environment"] == "DefaultEnvironment"
    assert started["agents"] == ["a"]
    assert records[-1]["event"] == "trace.stopped"


class Sender(Agent):
    def __init__(self, peer):
        super().__init__()
        self.peer = peer

    def on_ready(self):
        self.schedule_instant_message(Ping(1), self.peer)


async def run_in_tcp_container(receiver: Agent):
    """Let a Sender send one Ping to *receiver* in a TCP container."""
    container = create_tcp_container(addr=("127.0.0.1", 5555))
    container.register(receiver, "receiver")
    container.register(Sender(receiver.addr), "sender")
    async with activate(container):
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_tcp_container_trace(trace_file):
    class Receiver(Agent):
        @on_message(Ping)
        def on_ping(self, content, meta):
            log.info("user.ping", n=content.n)

    enable_tracing()
    await run_in_tcp_container(Receiver())
    disable_tracing()
    records = read_trace(trace_file)

    started = one(records, "container.started")
    assert started["container"] == "TCPContainer"
    assert started["addr"] == "('127.0.0.1', 5555)"
    assert started["codec"] == "JSON"
    assert started["agents"] == ["receiver", "sender"]

    sent = one(records, "message.sent")
    assert sent["sender"] == "sender"
    assert sent["receiver_id"] == "receiver"
    assert one(records, "task.scheduled", agent="sender")["id"] == sent["cause"]
    received = one(records, "message.received", cause=sent["id"])
    assert received["agent"] == "receiver"
    assert one(records, "user.ping")["cause"] == received["id"]


@pytest.mark.asyncio
async def test_failing_message_handler_is_recorded(trace_file):
    # A failing sync handler ends the agent's inbox loop (existing mango
    # behaviour), so this runs in a TCP container: stepping a simulation
    # world would wait for the inbox forever.
    class BrokenReceiver(Agent):
        @on_message(Ping)
        def on_ping(self, content, meta):
            raise ValueError(f"unexpected ping {content.n}")

    enable_tracing()
    await run_in_tcp_container(BrokenReceiver())
    disable_tracing()
    records = read_trace(trace_file)

    failed = one(records, "message.failed")
    assert failed["level"] == "error"
    assert failed["error"] == "ValueError('unexpected ping 1')"
    assert failed["agent"] == "receiver"
    received = one(records, "message.received", id=failed["id"])
    assert received["content"] == {"type": "Ping"}
    # all other mango records are debug
    others = [r for r in records if "category" in r and r is not failed]
    assert {r["level"] for r in others} == {"debug"}


@pytest.mark.asyncio
async def test_message_topology_is_directed(trace_file):
    enable_tracing()
    await run_ping_pong()
    disable_tracing()

    # the first Ping is sent by the world, so only b -> a is a traced send
    topology = message_topology(trace_file)

    assert set(topology.nodes) == {"a", "b"}
    assert list(topology.edges) == [("b", "a")]
    edge = topology.edges["b", "a"]
    assert edge["messages"] == edge["received"] == 1
    assert edge["types"] == {"Ping": 1}
    assert edge["first"] == edge["last"]


@pytest.mark.asyncio
async def test_message_topology_keeps_lost_messages(trace_file):
    class Silent(Agent):
        def handle_message(self, content, meta):
            pass

    enable_tracing(categories={"message"})
    world = create_world(
        communication_sim=SimpleCommunicationSimulation(loss_percent=1.0)
    )
    receiver = world.register(Silent(), "receiver")
    world.register(Sender(receiver.addr), "sender")
    async with world:
        await step_simulation(world, step_size_s=1.0)
    disable_tracing()

    topology = message_topology(read_trace(trace_file))

    assert set(topology.nodes) == {"receiver", "sender"}
    edge = topology.edges["sender", "receiver"]
    assert (edge["messages"], edge["received"]) == (1, 0)
