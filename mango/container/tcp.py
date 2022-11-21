"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""
import asyncio
import logging, warnings
from typing import Optional, Union, Tuple, Dict, Any
from .protocol import ContainerProtocol
from ..messages.codecs import Codec
from ..util.clock import Clock
from mango.container.core import Container

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
        proto_msgs_module=None,
        clock: Clock,
    ):
        """
        Initializes a TCP container. Do not directly call this method but use
        the factory method of **Container** instead
        :param addr: The container address
        :param codec: The codec to use
        :param loop: Current event loop
        :param proto_msgs_module: The module for proto msgs in case of
        proto as codec
        """
        super().__init__(
            addr=addr,
            codec=codec,
            proto_msgs_module=proto_msgs_module,
            loop=loop,
            name=f"{addr[0]}:{addr[1]}",
            clock=clock,
        )

        self.server = None  # will be set within the factory method
        self.running = True

    async def _handle_msg(self, *, priority: int, msg_content, meta: Dict[str, Any]):
        """

        :param priority:
        :param msg_content:
        :param meta:
        :return:
        """
        logger.debug(
            f"Received msg with content and meta;{str(msg_content)};{str(meta)}"
        )
        receiver_id = meta.get("receiver_id", None)
        if receiver_id and receiver_id in self._agents.keys():
            receiver = self._agents[receiver_id]
            await receiver.inbox.put((priority, msg_content, meta))
        else:
            logger.warning(f"Received a message for an unknown receiver;{receiver_id}")

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        create_acl: bool = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        mqtt_kwargs: Dict[str, Any] = None,
        **kwargs
    ) -> bool:
        """
        The Container sends a message to an agent using TCP.
        
        :param content: The content of the message
        :param receiver_addr: Tuple of host, port
        :param receiver_id: The agent id of the receiver
        :param create_acl: True if an acl message shall be created around the
            content.
            
            .. deprecated:: 0.4.0
                Use 'container.send_acl_message' instead. In the next version this parameter
                will be dropped entirely.
        :param acl_metadata: metadata for the acl_header.
            Ignored if create_acl == False
            
            .. deprecated:: 0.4.0
                Use 'container.send_acl_message' instead. In the next version this parameter
                will be dropped entirely.
        :param mqtt_kwargs: 
            .. deprecated:: 0.4.0
                Use 'kwargs' instead. In the next version this parameter
                will be dropped entirely.
        :param kwargs: Additional parameters to provide protocol specific settings 
        """
        if create_acl is not None or acl_metadata is not None:
            warnings.warn("The parameters create_acl and acl_metadata are deprecated and will " \
                          "be removed in the next release. Use send_acl_message instead.", DeprecationWarning)
        if mqtt_kwargs is not None:
            warnings.warn("The parameter mqtt_kwargs is deprecated and will " \
                          "be removed in the next release. Use kwargs instead.", DeprecationWarning)

        if isinstance(receiver_addr, str) and ":" in receiver_addr:
            receiver_addr = receiver_addr.split(":")
        elif isinstance(receiver_addr, (tuple, list)) and len(receiver_addr) == 2:
            receiver_addr = tuple(receiver_addr)
        else:
            logger.warning(f"Address for sending message is not valid;{receiver_addr}")
            return False

        if create_acl is not None and create_acl:
            message = self._create_acl(
                content=content,
                receiver_addr=receiver_addr,
                receiver_id=receiver_id,
                acl_metadata=acl_metadata,
            )
        else:
            message = content

        if receiver_addr == self.addr:
            if not receiver_id:
                receiver_id = message.receiver_id
            # internal message

            success = self._send_internal_message(receiver_id, message)
        else:
            success = await self._send_external_message(receiver_addr, message)

        return success

    def _send_internal_message(self, receiver_id, message) -> bool:
        """
        Sends a message to an agent that lives in the same container
        :param receiver_id: ID of the receiver
        :param message:
        :return: boolean indicating whether sending was successful
        """

        receiver = self._agents.get(receiver_id, None)
        if receiver is None:
            logger.warning(
                f"Sending internal message not successful, receiver id unknown;{receiver_id}"
            )
            return False
        # TODO priority assignment could be specified here,
        priority = 0
        if hasattr(message, "split_content_and_meta"):
            content, meta = message.split_content_and_meta()
        else:
            content = message
            meta = {}
        meta["network_protocol"] = "tcp"
        receiver.inbox.put_nowait((priority, content, meta))
        return True

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
