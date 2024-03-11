"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""

import asyncio
import logging
import time
from typing import Optional, Tuple, Union

from mango.container.core import Container, ContainerMirrorData

from ..messages.codecs import Codec
from ..util.clock import Clock
from .protocol import ContainerProtocol

logger = logging.getLogger(__name__)

TCP_CONNECTION_TTL = "tcp_connection_ttl"
TCP_MAX_CONNECTIONS_PER_TARGET = "tcp_max_connections_per_target"


class TCPConnectionPool:
    """Pool of async tcp connections. Is able to create arbitrary connections and manages
    them. This makes reusing connections possible. To obtain a connection, use `obtain_connection`.
    When you are done with the connection, use `release_connection`.

    There are two parameters to be set, ttl_in_sec (time to live for a connection), max_connections_per_target (max number
    of connections per connection target).
    """

    def __init__(
        self,
        asyncio_loop,
        ttl_in_sec: float = 30.0,
        max_connections_per_target: int = 10,
    ) -> None:
        self._loop = asyncio_loop
        self._available_connections = {}
        self._connection_counts = {}
        self._ttl_in_sec = ttl_in_sec
        self._max_connections_per_target = max_connections_per_target

    def _put_in_available_connections(self, addr_key, connection):
        return self._available_connections[addr_key].put((connection, time.time()))

    async def obtain_connection(
        self, host: str, port: int, protocol: ContainerProtocol
    ):
        """Obtain a connection from the pool. If no connection is available, a new connection is
        created.

        :param host: the host
        :type host: str
        :param port: the port
        :type port: int
        :param protocol: ContainerProtocol
        :type protocol: ContainerProtocol
        :return: connection
        :rtype: ContainerProtocol with open transport object
        """
        addr_key = (host, port)

        # maintaining connections
        shutdown_connections = []
        for key, queue in self._available_connections.items():
            if queue.qsize() > 0:
                item, item_time = queue._queue[0]
                sec_elapsed = time.time() - item_time
                if sec_elapsed > self._ttl_in_sec:
                    self._connection_counts[key] -= 1
                    connection = item
                    shutdown_connections.append(connection)
                    queue.get_nowait()
        for connection in shutdown_connections:
            await connection.shutdown()

        if addr_key not in self._available_connections:
            self._available_connections[addr_key] = asyncio.Queue(
                self._max_connections_per_target
            )
            self._connection_counts[addr_key] = 0

        # only creat new ones if the max connections number won't be exceeded
        if (
            self._available_connections[addr_key].empty()
            and self._connection_counts[addr_key] < self._max_connections_per_target
        ):
            self._connection_counts[addr_key] += 1
            await self._put_in_available_connections(
                addr_key,
                (
                    (
                        await self._loop.create_connection(
                            lambda: protocol,
                            host,
                            port,
                        )
                    )[1]
                ),
            )

        # if the queue is empty this will wait until a connection is available again
        connection = (await self._available_connections[addr_key].get())[0]
        self._available_connections[addr_key].task_done()
        return connection

    async def release_connection(self, host: str, port: int, connection):
        """Release the connection to the pool. Have to be called after a connection is obtained,
        otherwise the connection can never return to the pool.

        :param host: the host
        :type host: str
        :param port: the port
        :type port: int
        :param connection: the connection
        :type connection: ContainerProtocol
        """
        addr_key = (host, port)

        if self._ttl_in_sec >= 0:
            await self._put_in_available_connections(addr_key, connection)
        else:
            await connection.shutdown()
            self._connection_counts[addr_key] -= 1

    async def shutdown(self):
        # maintaining connections
        for _, queue in self._available_connections.items():
            while not queue.empty():
                connection = (await queue.get())[0]
                queue.task_done()
                await connection.shutdown()


def tcp_mirror_container_creator(
    container_data, loop, message_pipe, main_queue, event_pipe, terminate_event
):
    return TCPContainer(
        addr=container_data.addr,
        codec=container_data.codec,
        clock=container_data.clock,
        loop=loop,
        mirror_data=ContainerMirrorData(
            message_pipe=message_pipe,
            event_pipe=event_pipe,
            terminate_event=terminate_event,
            main_queue=main_queue,
        ),
        **container_data.kwargs,
    )


class TCPContainer(Container):
    """
    This is a container that communicate directly with other containers
    via tcp
    """

    def __init__(
        self,
        *,
        addr: Tuple[str, int],
        codec: Codec,
        loop: asyncio.AbstractEventLoop,
        clock: Clock,
        **kwargs,
    ):
        """
        Initializes a TCP container. Do not directly call this method but use
        the factory method of **Container** instead
        :param addr: The container address
        :param codec: The codec to use
        :param loop: Current event loop
        """
        super().__init__(
            addr=addr,
            codec=codec,
            loop=loop,
            name=f"{addr[0]}:{addr[1]}",
            clock=clock,
            **kwargs,
        )

        self.server = None  # will be set within setup
        self.running = True
        self._tcp_connection_pool = TCPConnectionPool(
            loop,
            ttl_in_sec=kwargs.get(TCP_CONNECTION_TTL, 30),
            max_connections_per_target=kwargs.get(TCP_MAX_CONNECTIONS_PER_TARGET, 10),
        )

    async def setup(self):
        # create a TCP server bound to host and port that uses the
        # specified protocol
        self.server = await self.loop.create_server(
            lambda: ContainerProtocol(container=self, loop=self.loop, codec=self.codec),
            self.addr[0],
            self.addr[1],
        )

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message to an agent using TCP.

        :param content: The content of the message
        :param receiver_addr: Tuple of host, port
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        if isinstance(receiver_addr, str) and ":" in receiver_addr:
            receiver_addr = receiver_addr.split(":")
        elif isinstance(receiver_addr, (tuple, list)) and len(receiver_addr) == 2:
            receiver_addr = tuple(receiver_addr)
        else:
            logger.warning("Address for sending message is not valid;%s", receiver_addr)
            return False

        message = content

        if receiver_addr == self.addr:
            if not receiver_id:
                receiver_id = message.receiver_id

            # internal message
            meta = {"network_protocol": "tcp"}
            success = self._send_internal_message(
                message, receiver_id, default_meta=meta
            )
        else:
            success = await self._send_external_message(receiver_addr, message)

        return success

    async def _send_external_message(self, addr, message) -> bool:
        """
        Sends *message* to another container at *addr*
        :param addr: Tuple of (host, port)
        :param message: The message
        :return:
        """
        if addr is None or not isinstance(addr, (tuple, list)) or len(addr) != 2:
            logger.warning(
                "Sending external message not successful, invalid address; %s",
                addr,
            )
            return False

        try:
            protocol = await self._tcp_connection_pool.obtain_connection(
                addr[0],
                addr[1],
                ContainerProtocol(container=self, loop=self.loop, codec=self.codec),
            )
            logger.debug("Connection established to addr; %s", addr)

            protocol.write(self.codec.encode(message))

            logger.debug("Message sent to addr; %s", addr)

            await self._tcp_connection_pool.release_connection(
                addr[0], addr[1], protocol
            )
        except OSError as e:
            logger.warning(
                "Could not establish connection to receiver of a message; %s", addr
            )
            logger.error(e)
            return False
        return True

    def as_agent_process(
        self,
        agent_creator,
        mirror_container_creator=tcp_mirror_container_creator,
    ):
        return super().as_agent_process(
            agent_creator=agent_creator,
            mirror_container_creator=mirror_container_creator,
        )

    async def shutdown(self):
        """
        calls shutdown() from super class Container and closes the server
        """
        await super().shutdown()
        await self._tcp_connection_pool.shutdown()

        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
