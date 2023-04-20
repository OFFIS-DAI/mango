"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""
import asyncio
import logging
import time
from typing import Optional, Tuple, Union

from mango.container.core import Container

from ..messages.codecs import Codec
from ..util.clock import Clock
from .protocol import ContainerProtocol

logger = logging.getLogger(__name__)

TCP_CONNECTION_TTL = "tcp_connection_ttl"
TCP_MAX_CONNECTIONS_PER_TARGET = "tcp_max_connections_per_target"


class TCPConnectionPool:
    def __init__(
        self, asyncio_loop, ttl_in_sec: int = 30, max_connections_per_target: int = 10
    ) -> None:
        self._loop = asyncio_loop
        self._available_connections = {}
        self._connection_counts = {}
        self._ttl_in_sec = ttl_in_sec
        self._max_connections_per_target = max_connections_per_target

    async def obtain_connection(
        self, host: str, port: int, protocol: ContainerProtocol
    ):
        addr_key = (host, port)

        # maintaining connections
        for addr_key, queue in self._available_connections.items():
            if queue.qsize() > 0:
                sec_elapsed = time.time() - queue.get_nowait()[1]
                if sec_elapsed > self._ttl_in_sec:
                    self._connection_counts[addr_key] -= 1
                    connection = (await queue.get())[0]
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
            await self._available_connections[addr_key].put(
                (
                    await self._loop.create_connection(
                        lambda: protocol,
                        host,
                        protocol,
                    ),
                    time.time(),
                )
            )

        # if the queue is empty this will wait until a connection is available again
        return self._available_connections[addr_key].get()

    def release_connection(self, host: str, port: int, connection):
        addr_key = (host, port)

        asyncio.create_task(self._available_connections[addr_key].put(connection))

    async def shutdown(self):
        # maintaining connections
        for _, queue in self._available_connections.items():
            while not queue.empty():
                connection = (await queue.get())[0]
                await connection.shutdown()


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

        self.server = None  # will be set within the factory method
        self.running = True
        self._tcp_connection_pool = TCPConnectionPool(
            loop,
            ttl_in_sec=kwargs.get(TCP_CONNECTION_TTL, 30),
            max_connections_per_target=kwargs.get(TCP_MAX_CONNECTIONS_PER_TARGET, 10),
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
            logger.warning(f"Address for sending message is not valid;{receiver_addr}")
            return False

        message = content

        if receiver_addr == self.addr:
            if not receiver_id:
                receiver_id = message.receiver_id

            # internal message
            meta = {"network_protocol": "tcp"}
            receiver = self._agents.get(receiver_id, None)
            if receiver is None:
                logger.warning(
                    f"Sending internal message not successful, receiver id unknown;{receiver_id}"
                )
                return False
            success = self._send_internal_message(
                message, default_meta=meta, target_inbox_overwrite=receiver.inbox
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
                str(addr),
            )
            return False

        try:
            _, protocol = await self._tcp_connection_pool.obtain_connection(
                addr[0],
                addr[1],
                lambda: ContainerProtocol(
                    container=self, loop=self.loop, codec=self.codec
                ),
            )
            logger.debug("Connection established to addr; %s", str(addr))

            protocol.write(self.codec.encode(message))

            logger.debug("Message sent to addr; %s", str(addr))

            self._tcp_connection_pool.release_connection(addr[0], addr[1], protocol)
        except OSError:
            logger.warning(
                "Could not establish connection to receiver of a message; %s", str(addr)
            )
            return False
        return True

    async def shutdown(self):
        """
        calls shutdown() from super class Container and closes the server
        """
        await super().shutdown()
        self._tcp_connection_pool.shutdown()
        self.server.close()
        await self.server.wait_closed()
