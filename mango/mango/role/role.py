from abc import ABC, abstractmethod
from typing import Any, Dict

class Role(ABC):

    def __init__(self) -> None:
        pass

    def setup(self, agent_context):
        pass

    def on_change_model(self, model, agent_context) -> None:
        pass

    def handle_msg(self, content, meta: Dict[str, Any], agent_context) -> None:
        pass

    def is_applicable(self, content, meta: Dict[str, Any]):
        return True

    async def on_stop(self) -> None:
        pass


class ReactiveRole(Role):

    @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any], agent_context) -> None:
        pass

    @abstractmethod
    def is_applicable(self, content, meta):
        return True
