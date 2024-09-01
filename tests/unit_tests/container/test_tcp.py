import pytest
import asyncio
from mango.container.tcp import TCPConnectionPool
from mango.container.protocol import ContainerProtocol
from mango import create_container

@pytest.mark.asyncio
async def test_connection_open_close():
    c = await create_container(addr=("127.0.0.2", 5555), copy_internal_messages=False)
    await c.shutdown()

@pytest.mark.asyncio
async def test_connection_pool_obtain_release():
    c = await create_container(addr=("127.0.0.2", 5555), copy_internal_messages=False)
    c2 = await create_container(addr=("127.0.0.2", 5556), copy_internal_messages=False)

    addr = "127.0.0.2", 5556
    connection_pool = TCPConnectionPool(asyncio.get_event_loop())
    raw_prot = ContainerProtocol(
        container=c, loop=asyncio.get_event_loop(), codec=c.codec
    )
    protocol = await connection_pool.obtain_connection(addr[0], addr[1], raw_prot)

    assert connection_pool._available_connections[addr].qsize() == 0
    assert connection_pool._connection_counts[addr] == 1

    await connection_pool.release_connection(addr[0], addr[1], protocol)

    assert connection_pool._available_connections[addr].qsize() == 1
    assert connection_pool._connection_counts[addr] == 1
    await connection_pool.shutdown()

    await c.shutdown()
    await c2.shutdown()


@pytest.mark.asyncio
async def test_connection_pool_double_obtain_release():
    c = await create_container(addr=("127.0.0.2", 5555), copy_internal_messages=False)
    c2 = await create_container(addr=("127.0.0.2", 5556), copy_internal_messages=False)

    addr = "127.0.0.2", 5556
    connection_pool = TCPConnectionPool(asyncio.get_event_loop())
    raw_prot = ContainerProtocol(
        container=c, loop=asyncio.get_event_loop(), codec=c.codec
    )
    protocol = await connection_pool.obtain_connection(addr[0], addr[1], raw_prot)

    assert connection_pool._available_connections[addr].qsize() == 0
    assert connection_pool._connection_counts[addr] == 1

    raw_prot = ContainerProtocol(
        container=c, loop=asyncio.get_event_loop(), codec=c.codec
    )
    protocol2 = await connection_pool.obtain_connection(addr[0], addr[1], raw_prot)

    assert connection_pool._available_connections[addr].qsize() == 0
    assert connection_pool._connection_counts[addr] == 2

    await connection_pool.release_connection(addr[0], addr[1], protocol)

    assert connection_pool._available_connections[addr].qsize() == 1
    assert connection_pool._connection_counts[addr] == 2

    await connection_pool.release_connection(addr[0], addr[1], protocol2)

    assert connection_pool._available_connections[addr].qsize() == 2
    assert connection_pool._connection_counts[addr] == 2
    await connection_pool.shutdown()

    await c.shutdown()
    await c2.shutdown()


@pytest.mark.asyncio
async def test_ttl():
    addr = "127.0.0.2", 5556
    addr2 = "127.0.0.2", 5557
    c = await create_container(addr=("127.0.0.2", 5555), copy_internal_messages=False)
    c2 = await create_container(addr=addr, copy_internal_messages=False)
    c3 = await create_container(addr=addr2, copy_internal_messages=False)

    connection_pool = TCPConnectionPool(asyncio.get_event_loop(), ttl_in_sec=0.1)
    raw_prot = ContainerProtocol(
        container=c, loop=asyncio.get_event_loop(), codec=c.codec
    )
    protocol = await connection_pool.obtain_connection(addr[0], addr[1], raw_prot)

    assert connection_pool._available_connections[addr].qsize() == 0
    assert connection_pool._connection_counts[addr] == 1

    await connection_pool.release_connection(addr[0], addr[1], protocol)

    assert connection_pool._available_connections[addr].qsize() == 1
    assert connection_pool._connection_counts[addr] == 1

    await asyncio.sleep(0.2)

    protocol = await connection_pool.obtain_connection(addr2[0], addr2[1], raw_prot)

    assert connection_pool._available_connections[addr].qsize() == 0
    assert connection_pool._connection_counts[addr] == 0
    assert connection_pool._available_connections[addr2].qsize() == 0
    assert connection_pool._connection_counts[addr2] == 1

    await connection_pool.release_connection(addr2[0], addr2[1], protocol)

    assert connection_pool._available_connections[addr2].qsize() == 1
    assert connection_pool._connection_counts[addr2] == 1
    await connection_pool.shutdown()

    await c.shutdown()
    await c2.shutdown()
    await c3.shutdown()


@pytest.mark.asyncio
async def test_max_connections():
    c = await create_container(addr=("127.0.0.2", 5555), copy_internal_messages=False)
    c2 = await create_container(addr=("127.0.0.2", 5556), copy_internal_messages=False)

    addr = "127.0.0.2", 5556
    connection_pool = TCPConnectionPool(
        asyncio.get_event_loop(), max_connections_per_target=1
    )
    raw_prot = ContainerProtocol(
        container=c, loop=asyncio.get_event_loop(), codec=c.codec
    )
    protocol = await connection_pool.obtain_connection(addr[0], addr[1], raw_prot)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            connection_pool.obtain_connection(addr[0], addr[1], raw_prot), timeout=1
        )

    await connection_pool.release_connection(addr[0], addr[1], protocol)
    protocol = await connection_pool.obtain_connection(addr[0], addr[1], raw_prot)
    await connection_pool.release_connection(addr[0], addr[1], protocol)

    assert connection_pool._available_connections[addr].qsize() == 1
    assert connection_pool._connection_counts[addr] == 1
    await connection_pool.shutdown()

    await c.shutdown()
    await c2.shutdown()
