import threading
import time
from abc import ABC
from functools import partial

import pika


class RabbitModule(ABC):
    def __init__(self, *, name: str, subscr_topics, pub_topics, broker):
        """

        :param name: name of the module (str)
        :param subscr_topics: List of string and integer tuples for subscribed topics:[ (topic, qos)]
        e.g.[("my/topic", 0), ("another/topic", 2)]
        :param pub_topics: List of string and integer tuples for publishing topics:[ (topic, qos)]
        :param broker: MQTT broker
        :param log_level

        """
        super().__init__()
        self.subscr_topics = subscr_topics

        self.pub_topics = pub_topics

        self.thread_active = False
        self.thread_running = False
        self.mq_thread = threading.Thread(target=self.run_mq)

        self.known_registers = []
        self.sub_channel = None
        self.sub_connection = None

        self.setup_done = False

        # set up publishing connection
        # we separate this because each thread needs its own connection and we want to be able to call
        # publish from outside
        self.pub_connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=broker[0], port=broker[1])
        )

        self.pub_channel = self.pub_connection.channel()

        for topic in self.pub_topics:
            # self.pub_channel.queue_declare(queue=topic[0])
            self.pub_channel.exchange_declare(exchange=topic[0], exchange_type="fanout")

        # run the sub thread
        self.mq_thread.start()

        # make sure all our connections etc. are set up before we attempt to access them from outside
        while not self.thread_active:
            continue

    ### The following methods might be changed, in case we use another MQ paradigme such as rabbitMQ, zeroMQ...)
    def add_message_callback(self, topic, fkt):
        self.known_registers.append((topic, fkt))

    def start_mq_thread(self):
        self.thread_running = True

        # wait for all the exchanges and queues to be set up
        while not self.setup_done:
            continue

    def run_mq(self):
        # set up stuff
        self.sub_connection = pika.BlockingConnection(
            pika.ConnectionParameters(host="localhost")
        )
        self.sub_channel = self.sub_connection.channel()

        for topic in self.subscr_topics:
            # self.sub_channel.queue_declare(queue=topic[0])
            self.sub_channel.exchange_declare(exchange=topic[0], exchange_type="fanout")

        self.thread_active = True

        # run loop
        while self.thread_active:
            if self.thread_running:

                # set up saved callback
                for cb in self.known_registers:
                    queues = []
                    result = self.sub_channel.queue_declare(queue="", exclusive=True)
                    queues.append(result.method.queue)
                    self.sub_channel.queue_bind(exchange=cb[0], queue=queues[-1])

                    f = partial(self.bind_callback, cb[0], cb[1])
                    self.sub_channel.basic_consume(
                        queue=queues[-1], on_message_callback=f, auto_ack=True
                    )

                self.setup_done = True

                # this loops until it is stopped from outside
                # essentially what happens in start_consuming with extra flag check
                while self.sub_channel._consumer_infos and self.thread_running:
                    self.sub_channel.connection.process_data_events(time_limit=1)

                self.sub_channel.cancel()

            else:
                time.sleep(1)

        # teardown

    def end_mq_thread(self):
        # self.pub_connection.close()
        self.thread_active = False
        self.thread_running = False

    def publish_mq_message(self, topic, payload):
        self.pub_channel.basic_publish(exchange=topic, routing_key="", body=payload)

    # the actual binding to whatever signature the frameworks callbacks have happens here
    def bind_callback(self, topic, func, ch, method, properties, message):
        func(payload=message, topic=topic)
