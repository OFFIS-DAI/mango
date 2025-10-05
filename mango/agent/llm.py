import logging

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

from ..messages.message import AgentAddress
from .core import State

MANGO_SYSTEM_PROMPT = """
You are a mango framework agent. You run inside a container. The container handles networking and scheduling.
Your job is to interpret incoming messages, decide whether to act locally, message other agents, or schedule work using the Mango API.

## Identity & Role

* You are: An LLM-driven Mango agent.
* You live in a container: all communication and scheduling is delegated to the container.
* Primary goals:
  1. Understand objectives from messages.
  2. Use the correct Agent API methods.
  3. Produce structured JSON outputs that call tools deterministically.

---

## Mango Agent API

You control an instance of a class derived from `Agent`.
The following delegate methods are available to you through `AgentDelegates`:

### Messaging

```
await self.send_message(content, receiver_addr: AgentAddress, **kwargs) -> bool
```

Schedules and sends a message asynchronously via the container.
Use this for inter-agent communication.

### Neighbor Discovery

```
self.neighbors(state: str = "NORMAL") -> list[AgentAddress]
```

Returns neighboring agent addresses filtered by state. The state is a string and can be:

* NORMAL — usable neighbor
* INACTIVE — neighbor exists but link inactive
* BROKEN — neighbor exists but unusable

### Address

```
self.addr -> AgentAddress
self.aid  -> str
self.current_timestamp -> float
```

`self.addr` returns the agent's own `AgentAddress`.
`AgentAddress` structure:

```
{
  "aid": "string (logical identifier)",
  "protocol_addr": tuple (first element ip, second port)
}
```
Both fields are required.

### Custom code scheduling

self.schedule_string_task("python code")

The python code must be executable as is, be careful not to use unavailable packages. Only use this
if absolutely necessary.
---

## Decision Guidelines

* Use `send_message` when communicating with other agents.
* Use `neighbors` to discover peers dynamically. Do not hardcode addresses.
* Use the `schedule_string_task` if you want to achieve something with generated python code, which
  can surely not be done with any other tool
* Reply directly when you can answer without external actions.
---

## Style

* Prefer concise, schema-first JSON outputs.
* When a tool call is wanted, be sure to use the correct format

Example:
{
  "tool": "get_weather_tool",
  "args": {"city": "Paris", "unit": "C"}
}

* Use lowercase snake_case for keys.
* Do not include internal reasoning in outputs.
* Assume tools map directly to the Python API methods of the Agent base class.

"""

logger = logging.getLogger(__name__)


class LLMModule:
    """The mano LLM module is a access module to use arbitrary LLMs in a mango Agent.
    The advantage of this module is that its creating a context using the system prompt
    explaining the mango multi-agent world. This means core concepts and core mango APIs
    as tools.

    Further, it is still possible to fully customize the LLM behavior by providing an additional
    system prompt (which will be added to the mango System prompt), and provide additional tools.

    Any OpenAI-style or ollama API can be used. If you are running your model with ollama set
    ollama=True (default).

    Underneath pydantic-ai is used to perform the actual tool calling. Therefore it is beneficial to
    use pydantic models in tools provided.
    """

    def __init__(
        self,
        agent,
        model="gpt-oss:20b",
        base_url="http://localhost:11434/v1",
        api_key="",
        additional_system_prompt="",
        additional_tools=[],
        ollama=True,
    ):
        """Creating LLMModule

        :param agent: _description_
        :type agent: _type_
        :param model: _description_, defaults to "gpt-oss:20b"
        :type model: str, optional
        :param base_url: _description_, defaults to "http://localhost:11434/v1"
        :type base_url: str, optional
        :param api_key: _description_, defaults to ""
        :type api_key: str, optional
        :param additional_system_prompt: _description_, defaults to ""
        :type additional_system_prompt: str, optional
        :param additional_tools: _description_, defaults to []
        :type additional_tools: list, optional
        :param ollama: _description_, defaults to True
        :type ollama: bool, optional
        """
        self.agent = agent

        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        if ollama:
            provider = OllamaProvider(base_url=base_url, api_key=api_key)

        self.model = OpenAIChatModel(model_name=model, provider=provider)
        system_prompt = MANGO_SYSTEM_PROMPT + additional_system_prompt
        mango_tools = [
            self.addr,
            self.timestamp,
            self.send_message,
            self.neighbors,
            self.schedule_string_task,
        ]
        self.llm = PydanticAgent(
            self.model,
            tools=mango_tools + additional_tools,
            system_prompt=system_prompt,
        )

    def addr(self):
        """Address of this agent
        `AgentAddress` structure:

        ```
        {
          "aid": "string (logical identifier)",
          "protocol_addr": tuple (first element ip, second port)
        }
        ```

        :return: AgentAddress of self
        :rtype: AgentAddress
        """
        return self.agent.addr

    def timestamp(self):
        """Return the timestamp as float

        :return: Return the timestamp of the simulation (agent)
        :rtype: float
        """
        return self.agent.current_timestamp

    def schedule_string_task(self, task_code: str):
        """Schedule a string task, the string has to be a fully executable python code snippet. Only
        use if necessary.

        :param task_code: the task to execute as python code string (executable as is)
        :type task_code: str
        """
        logger.warning("Invoking custom string task = %s", task_code)
        namespace = {"self", self}
        exec(task_code, namespace)
        coro = namespace["execute"]()
        self.agent.schedule_instant_task(coro)

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ) -> bool:
        """
        Send a message to the agent with the address receiver_addr and with the content content.

        :param content: the content to send
        :type content: Any
        :param receiver_addr: the receiver address
        :type receiver_addr: AgentAddress
        :return: true if successfull
        :rtype: bool
        """
        return await self.agent.send_message(
            content, receiver_addr=receiver_addr, **kwargs
        )

    def neighbors(self, state: str = "NORMAL") -> list[AgentAddress]:
        """Return the neighbors of the agent (controlled by the topology api).

        :return: the list of agent addresses filtered by state
        :rtype: list[AgentAddress]
        """
        return self.agent.neighbors(state=State[state])

    async def invoke(self, prompt: str, **kwargs):
        """Invoke the LLM directy

        :param prompt: the prompt as a string
        :type prompt: str
        :return: LLM answer
        :rtype: str
        """
        return await self.llm.run(prompt, **kwargs)
