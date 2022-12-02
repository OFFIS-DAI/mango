import pkg_resources

from mango.container.factory import create as create_container
from mango.agent.core import Agent
from mango.role.core import RoleAgent
from mango.role.api import Role, RoleContext

pkg_resources.declare_namespace(__name__)

