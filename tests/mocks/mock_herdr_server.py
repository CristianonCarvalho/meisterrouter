import asyncio
import json
import logging
from typing import Optional, Callable, Dict, Any, List

logger = logging.getLogger(__name__)


class MockHerdrServer:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.server: Optional[asyncio.Server] = None
        self.clients: List[asyncio.StreamWriter] = []
        self.received_requests: List[Dict[str, Any]] = []
        self.pane_counter = 1
        self.custom_handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

    async def start(self) -> "MockHerdrServer":
        self.server = await asyncio.start_unix_server(self._handle_client, path=self.sock_path)
        return self

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.clients.append(writer)
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8").strip())
                except Exception:
                    continue

                self.received_requests.append(payload)
                req_id = payload.get("id")
                method = payload.get("method")
                params = payload.get("params", {})

                error = None
                if method in self.custom_handlers:
                    handler_res = self.custom_handlers[method](params)
                    if isinstance(handler_res, dict) and "__error__" in handler_res:
                        error = handler_res["__error__"]
                        result = None
                    else:
                        result = handler_res
                elif method == "pane.split":
                    self.pane_counter += 1
                    result = f"w1:p{self.pane_counter}"
                elif method == "pane.read":
                    result = "mock terminal output\n[workspace active]"
                elif method == "agent.prompt":
                    result = {"status": "done", "pane_id": params.get("pane_id")}
                elif method == "events.subscribe":
                    result = {"subscribed": True}
                elif method == "pane.send_keys":
                    result = {"success": True}
                elif method == "notification.show":
                    result = {"shown": True}
                else:
                    result = {"ok": True}

                if req_id is not None:
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                    }
                    if error is not None:
                        response["error"] = error
                    else:
                        response["result"] = result
                    writer.write((json.dumps(response) + "\n").encode("utf-8"))
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        finally:
            if writer in self.clients:
                self.clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def emit_event(self, event: Dict[str, Any]):
        message = {
            "jsonrpc": "2.0",
            "method": "events.notify",
            "params": event
        }
        data = (json.dumps(message) + "\n").encode("utf-8")
        for writer in list(self.clients):
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                pass

    def close(self):
        if self.server:
            self.server.close()
        for writer in list(self.clients):
            try:
                writer.close()
            except Exception:
                pass

    async def wait_closed(self):
        if self.server:
            await self.server.wait_closed()


async def run_mock_herdr_server(sock_path: str) -> MockHerdrServer:
    server = MockHerdrServer(sock_path)
    await server.start()
    return server
