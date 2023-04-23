"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""
import asyncio
import logging
import time
from typing import Optional, Tuple, Union

from mango.container.core import Container, IPCEvent, IPCEventType
from multiprocessing import Queue, Event
from multiprocessing.connection import Connection

from ..messages.codecs import Codec
from ..util.clock import Clock
from .protocol import ContainerProtocol

logger = logging.getLogger(__name__)

TCP_CONNECTION_TTL = "tcp_connection_ttl"
TCP_MAX_CONNECTIONS_PER_TARGET = "tcp_max_connections_per_target"


class TCPConnectionPool:
    def __init__(
        self, asyncio_loop, ttl_in_sec: float = 30, max_connections_per_target: int = 10
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
        addr_key = (host, port)

        # maintaining connections
        for key, queue in self._available_connections.items():
            if queue.qsize() > 0:
                item, item_time = queue.get_nowait()
                queue.task_done()
                sec_elapsed = time.time() - item_time
                if sec_elapsed > self._ttl_in_sec:
                    self._connection_counts[key] -= 1
                    connection = item
                    await connection.shutdown()
                else:
                    queue.put_nowait((item, item_time))

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
        addr_key = (host, port)

        await self._put_in_available_connections(addr_key, connection)

    async def shutdown(self):
        # maintaining connections
        for _, queue in self._available_connections.items():
            while not queue.empty():
                connection = (await queue.get())[0]
                queue.task_done()
                await connection.shutdown()


class TCPContainerMirror:
    pass


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
            await protocol.flush()

            logger.debug("Message sent to addr; %s", addr)

            await self._tcp_connection_pool.release_connection(
                addr[0], addr[1], protocol
            )
        except OSError:
            logger.warning(
                "Could not establish connection to receiver of a message; %s", addr
            )
            return False
        return True

    @staticmethod
    def _mirror_container_creator(
        container_data, loop, from_sp_queue, to_sp_queue, event_pipe, terminate_event
    ):
        return TCPContainerMirror(
            container_data,
            loop,
            from_sp_queue,
            to_sp_queue,
            event_pipe,
            terminate_event,
        )

    def as_agent_process(
        self,
        agent_creator,
        mirror_container_creator=_mirror_container_creator,
    ):
        super().as_agent_process(
            agent_creator=agent_creator,
            mirror_container_creator=mirror_container_creator,
        )

    async def shutdown(self):
        """
        calls shutdown() from super class Container and closes the server
        """
        await super().shutdown()
        await self._tcp_connection_pool.shutdown()
        self.server.close()
        await self.server.wait_closed()


class TCPContainerMirror(TCPContainer):
    def __init__(
        self,
        *,
        container_data,
        loop: asyncio.AbstractEventLoop,
        from_sp_queue: Queue,
        to_sp_queue: Queue,
        event_pipe: Connection,
        terminate_event: Event,
        **kwargs,
    ):
        super().__init__(
            addr=container_data.addr,
            codec=container_data.codec,
            clock=container_data.clock,
            loop=loop,
            **kwargs,
        )

        self._from_sp_queue = from_sp_queue
        self._to_sp_queue = to_sp_queue
        self._event_pipe = event_pipe

        self._fetch_from_ipc_task = asyncio.create_task(
            self.move_incoming_messages_to_inbox(to_sp_queue, terminate_event)
        )

    async def move_incoming_messages_to_inbox(
        self, process_queue: Queue, terminate_event: Event
    ):
        while not terminate_event.is_set():
            if not process_queue.empty():
                next_item = process_queue.get()
                await self.inbox.put(next_item)
            else:
                await asyncio.sleep(0.1)
        self.shutdown()

    def _reserve_aid(self, suggested_aid=None):
        ipc_event = IPCEvent(IPCEventType.AID, suggested_aid)
        self._event_pipe.send(ipc_event)
        return self._event_pipe.recv()

    def _send_internal_message(
        self,
        message,
        receiver_id,
        priority=0,
        default_meta=None,
        inbox=None,
    ) -> bool:
        # route internal messages outside of the process to the main container
        if receiver_id not in self._agents:
            self._from_sp_queue.put_nowait(
                (message, receiver_id, priority, default_meta)
            )
            return True

        super()._send_internal_message(
            message=message,
            receiver_id=receiver_id,
            priority=priority,
            default_meta=default_meta,
            inbox=inbox,
        )

    def as_agent_process(
        self,
        agent_creator,
        mirror_container_creator,
    ):
        raise NotImplementedError("Mirror container do not support agent processes.")

    async def shutdown(self):
        """
        Shutdown container without server, as the mirror does not have an own server instance
        """

        # cancel _fetch_from_ipc_task
        if self._fetch_from_ipc_task is not None:
            self._fetch_from_ipc_task.cancel()
            try:
                await self._fetch_from_ipc_task
            except asyncio.CancelledError:
                pass

        await super().shutdown()
        await self._tcp_connection_pool.shutdown()
