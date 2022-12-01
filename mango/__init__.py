import pkg_resources

from mango.container.factory import create as create_container
from mango.agent.core import Agent
from mango.role.core import RoleAgent

pkg_resources.declare_namespace(__name__)

