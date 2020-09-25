"""This module contains the base class for basic modules that can be used
 inside agents to encapsulate complex functionality """

from .mqtt_module import MQTTModule
from .rabbit_module import RabbitModule
from .zero_module import ZeroModule


class BaseModule:
    """An agent can have multiple specialized modules which inherit
    from BaseModule. The all need to specify which messaging framework should
     be used for the internal message exchange between the modules.
     TODO write more
    """
    frameworks = {
        'mqtt': MQTTModule,
        'rabbit': RabbitModule,
        'zero': ZeroModule
    }

    def __init__(self, *, name: str, framework='mqtt', subscr_topics,
                 pub_topics, broker):
        """
        Initialization of the module
        :param name: name of the module (str)
        :param subscr_topics: List of string and integer tuples for subscribed
         topics:[ (topic, qos)] e.g.[("my/topic", 0), ("another/topic", 2)]
        :param pub_topics: List of string and integer tuples for publishing
         topics:[ (topic, qos)]
        :param broker: MQTT broker
        :param log_level

        """
        super().__init__()
        self.name = name
        self.subscr_topics = subscr_topics
        self.pub_topics = pub_topics
        self.broker = broker

        self.messenger = BaseModule.frameworks[framework](
            name=self.name,
            subscr_topics=self.subscr_topics,
            pub_topics=self.pub_topics,
            broker=self.broker)

        self.add_message_callback = self.messenger.add_message_callback
        self.start_mq_thread = self.messenger.start_mq_thread
        self.end_mq_thread = self.messenger.end_mq_thread
        self.publish_mq_message = self.messenger.publish_mq_message
        self.bind_callback = self.messenger.bind_callback

    def raise_exceptions(self, result):
        """
        Function used as a callback to raise exceptions
        :param result: result of the task
        """
        exception = result.exception()
        if exception is not None:
            raise exception

    # def log(self, client, userdata, level, buf):
    #     pass
    #     # self.logger.info(
    #     #     f"log: client: {str(client._client_id.decode('utf-8'))}
    #     - {buf}")
