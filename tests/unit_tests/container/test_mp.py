import asyncio
import contextlib
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

from mango import (
    Agent,
    AgentAddress,
    activate,
    addr,
    create_ec_container,
    create_tcp_container,
    sender_addr,
)


async def wait_until(predicate, timeout: float = 5.0):
    """Poll until *predicate* holds, instead of sleeping a fixed amount.

    A single short sleep is not enough to hand control to the IO callbacks: the
    Windows event loop reads a clock with ~16 ms granularity and lets a shorter
    timer expire without ever polling for completions.
    """
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, "timed out waiting for condition"
        await asyncio.sleep(0.01)


class MyAgent(Agent):
    test_counter: int = 0
    current_task: object
    i_am_ready = False

    def on_ready(self):
        self.i_am_ready = True

    def handle_message(self, content, meta):
        self.test_counter += 1

        # get addr and id from sender
        if self.test_counter == 1:
            # send back pong, providing your own details
            self.current_task = self.schedule_instant_message(
                content=self.i_am_ready, receiver_addr=sender_addr(meta)
            )


class P2PMainAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1


class P2PTestAgent(Agent):
    receiver_id: str

    def __init__(self, receiver_id):
        super().__init__()
        self.receiver_id = receiver_id

    def handle_message(self, content, meta):
        # send back pong, providing your own details
        self.current_task = self.schedule_instant_message(
            content="pong", receiver_addr=addr(meta["sender_addr"], self.receiver_id)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "num_sp_agents,num_sp",
    [
        (1, 1),
        (2, 1),
        (2, 2),
        (1, 2),
        (3, 2),
        (2, 3),
        (3, 3),
        (1, 10),
        (10, 1),
        (10, 2),
        (10, 10),
    ],
)
async def test_agent_processes_ping_pong(num_sp_agents, num_sp):
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 15589), copy_internal_messages=False)
    for i in range(num_sp):
        await c.as_agent_process(
            agent_creator=lambda container: [
                container.register(MyAgent(), suggested_aid=f"process_agent{i},{j}")
                for j in range(num_sp_agents)
            ]
        )
    agent = c.register(MyAgent())

    # WHEN
    async with activate(c) as c:
        for i in range(num_sp):
            for j in range(num_sp_agents):
                await agent.send_message(
                    "Message To Process Agent",
                    receiver_addr=addr(c.addr, f"process_agent{i},{j}"),
                )
        while agent.test_counter != num_sp_agents * num_sp:
            await asyncio.sleep(0.01)

    assert agent.i_am_ready is True

    assert agent.test_counter == num_sp_agents * num_sp


@pytest.mark.asyncio
async def test_agent_processes_ping_pong_p_to_p():
    # GIVEN
    addr = ("127.0.0.1", 5829)
    aid_main_agent = "main_agent"
    c = create_tcp_container(addr=addr, copy_internal_messages=False)
    await c.as_agent_process(
        agent_creator=lambda container: container.register(
            P2PTestAgent(aid_main_agent), suggested_aid="process_agent1"
        )
    )
    main_agent = c.register(P2PMainAgent(), suggested_aid=aid_main_agent)

    # WHEN
    def agent_init(c):
        agent = c.register(MyAgent(), suggested_aid="process_agent2")
        agent.schedule_instant_message(
            "Message To Process Agent",
            receiver_addr=AgentAddress(addr, "process_agent1"),
        )
        return agent

    async with activate(c) as c:
        await c.as_agent_process(agent_creator=agent_init)

        while main_agent.test_counter != 1:
            await asyncio.sleep(0.01)

    assert main_agent.test_counter == 1


@pytest.mark.asyncio
async def test_async_agent_processes_ping_pong_p_to_p():
    # GIVEN
    addr = ("127.0.0.1", 5811)
    aid_main_agent = "main_agent"
    c = create_tcp_container(addr=addr, copy_internal_messages=False)
    main_agent = c.register(P2PMainAgent(), suggested_aid=aid_main_agent)

    target_addr = main_agent.addr

    async def agent_creator(container):
        p2pta = container.register(
            P2PTestAgent(aid_main_agent), suggested_aid="process_agent1"
        )
        await p2pta.send_message(content="pong", receiver_addr=target_addr)

    async with activate(c) as c:
        await c.as_agent_process(agent_creator=agent_creator)

        # WHEN
        def agent_init(c):
            agent = c.register(MyAgent(), suggested_aid="process_agent2")
            agent.schedule_instant_message(
                "Message To Process Agent", AgentAddress(addr, "process_agent1")
            )
            return agent

        await c.as_agent_process(agent_creator=agent_init)

        while main_agent.test_counter != 2:
            await asyncio.sleep(0.01)

    assert main_agent.test_counter == 2


