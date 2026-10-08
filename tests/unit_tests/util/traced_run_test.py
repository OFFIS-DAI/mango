import asyncio
from pathlib import Path

import pytest
import structlog

from mango import (
    Agent,
    TraceConfig,
    activate,
    configure_structlog,
    create_tcp_container,
    create_world,
    disable_tracing,
    enable_tracing,
    is_tracing,
    on_message,
    read_trace,
    run_with_simulation,
)
from mango.util.tracing import CATEGORIES, REAL_TIME_CATEGORIES, _trace_config

log = structlog.get_logger()


class Ping:
    pass


class Receiver(Agent):
    @on_message(Ping)
    def on_ping(self, content, meta):
        log.info("user.ping")


class Sender(Agent):
    def __init__(self, peer):
        super().__init__()
        self.peer = peer

    def on_ready(self):
        self.schedule_instant_message(Ping(), self.peer)


@pytest.fixture(autouse=True)
def clean_structlog():
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


def by_event(records, event):
    return [r for r in records if r["event"] == event]


async def ping_world(world):
    receiver = world.register(Receiver(), "receiver")
    world.register(Sender(receiver.addr), "sender")
    async with world:
        await world.step(step_size_s=1.0)


@pytest.mark.asyncio
async def test_traced_world_writes_trace_and_viewer(tmp_path, capsys):
    trace = tmp_path / "world.jsonl"

    await ping_world(create_world(trace=trace))

    records = read_trace(trace)
    assert by_event(records, "trace.started")[0]["categories"] == sorted(CATEGORIES)
    assert by_event(records, "world.started")
    assert by_event(records, "user.ping")[0]["agent"] == "receiver"
    assert records[-1]["event"] == "trace.stopped"
    assert (tmp_path / "world.html").exists()
    assert f"viewer: {tmp_path / 'world.html'}" in capsys.readouterr().err
    assert not is_tracing()
    assert not structlog.is_configured()


@pytest.mark.asyncio
async def test_traced_activation_uses_real_time_defaults(tmp_path):
    trace = tmp_path / "tcp.jsonl"
    container = create_tcp_container(addr=("127.0.0.1", 5561))
    receiver = container.register(Receiver(), "receiver")
    container.register(Sender(receiver.addr), "sender")

    async with activate(container, trace=TraceConfig(trace, html=False)):
        await asyncio.sleep(0.1)

    records = read_trace(trace)
    started = by_event(records, "trace.started")[0]
    assert started["categories"] == sorted(REAL_TIME_CATEGORIES)
    assert by_event(records, "container.started")[0]["container"] == "TCPContainer"
    assert by_event(records, "user.ping")
    assert not list(tmp_path.glob("*.html"))


@pytest.mark.asyncio
async def test_trace_is_finished_when_the_run_fails(tmp_path):
    trace = tmp_path / "failed.jsonl"

    with pytest.raises(RuntimeError, match="boom"):
        async with run_with_simulation(Receiver(), trace=trace):
            raise RuntimeError("boom")

    assert read_trace(trace)[-1]["event"] == "trace.stopped"
    assert (tmp_path / "failed.html").exists()
    assert not is_tracing()


@pytest.mark.asyncio
async def test_previous_structlog_configuration_is_restored(tmp_path):
    app_log = tmp_path / "app.jsonl"
    configure_structlog(app_log)
    log.info("before")

    await ping_world(create_world(trace=TraceConfig(tmp_path / "t.jsonl", html=False)))
    log.info("after")
    configure_structlog()

    assert [r["event"] for r in read_trace(app_log)] == ["before", "after"]
    assert "before" not in {r["event"] for r in read_trace(tmp_path / "t.jsonl")}


@pytest.mark.asyncio
async def test_run_inside_a_trace_is_not_traced_separately(tmp_path):
    configure_structlog(tmp_path / "outer.jsonl")
    enable_tracing()
    try:
        with pytest.warns(UserWarning, match="already on"):
            await ping_world(create_world(trace=tmp_path / "inner.jsonl"))
        assert is_tracing()
    finally:
        disable_tracing()
        configure_structlog()
    assert not (tmp_path / "inner.jsonl").exists()


def test_trace_argument_forms():
    assert _trace_config(None) is None
    assert _trace_config(False) is None
    assert _trace_config(True) == TraceConfig()
    assert _trace_config("run.jsonl").path == "run.jsonl"
    assert _trace_config(Path("run.jsonl")).path == Path("run.jsonl")
    config = TraceConfig(html=False)
    assert _trace_config(config) is config
    with pytest.raises(TypeError):
        _trace_config(1)
