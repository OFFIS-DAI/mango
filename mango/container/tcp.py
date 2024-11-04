"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""

import asyncio
import logging
import time
from typing import Any

from mango.container.core import AgentAddress, Container
from mango.container.mp import ContainerMirrorData

from ..messages.codecs import Codec
from ..messages.message import MangoMessage
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
        ttl_in_sec: float = 30.0,
        max_connections_per_target: int = 10,
    ) -> None:
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
                        await asyncio.get_running_loop().create_connection(
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
        addr: tuple[str, int],
        codec: Codec,
        clock: Clock,
        **kwargs,
    ):
        """
        Initializes a TCP container. Do not directly call this method but use
        the factory method create_container instead.
        :param addr: The container address
        :param codec: The codec to use
        :param loop: Current event loop
        """
        super().__init__(
            addr=addr,
            codec=codec,
            name=f"{addr[0]}:{addr[1]}",
            clock=clock,
            **kwargs,
        )

        self._tcp_connection_pool = None
        self.server = None  # will be set within start
        self._tcp_connection_pool = TCPConnectionPool(
            ttl_in_sec=self._kwargs.get(TCP_CONNECTION_TTL, 30),
            max_connections_per_target=self._kwargs.get(
                TCP_MAX_CONNECTIONS_PER_TARGET, 10
            ),
        )

    async def start(self):
        # create a TCP server bound to host and port that uses the
        # specified protocol
        self.server = await asyncio.get_running_loop().create_server(
            lambda: ContainerProtocol(container=self, codec=self.codec),
            self.addr[0],
            self.addr[1],
        )
        # if 0 is specified
        self.addr = (self.addr[0], self.server.sockets[0]._sock.getsockname()[1])
        await super().start()

    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message to an agent using TCP.

        :param content: The content of the message
        :param receiver_addr: Tuple of host, port
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        protocol_addr = receiver_addr.protocol_addr
        if isinstance(protocol_addr, str) and ":" in protocol_addr:
            protocol_addr = protocol_addr.split(":")
        elif isinstance(protocol_addr, tuple | list) and len(protocol_addr) == 2:
            protocol_addr = tuple(protocol_addr)
        else:
            logger.warning(
                "Receiver ProtocolAddress sending message from %s to %s is not valid",
                sender_id,
                receiver_addr,
            )
            return False

        meta = {}
        for key, value in kwargs.items():
            meta[key] = value
        meta["sender_id"] = sender_id
        meta["sender_addr"] = self.addr
        meta["receiver_id"] = receiver_addr.aid

        if protocol_addr == self.addr:
            # internal message
            meta["network_protocol"] = "tcp"
            success = self._send_internal_message(
                content, receiver_addr.aid, default_meta=meta
            )
        else:
            message = content
            # if the user does not provide a splittable content, we create the default one
            if not hasattr(content, "split_content_and_meta"):
                message = MangoMessage(content, meta)
            success = await self._send_external_message(
                receiver_addr.protocol_addr, message, meta
            )

        return success

    async def _send_external_message(self, addr, message, meta) -> bool:
        """
        Sends *message* to another container at *addr*
        :param addr: Tuple of (host, port)
        :param message: The message
        :return:
        """
        if addr is None or not isinstance(addr, tuple | list) or len(addr) != 2:
            logger.warning(
                "Sending external message not successful, invalid address; %s",
                addr,
            )
            return False

        try:
            protocol = await self._tcp_connection_pool.obtain_connection(
                addr[0],
                addr[1],
                ContainerProtocol(container=self, codec=self.codec),
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

    def _create_mirror_container(self):
        return tcp_mirror_container_creator

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

        if self._tcp_connection_pool is not None:
            await self._tcp_connection_pool.shutdown()

        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
