import asyncio
import json
import logging
import os
from typing import Optional, Callable, Dict, Any, List

logger = logging.getLogger(__name__)


class MockHerdrServer:
    def __init__(self, sock_path: str, schema_path: Optional[str] = None, validate_schema: bool = True):
        self.sock_path = sock_path
        self.validate_schema = validate_schema
        self.schema_path = schema_path or os.path.join(os.path.dirname(__file__), "herdr_schema.json")
        self.server: Optional[asyncio.Server] = None
        self.clients: List[asyncio.StreamWriter] = []
        self.received_requests: List[Dict[str, Any]] = []
        self.pane_counter = 1
        self.custom_handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self.method_schemas: Dict[str, Dict[str, Any]] = {}
        if self.validate_schema and os.path.exists(self.schema_path):
            self._load_schema()

    def _load_schema(self):
        try:
            with open(self.schema_path, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
            req = schema_data.get("schemas", {}).get("request", {})
            defs = req.get("$defs", {})
            for item in req.get("oneOf", []):
                m = item.get("properties", {}).get("method", {}).get("const")
                ref = item.get("properties", {}).get("params", {}).get("$ref", "")
                if m:
                    def_name = ref.split("/")[-1] if ref else None
                    param_def = defs.get(def_name, {}) if def_name else item.get("properties", {}).get("params", {})
                    self.method_schemas[m] = {
                        "allowed_properties": set(param_def.get("properties", {}).keys()),
                        "required": set(param_def.get("required", [])),
                        "allow_additional": param_def.get("additionalProperties", False),
                    }
        except Exception as e:
            logger.warning("Failed loading Herdr schema for mock server: %s", e)

    def _validate_request(self, method: str, params: Any) -> Optional[str]:
        if not self.validate_schema or not self.method_schemas:
            return None
        if method not in self.method_schemas:
            return f"Method '{method}' not found in Herdr API schema"
        m_info = self.method_schemas[method]
        if not isinstance(params, dict):
            return f"Params for '{method}' must be a JSON object, got {type(params).__name__}"
        missing = m_info["required"] - set(params.keys())
        if missing:
            return f"Missing required parameters for '{method}': {sorted(list(missing))}"
        if not m_info["allow_additional"]:
            extra = set(params.keys()) - m_info["allowed_properties"]
            if extra:
                return f"Disallowed unknown parameters for '{method}': {sorted(list(extra))}. Allowed: {sorted(list(m_info['allowed_properties']))}"
        return None

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
                val_err = self._validate_request(method, params)
                if val_err:
                    error = {"code": -32602, "message": f"Schema validation failed: {val_err}"}
                    result = None
                elif method in self.custom_handlers:
                    handler_res = self.custom_handlers[method](params)
                    if isinstance(handler_res, dict) and "__error__" in handler_res:
                        error = handler_res["__error__"]
                        result = None
                    else:
                        result = handler_res
                elif method == "pane.split":
                    self.pane_counter += 1
                    result = f"w1:p{self.pane_counter}"
                elif method == "pane.current":
                    result = {"pane": {"pane_id": "w1:p1", "workspace_id": "w1"}}
                elif method == "pane.read":
                    result = "mock terminal output\n[workspace active]"
                elif method == "agent.prompt":
                    result = {"status": "done", "pane_id": params.get("target") or params.get("pane_id")}
                elif method == "events.subscribe":
                    result = {"subscribed": True}
                elif method == "pane.send_keys":
                    result = {"success": True}
                elif method == "tab.create":
                    self.pane_counter += 1
                    tab_id = f"t{self.pane_counter}"
                    pane_id = f"w1:p{self.pane_counter}"
                    result = {
                        "type": "tab_created",
                        "tab": {
                            "tab_id": tab_id,
                            "workspace_id": params.get("workspace_id") or "w1",
                            "number": self.pane_counter,
                            "label": params.get("label") or "tab",
                            "focused": bool(params.get("focus", False)),
                            "pane_count": 1,
                            "agent_status": "idle",
                        },
                        "root_pane": {
                            "pane_id": pane_id,
                            "workspace_id": params.get("workspace_id") or "w1",
                            "cwd": params.get("cwd") or "",
                            "focused": bool(params.get("focus", False)),
                        },
                    }
                elif method == "tab.close":
                    result = {"ok": True}
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
