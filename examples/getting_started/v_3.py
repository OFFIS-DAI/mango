from mango import Agent


class HelloWorldAgent(Agent):
    def __init__(self, container, other_addr, other_id):
        super().__init__(container)
        self.schedule_instant_acl_message(
            receiver_addr=other_addr,
            receiver_id=other_id,
            content="Hello world!",
        )

    def handle_message(self, content, meta):
        print(f"Received a message with the following content: {content}")
