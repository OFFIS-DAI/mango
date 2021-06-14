from abc import ABC, abstractmethod
from typing import Any, Dict

from ..util.scheduling import ScheduledTask
from typing import *

T = TypeVar('T')
class RoleContext(ABC):

    @abstractmethod
    def get_or_create_model(self, cls: Type[T]) -> T:
        pass

    @abstractmethod
    def update(self, role_model):
        pass

    @abstractmethod
    def subscribe_model(self, obj, role_model_type):
        pass

    @abstractmethod
    def subscribe_message(self, obj, method, message_condition):
        pass

    @abstractmethod
    def schedule_task(self, task : ScheduledTask):
        pass

    @abstractmethod
    async def send_message(self, content,
                receiver_addr: Union[str, Tuple[str, int]], *,
                receiver_id: Optional[str] = None,
                create_acl: bool = False,
                acl_metadata: Optional[Dict[str, Any]] = None,
                mqtt_kwargs: Dict[str, Any] = None,
                ):
        pass

    @abstractmethod
    def addr(self):
        pass

    @abstractmethod
    def aid(self):
        pass


class Role(ABC):

    def __init__(self) -> None:
        pass

    def bind(self, context : RoleContext):
        self._context = context

    @property
    def context(self):
        return self._context

    def setup(self) -> None:
        pass

    def on_change_model(self, model) -> None:
        pass

    def handle_msg(self, content, meta: Dict[str, Any]) -> None:
        pass

    async def on_stop(self) -> None:
        pass


class SimpleReactiveRole(Role):

    def setup(self):
        self.context.subscribe_message(self, self.handle_msg, self.is_applicable)

    @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any], agent_context) -> None:
        pass

    @abstractmethod
    def is_applicable(self, content, meta: Dict[str, Any]):
        return True

class ProactiveRole(Role):

    @abstractmethod
    def setup(self) -> None:
        pass
