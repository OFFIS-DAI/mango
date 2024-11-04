from mango import create_tcp_container, create_mqtt_container
from mango.util.clock import ExternalClock


def create_test_container(type, init_addr, repl_addr, codec):
    broker = ("127.0.0.1", 1883, 60)

    clock_man = ExternalClock(5)
    clock_ag = ExternalClock()
    if type == "tcp":
        container_man = create_tcp_container(
            addr=init_addr,
            codec=codec,
            clock=clock_man,
        )
        container_ag = create_tcp_container(
            addr=repl_addr,
            codec=codec,
            clock=clock_ag,
        )
    elif type == "mqtt":
        container_man = create_mqtt_container(
            broker_addr=broker,
            client_id="container_1",
            clock=clock_man,
            codec=codec,
            inbox_topic=init_addr,
            transport="tcp",
        )
        container_ag = create_mqtt_container(
            broker_addr=broker,
            client_id="container_2",
            clock=clock_ag,
            codec=codec,
            inbox_topic=repl_addr,
            transport="tcp",
        )
    elif type == "mqtt_minimal":
        container_man = create_mqtt_container(
            broker_addr=broker,
            client_id=init_addr,
            clock=clock_man,
            codec=codec,
        )
        container_ag = create_mqtt_container(
            broker_addr=broker,
            client_id=repl_addr,
            clock=clock_ag,
            codec=codec,
        )
    return container_man, container_ag
