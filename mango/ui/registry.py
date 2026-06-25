import asyncio
import json
import logging
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    source_addr: str
    source_aid: str
    target_addr: str
    target_aid: str
    conn_type: str = "tcp"  # tcp | mqtt | internal
    direction: str = "bi"   # uni | bi


@dataclass
class AgentInfo:
    aid: str
    container_addr: str
    status: str = "active"


@dataclass
class ContainerInfo:
    addr: str
    name: str


class TopologyRegistry:
    """Registry that sits above containers and tracks agents, connections,
    and infrastructure health. Containers opt in by calling registry.register(container).
    Agents opt in via visible=True (default) in their __init__.
    """

    def __init__(self):
        self._container_refs: dict[str, object] = {}
        self._containers: dict[str, ContainerInfo] = {}
        self._agents: dict[str, AgentInfo] = {}
        self._connections: list[ConnectionInfo] = []
        self._ws_clients: set = set()

    def register(self, container) -> None:
        """Register a container with this registry."""
        container._registry = self
        addr_str = str(container.addr)
        self._container_refs[addr_str] = container
        self._containers[addr_str] = ContainerInfo(
            addr=addr_str,
            name=getattr(container, "name", addr_str) or addr_str,
        )
        # pick up agents already registered before this call
        for aid, agent in container._agents.items():
            if getattr(agent, "_visible", True):
                self._agents[f"{addr_str}:{aid}"] = AgentInfo(
                    aid=aid, container_addr=addr_str
                )

    def add_connection(
        self,
        source,
        target,
        conn_type: str = "tcp",
        direction: str = "bi",
    ) -> None:
        """Declare a connection between two agents.

        source / target are AgentAddress objects (protocol_addr, aid).
        """
        self._connections.append(
            ConnectionInfo(
                source_addr=str(source.protocol_addr),
                source_aid=source.aid,
                target_addr=str(target.protocol_addr),
                target_aid=target.aid,
                conn_type=conn_type,
                direction=direction,
            )
        )
        self._broadcast()

    async def check_health(self) -> None:
        """Poll each visible agent for its health status.

        Agents can optionally implement ``async def health_check(self) -> str``
        returning a status string (e.g. "active", "idle", "error").
        """
        for key, info in list(self._agents.items()):
            container = self._container_refs.get(info.container_addr)
            if container is None:
                continue
            agent = container._agents.get(info.aid)
            if agent is None:
                continue
            if hasattr(agent, "health_check"):
                try:
                    status = await agent.health_check()
                    info.status = str(status)
                except Exception:
                    info.status = "error"
        self._broadcast()

    # ------------------------------------------------------------------
    # Called by Container hooks
    # ------------------------------------------------------------------

    def on_agent_registered(self, container, agent, aid: str) -> None:
        addr_str = str(container.addr)
        self._agents[f"{addr_str}:{aid}"] = AgentInfo(
            aid=aid, container_addr=addr_str
        )
        self._broadcast()

    def on_agent_deregistered(self, container, aid: str) -> None:
        addr_str = str(container.addr)
        self._agents.pop(f"{addr_str}:{aid}", None)
        self._broadcast()

    # ------------------------------------------------------------------
    # WebSocket / server
    # ------------------------------------------------------------------

    async def start_server(self, host: str = "localhost", port: int = 8000) -> None:
        """Start the UI server inside the running asyncio loop.

        Call this after activate() so the loop is already running.
        """
        try:
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "Install mango-agents[ui] to use the topology UI: "
                "pip install mango-agents[ui]"
            ) from exc

        from .server import create_app

        app = create_app(self)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.ensure_future(server.serve())
        logger.info("Topology UI available at http://%s:%d", host, port)

    async def _connect_client(self, ws) -> None:
        self._ws_clients.add(ws)
        await ws.send_text(json.dumps(self._snapshot()))

    def _disconnect_client(self, ws) -> None:
        self._ws_clients.discard(ws)

    def _broadcast(self) -> None:
        if not self._ws_clients:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # loop not running yet; clients get full state on connect
        data = json.dumps(self._snapshot())
        for ws in list(self._ws_clients):
            loop.create_task(ws.send_text(data))

    def _snapshot(self) -> dict:
        return {
            "containers": [asdict(c) for c in self._containers.values()],
            "agents": [asdict(a) for a in self._agents.values()],
            "connections": [asdict(c) for c in self._connections],
        }