@pytest.mark.asyncio
async def test_async_agent_processes_ping_pong_p_to_p_external():
    # GIVEN
    addr = ("127.0.0.1", 5811)
    aid_main_agent = "main_agent"
    c = create_ec_container(addr=addr, copy_internal_messages=False)
    main_agent = c.register(P2PMainAgent(), suggested_aid=aid_main_agent)

    target_addr = main_agent.addr

    async def agent_creator(container):
        p2pta = container.register(
            P2PTestAgent(aid_main_agent), suggested_aid="process_agent1"
        )
        await p2pta.send_message(content="pong", receiver_addr=target_addr)

    async with activate(c) as c:
        await c.as_agent_process(agent_creator=agent_creator)

        # WHEN
        def agent_init(c):
            agent = c.register(MyAgent(), suggested_aid="process_agent2")
            agent.schedule_instant_message(
                "Message To Process Agent", AgentAddress(addr, "process_agent1")
            )
            return agent

        await c.as_agent_process(agent_creator=agent_init)

        while main_agent.test_counter != 2:
            await asyncio.sleep(0.01)

    assert main_agent.test_counter == 2


def test_lazy_setup_agent_processes():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 15589), copy_internal_messages=False)
    c.as_agent_process_lazy(
        agent_creator=lambda container: [
            container.register(MyAgent(), suggested_aid="process_agent0")
        ]
    )
    agent = c.register(MyAgent())


@pytest.mark.asyncio
async def test_lazy_ready_agent_processes():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 15589), copy_internal_messages=False)
    c.as_agent_process_lazy(
        agent_creator=lambda container: [
            container.register(MyAgent(), suggested_aid="process_agent0")
        ]
    )
    agent = c.register(MyAgent())

    def handle_message(content, meta):
        agent.other_agent_is_ready = content

    agent.handle_message = handle_message

    async with activate(c) as c:
        await agent.send_message(
            "Message To Process Agent",
            receiver_addr=addr(c.addr, "process_agent0"),
        )
        await wait_until(lambda: hasattr(agent, "other_agent_is_ready"))
        assert agent.other_agent_is_ready is True


@pytest.mark.asyncio
async def test_ready_agent_processes():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 15589), copy_internal_messages=False)
    await c.as_agent_process(
        agent_creator=lambda container: [
            container.register(MyAgent(), suggested_aid="process_agent0")
        ]
    )
    agent = c.register(MyAgent())

    def handle_message(content, meta):
        agent.other_agent_is_ready = content

    agent.handle_message = handle_message

    async with activate(c) as c:
        await agent.send_message(
            "Message To Process Agent",
            receiver_addr=addr(c.addr, "process_agent0"),
        )
        await wait_until(lambda: hasattr(agent, "other_agent_is_ready"))
        assert agent.other_agent_is_ready is True


class LateAgent(Agent):
    """Registered after its agent process is already running."""

    def handle_message(self, content, meta):
        pass


async def _async_agent_creator(container):
    """Awaits before registering.

    A coroutine agent creator is supported, and the await lets the process's
    dispatch reader attach before ``register`` performs its handshake. That used
    to make the handshake collide with the reader.
    """
    await asyncio.sleep(0.1)
    return [container.register(MyAgent(), suggested_aid="late_agent")]


def _single_agent_creator(container):
    return [container.register(MyAgent(), suggested_aid="resident")]


async def _register_from_inside(container, aid):
    container.register(LateAgent(), suggested_aid=aid)


def _failing_agent_creator(container):
    raise RuntimeError("deliberate failure inside the agent creator")


