import asyncio
import logging
import struct

# The struct module performs conversions between python values and
# C structs represented as Python bytes objects.
# '!' refers to network byte order(= big-endian)
# 'L' refers to the c type 'unsigned long' and is a python uint32
# The header stores the number of bytes in the payload
HEADER = struct.Struct("!L")

logger = logging.getLogger(__name__)


class ContainerProtocol(asyncio.Protocol):
    """Protocol for implementing the TCP Container connection. Internally reads the asyncio transport object
    into a buffer and moves the read messages async to the container inbox."""

    def __init__(self, *, container, loop, codec):
        """

        :param container:
        :param loop:
        """
        super().__init__()

        self.codec = codec
        self.transport = None  # type: _SelectorTransport
        self.container = container
        self._loop = loop
        self._buffer = bytearray()

    def connection_made(self, transport):
        """

        :param transport:

        """
        self.transport = transport  # store transport object

    def connection_lost(self, exc):
        """

        :param exc:
        :return:
        """
        self.transport.close()
        super().connection_lost(exc)

    def eof_received(self):
        """

        :return:
        """
        return super().eof_received()

    def data_received(self, data):
        """

        :param data:
        :return:
        """
        required_read_size = None
        self._buffer.extend(data)
        while True:
            # We may have more then one message in the buffer,
            # so we loop over the buffer until we got all complete messages.

            if required_read_size is None and len(self._buffer) >= HEADER.size:
                # Received the complete header of a new message
                logger.debug("Received complete header of a message")

                required_read_size = HEADER.unpack_from(self._buffer)[0]
                # Unpack from buffer, according to the format of the struct.
                # The result is a tuple even if it contains exactly one item.
                # The buffer object reamins unchanged
                # The header is also in the buffer
                required_read_size += HEADER.size

            if (
                required_read_size is not None
                and len(self._buffer) >= required_read_size
            ):
                logger.debug("Received complete message")
                # At least one complete message is in the buffer
                # read the payload of the message
                data = self._buffer[HEADER.size : required_read_size]
                self._buffer = self._buffer[required_read_size:]
                required_read_size = None

                message = self.codec.decode(data)

                if hasattr(message, "split_content_and_meta"):
                    content, acl_meta = message.split_content_and_meta()
                    acl_meta["network_protocol"] = "tcp"
                else:
                    content, acl_meta = message, message

                # TODO priority is now always 0,
                #  but should be encoded in the message
                self.container.inbox.put_nowait((0, content, acl_meta))

            else:
                # No complete message in the buffer, nothing more to do.
                break

    def write(self, msg_payload):
        """
        Write the message (as bytes) to the connection.

        :param msg_payload:  message payload
        :return:
        """

        # get length of the payload and store the number of bytes as byte
        # object defined in Header

        header = HEADER.pack(len(msg_payload))

        message = header + msg_payload  # message is header and payload

        self.transport.write(message)

    async def shutdown(self):
        """
        Will close the transport and stop the writing task
        :return:
        """
        self.transport.close()  # this will cause the
        # self._task_process_out_msg to be cancelled
