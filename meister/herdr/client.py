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
        self.socket_path = socket_path or os.environ.get("HERDR_SOCKET_PATH")
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

        # Fail any remaining pending futures
        for req_id, fut in list(self._pending_requests.items()):
            if not fut.done():
                fut.set_exception(HerdrConnectionError("Connection closed while request was pending"))
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
            # Mark all pending requests as disconnected
            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.set_exception(HerdrConnectionError("Connection to Herdr socket terminated"))
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
    ) -> str:
        """Split a pane in Herdr. Returns the new pane_id."""
        params: dict[str, Any] = {
            "direction": direction,
            "split_ratio": split_ratio,
        }
        if command is not None:
            params["command"] = command

        result = await self._call("pane.split", params)
        if isinstance(result, dict) and "pane_id" in result:
            return str(result["pane_id"])
        return str(result)

    async def read_pane(self, pane_id: str, lines: int = 100) -> str:
        """Read recent terminal output lines from a pane."""
        params = {
            "pane_id": pane_id,
            "lines": lines,
        }
        result = await self._call("pane.read", params)
        if isinstance(result, dict) and "output" in result:
            return str(result["output"])
        return str(result)

    async def prompt_agent(
        self,
        pane_id: str,
        prompt: str,
        wait_until: str = "done",
        timeout_ms: int = 180000,
    ) -> dict:
        """Send prompt to agent in pane and await completion."""
        params = {
            "pane_id": pane_id,
            "prompt": prompt,
            "wait_until": wait_until,
            "timeout_ms": timeout_ms,
        }
        timeout_sec = (timeout_ms / 1000.0) + 10.0
        result = await self._call("agent.prompt", params, timeout=timeout_sec)
        if isinstance(result, dict):
            return result
        return {"result": result}

    async def subscribe_events(
        self, callback: Callable[[dict], Union[Awaitable[None], None]]
    ) -> None:
        """Subscribe to Herdr reactive events and invoke callback on each event."""
        self._event_callbacks.append(callback)
        if not self._subscribed:
            await self._call("events.subscribe", {})
            self._subscribed = True

    async def send_keys(self, pane_id: str, keys: str) -> None:
        """Send keystrokes to a specific pane."""
        params = {
            "pane_id": pane_id,
            "keys": keys,
        }
        await self._call("pane.send_keys", params)

    async def send_interrupt(self, pane_id: str) -> None:
        """Send SIGINT / Ctrl+C to pane to halt execution or runaway retries."""
        await self.send_keys(pane_id, "\x03")

    async def show_notification(self, message: str) -> None:
        """Show native Herdr status notification."""
        params = {"message": message}
        await self._call("notification.show", params)

    # Alias for close() to maintain backwards and cross-call compatibility
    disconnect = close