@pytest.mark.asyncio
async def test_as_agent_process_returns_the_handle():
    c = create_tcp_container(addr=("127.0.0.1", 15590), copy_internal_messages=False)
    handle = await c.as_agent_process(agent_creator=_single_agent_creator)
    assert handle is not None, "the handle carries the pid; callers need it"
    assert isinstance(handle.pid, int)
    async with activate(c):
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_async_agent_creator_can_register_after_awaiting():
    c = create_tcp_container(addr=("127.0.0.1", 15591), copy_internal_messages=False)
    await c.as_agent_process(agent_creator=_async_agent_creator)
    async with activate(c) as c:
        await wait_until(lambda: "late_agent" in c._container_process_manager.aids)


@pytest.mark.asyncio
async def test_register_inside_a_running_agent_process():
    c = create_tcp_container(addr=("127.0.0.1", 15592), copy_internal_messages=False)
    handle = await c.as_agent_process(agent_creator=_single_agent_creator)
    async with activate(c) as c:
        c.dispatch_to_agent_process(handle.pid, _register_from_inside, "runtime_agent")
        await wait_until(lambda: "runtime_agent" in c._container_process_manager.aids)


@pytest.mark.asyncio
async def test_dispatch_larger_than_the_socket_buffer_keeps_the_channel_usable():
    c = create_tcp_container(addr=("127.0.0.1", 15593), copy_internal_messages=False)
    handle = await c.as_agent_process(agent_creator=_single_agent_creator)
    async with activate(c) as c:
        c.dispatch_to_agent_process(
            handle.pid, _register_from_inside, "x" * (4 * 1024 * 1024)
        )
        # A frame torn by a partial synchronous write would desynchronise the
        # channel, so the next dispatch would never arrive.
        c.dispatch_to_agent_process(handle.pid, _register_from_inside, "after_big")
        await wait_until(lambda: "after_big" in c._container_process_manager.aids)


@pytest.mark.asyncio
async def test_agent_process_failing_to_start_raises_instead_of_hanging():
    c = create_tcp_container(addr=("127.0.0.1", 15594), copy_internal_messages=False)
    with pytest.raises(RuntimeError, match="terminated during startup"):
        await asyncio.wait_for(
            c.as_agent_process(agent_creator=_failing_agent_creator), timeout=60
        )
    await c.shutdown()


@pytest.mark.asyncio
async def test_shutdown_twice_is_harmless():
    c = create_tcp_container(addr=("127.0.0.1", 15595), copy_internal_messages=False)
    await c.as_agent_process(agent_creator=_single_agent_creator)
    async with activate(c):
        await asyncio.sleep(0.05)
        await c.shutdown()


def _pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0  # 0 == exited
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def test_agent_process_stops_when_its_parent_dies(tmp_path):
    """An agent process must not outlive a parent that dies without warning.

    The helper kills itself with os._exit, so the terminate event is never set
    and multiprocessing's exit hook never runs. The agent process has to notice
    on its own, or it stays alive forever.
    """
    helper = pathlib.Path(__file__).with_name("orphan_helper.py")
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    marker = tmp_path / "stopped.marker"
    pidfile = tmp_path / "child.pid"
    # Files rather than pipes: were the fix to regress, the surviving agent
    # process would inherit the pipe ends and block this call instead of
    # failing the assertion below.
    out = (tmp_path / "helper.log").open("w")
    env = dict(
        os.environ,
        MANGO_ORPHAN_MARKER=str(marker),
        PYTHONPATH=str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    with out:
        proc = subprocess.Popen(
            [sys.executable, str(helper), str(pidfile)],
            cwd=str(repo_root),
            env=env,
            stdout=out,
            stderr=out,
        )
        returncode = proc.wait(timeout=120)

    log = (tmp_path / "helper.log").read_text()
    assert returncode == 7, f"helper did not die as intended: {log}"
    assert pidfile.exists(), f"helper never started an agent process: {log}"

    pid = int(pidfile.read_text())
    try:
        deadline = time.perf_counter() + 30
        while time.perf_counter() < deadline and not marker.exists():
            time.sleep(0.05)
        assert marker.exists(), (
            "the agent process did not shut down after its parent died"
        )

        while time.perf_counter() < deadline and _pid_is_alive(pid):
            time.sleep(0.05)
        assert not _pid_is_alive(pid), f"agent process {pid} outlived its parent"
    finally:
        # Do not leave the very orphan this test is about behind when it fails.
        if _pid_is_alive(pid):
            _kill(pid)


if __name__ == "__main__":
    asyncio.run(test_agent_processes_ping_pong(5, 5))
