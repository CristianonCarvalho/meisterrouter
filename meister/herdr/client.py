import asyncio
import itertools
import json
import logging
import os
from typing import Optional, Callable, Awaitable, Any, Union

logger = logging.getLogger(__name__)


class HerdrRPCError(Exception):
    """Exception raised when Herdr JSON-RPC server returns an error response."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"JSON-RPC Error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class HerdrConnectionError(OSError):
    """Exception raised when connection to Herdr UNIX socket fails or disconnects."""
    pass


class HerdrSocketClient:
    """Async JSON-RPC client communicating with Herdr UNIX domain socket."""

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or os.environ.get("HERDR_SOCKET_PATH") or os.environ.get("HERDR_SOCKET")
        if not self.socket_path:
            candidates = [
                os.path.expanduser("~/.config/herdr/herdr.sock"),
                os.path.expanduser("~/.herdr/herdr.sock"),
                "/tmp/herdr.sock",
            ]
            for c in candidates:
                if os.path.exists(c):
                    self.socket_path = c
                    break
        if not self.socket_path:
            raise ValueError(
                "HERDR_SOCKET_PATH environment variable not set and socket_path not provided."
            )
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._id_counter = itertools.count(1)
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._event_callbacks: list[Callable[[dict], Union[Awaitable[None], None]]] = []
        self._subscribed = False
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Establish UNIX domain socket connection and spawn background message listener."""
        if self.is_connected:
            return

        try:
            self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
        except OSError as e:
            raise HerdrConnectionError(
                f"Failed to connect to Herdr UNIX socket at '{self.socket_path}': {e}"
            ) from e

        self._listen_task = asyncio.create_task(self._listen_loop())

    async def close(self) -> None:
        """Close connection and clean up background tasks and pending requests."""
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        # Cancel any remaining pending futures
        for req_id, fut in list(self._pending_requests.items()):
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()
        self._subscribed = False

    async def __aenter__(self) -> "HerdrSocketClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _listen_loop(self) -> None:
        """Background loop reading JSON-RPC responses and incoming notifications."""
        assert self._reader is not None
        try:
            while not self._reader.at_eof():
                line = await self._reader.readline()
                if not line:
                    break
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                try:
                    payload = json.loads(line_str)
                except json.JSONDecodeError as e:
                    logger.warning("Failed to decode JSON-RPC message: %s", e)
                    continue

                req_id = payload.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(payload)
                elif payload.get("error") and self._pending_requests:
                    # Servidor retornou erro sem ecoar o ID (ex: id="" ou null)
                    _, fut = self._pending_requests.popitem()
                    if not fut.done():
                        fut.set_result(payload)
                else:
                    # Notification or Event
                    event_data = payload.get("params", payload)
                    for cb in self._event_callbacks:
                        try:
                            res = cb(event_data)
                            if asyncio.iscoroutine(res):
                                asyncio.create_task(res)
                        except Exception as e:
                            logger.error("Error executing Herdr event callback: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("HerdrSocketClient listener loop error: %s", e)
        finally:
            # Mark all pending requests as disconnected/cancelled
            for fut in list(self._pending_requests.values()):
                if not fut.done():
                    fut.cancel()
            self._pending_requests.clear()

    async def _call(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = 30.0,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        if not self.is_connected:
            await self.connect()

        req_id = f"req_{next(self._id_counter)}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_requests[req_id] = future

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        encoded = (json.dumps(message) + "\n").encode("utf-8")

        async with self._lock:
            if not self.is_connected or self._writer is None:
                self._pending_requests.pop(req_id, None)
                raise HerdrConnectionError("Cannot send request; client is not connected")
            try:
                self._writer.write(encoded)
                await self._writer.drain()
            except (OSError, BrokenPipeError) as e:
                self._pending_requests.pop(req_id, None)
                await self.close()
                raise HerdrConnectionError(f"Failed to send request: {e}") from e

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timed out waiting for response to method '{method}' (id: {req_id})")
        except asyncio.CancelledError as e:
            raise HerdrConnectionError("Connection closed while waiting for response") from e
        finally:
            self._pending_requests.pop(req_id, None)

        if "error" in response and response["error"] is not None:
            err = response["error"]
            if isinstance(err, dict):
                raise HerdrRPCError(
                    code=err.get("code", -32000),
                    message=err.get("message", "Unknown RPC error"),
                    data=err.get("data"),
                )
            raise HerdrRPCError(code=-32000, message=str(err))

        return response.get("result")

    async def split_pane(
        self,
        direction: str = "right",
        command: Optional[list[str]] = None,
        split_ratio: float = 0.5,
        cwd: Optional[str] = None,
    ) -> str:
        """Split a pane in Herdr. Returns the new pane_id."""
        params: dict[str, Any] = {
            "direction": direction,
            "ratio": split_ratio,
            "split_ratio": split_ratio,
        }
        if cwd:
            params["cwd"] = cwd
        if command is not None:
            params["command"] = command

        result = await self._call("pane.split", params)
        pane_id = ""
        if isinstance(result, dict):
            if "pane" in result and isinstance(result["pane"], dict):
                pane_id = str(result["pane"].get("pane_id", ""))
            elif "pane_id" in result:
                pane_id = str(result["pane_id"])
        else:
            pane_id = str(result) if result else ""

        if command and pane_id:
            cmd_str = " ".join(command) if isinstance(command, list) else str(command)
            try:
                await self.send_text(pane_id, f"{cmd_str}\n")
            except Exception as e:
                logger.debug("Could not send command to new pane %s: %s", pane_id, e)

        return pane_id

    async def get_current_pane(self) -> dict[str, Any]:
        """Get information about the currently focused pane and workspace in Herdr.

        Returns a dictionary containing 'pane_id', 'workspace_id', 'cwd', etc.
        """
        result = await self._call("pane.current", {})
        if isinstance(result, dict):
            if "pane" in result and isinstance(result["pane"], dict):
                return result["pane"]
            return result
        return {}

    async def read_pane(
        self,
        pane_id: str,
        lines: int = 100,
        source: str = "recent_unwrapped",
    ) -> str:
        """Read recent terminal output lines from a pane using recent_unwrapped source."""
        params: dict[str, Any] = {
            "pane_id": pane_id,
            "source": source,
            "lines": lines,
            "format": "text",
            "strip_ansi": True,
        }
        result = await self._call("pane.read", params)
        if isinstance(result, dict):
            if "read" in result and isinstance(result["read"], dict):
                return str(result["read"].get("text", ""))
            return str(result.get("text", result.get("output", "")))
        return str(result)

    async def prompt_agent(
        self,
        pane_id: str,
        prompt: str,
        wait_until: str = "done",
        timeout_ms: int = 180000,
    ) -> dict:
        """Send prompt to agent in pane and await completion."""
        params: dict[str, Any] = {
            "target": pane_id,
            "text": prompt,
            "pane_id": pane_id,
            "prompt": prompt,
        }
        if wait_until:
            params["wait"] = {
                "timeout_ms": timeout_ms,
                "until": [wait_until] if isinstance(wait_until, str) else list(wait_until),
            }
        timeout_sec = (timeout_ms / 1000.0) + 10.0
        result = await self._call("agent.prompt", params, timeout=timeout_sec)
        if isinstance(result, dict):
            if "status" not in result:
                result["status"] = "done"
            if "pane_id" not in result:
                result["pane_id"] = pane_id
            return result
        return {"status": "done", "pane_id": pane_id, "result": result}

    async def send_text(self, pane_id: str, text: str) -> None:
        """Send text directly to a pane."""
        params = {
            "pane_id": pane_id,
            "text": text,
        }
        await self._call("pane.send_text", params)

    async def send_keys(self, pane_id: str, keys: Union[str, list[str]]) -> None:
        """Send keystrokes to a specific pane."""
        key_list = [keys] if isinstance(keys, str) else list(keys)
        params = {
            "pane_id": pane_id,
            "keys": key_list,
        }
        await self._call("pane.send_keys", params)

    async def send_interrupt(self, pane_id: str) -> None:
        """Send SIGINT / Ctrl+C to pane to halt execution or runaway retries."""
        await self.send_keys(pane_id, "ctrl+c")

    async def subscribe_events(
        self, callback: Callable[[dict], Union[Awaitable[None], None]]
    ) -> None:
        """Subscribe to Herdr reactive events and invoke callback on each event."""
        self._event_callbacks.append(callback)
        if not self._subscribed:
            subscriptions = [
                {"type": "pane.created"},
                {"type": "pane.closed"},
                {"type": "pane.exited"},
                {"type": "pane.agent_detected"},
                {"type": "workspace.focused"},
            ]
            try:
                await self._call("events.subscribe", {"subscriptions": subscriptions})
                self._subscribed = True
            except Exception as e:
                logger.debug("events.subscribe failed: %s", e)

    async def show_notification(self, message: str, title: str = "MeisterRouter") -> None:
        """Show native Herdr status notification."""
        params = {
            "title": title,
            "body": message,
            "message": message,
        }
        try:
            await self._call("notification.show", params)
        except Exception as e:
            logger.debug("notification.show failed: %s", e)

    # Alias for close() to maintain backwards and cross-call compatibility
    disconnect = close

