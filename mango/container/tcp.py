"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple, Union

from mango.container.core import Container

from ..messages.codecs import Codec
from ..util.clock import Clock
from .protocol import ContainerProtocol

logger = logging.getLogger(__name__)


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
                f"Sending external message not successful, invalid address;{str(addr)}"
            )
            return False

        try:
            transport, protocol = await self.loop.create_connection(
                lambda: ContainerProtocol(
                    container=self, loop=self.loop, codec=self.codec
                ),
                addr[0],
                addr[1],
            )
            logger.debug(f"Connection established to addr;{str(addr)}")

            protocol.write(self.codec.encode(message))

            logger.debug(f"Message sent to addr;{str(addr)}")
            await protocol.shutdown()
        except OSError:
            logger.warning(
                f"Could not establish connection to receiver of a message;{str(addr)}"
            )
            return False
        return True

    async def shutdown(self):
        """
        calls shutdown() from super class Container and closes the server
        """
        await super().shutdown()
        self.server.close()
        await self.server.wait_closed()
