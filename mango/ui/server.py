from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

_STATIC = Path(__file__).parent / "static"


def create_app(registry):
    app = FastAPI(title="mango topology")

    @app.get("/")
    async def index():
        return HTMLResponse((_STATIC / "index.html").read_text())

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        await registry._connect_client(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep-alive; client sends nothing
        except WebSocketDisconnect:
            registry._disconnect_client(websocket)

    return app
