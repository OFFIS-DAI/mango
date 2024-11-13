import logging
from typing import Any

from mango.container.core import Container
from mango.container.external_coupling import ExternalSchedulingContainer
from mango.container.mqtt import MQTTContainer
from mango.container.tcp import TCPContainer
from mango.messages.codecs import JSON

from ..messages.codecs import Codec
from ..util.clock import AsyncioClock, Clock, ExternalClock

logger = logging.getLogger(__name__)

TCP_CONNECTION = "tcp"
MQTT_CONNECTION = "mqtt"
EXTERNAL_CONNECTION = "external_connection"


def create_mqtt(
    broker_addr: tuple | dict | str,
    client_id: str,
    codec: Codec = None,
    clock: Clock = None,
    inbox_topic: str | None = None,
    copy_internal_messages: bool = False,
    **kwargs,
):
    if codec is None:
        codec = JSON()
    if clock is None:
        clock = AsyncioClock()

    return MQTTContainer(
        client_id=client_id,
        broker_addr=broker_addr,
        clock=clock,
        codec=codec,
        inbox_topic=inbox_topic,
        copy_internal_messages=copy_internal_messages,
        **kwargs,
    )


def create_external_coupling(
    codec: Codec = None,
    clock: Clock = None,
    addr: None | str | tuple[str, int] = None,
    **kwargs: dict[str, Any],
):
    if codec is None:
        codec = JSON()
    if clock is None:
        clock = ExternalClock()

    return ExternalSchedulingContainer(addr=addr, codec=codec, clock=clock, **kwargs)


def create_tcp(
    addr: str | tuple[str, int],
    codec: Codec = None,
    clock: Clock = None,
    copy_internal_messages: bool = False,
    auto_port=False,
    **kwargs: dict[str, Any],
) -> Container:
    """
    This method is called to instantiate a tcp container

    :param codec: Defines the codec to use. Defaults to JSON
    :param clock: The clock that the scheduler of the agent should be based on. Defaults to the AsyncioClock
    :param addr: the address to use. it has to be a tuple of (host, port).

    :return: The instance of a TCPContainer
    """
    if codec is None:
        codec = JSON()
    if clock is None:
        clock = AsyncioClock()
    if isinstance(addr, str):
        host, port = addr.split(":")
        addr = (host, int(port))

    # initialize TCPContainer
    return TCPContainer(
        addr=(addr[0], 0) if auto_port else addr,
        codec=codec,
        clock=clock,
        copy_internal_messages=copy_internal_messages,
        **kwargs,
    )
