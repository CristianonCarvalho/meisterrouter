import asyncio
import pytest
from tests.mocks.mock_herdr_server import run_mock_herdr_server
from tests.platform_marks import posix_only
from meister.herdr.client import HerdrSocketClient, HerdrRPCError


@posix_only
@pytest.mark.asyncio
async def test_client_split_and_read(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        client = HerdrSocketClient(sock_path)
        await client.connect()
        pane_id = await client.split_pane(direction="right", command=["echo", "worker"])
        assert pane_id == "w1:p2"

        output = await client.read_pane(pane_id)
        assert "mock terminal output" in output
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_prompt_agent(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            res = await client.prompt_agent("w1:p1", prompt="Implement auth tests", wait_until="done", timeout_ms=5000)
            assert isinstance(res, dict)
            assert res.get("status") == "done"
            assert res.get("pane_id") == "w1:p1"
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_prompt_agent_raises_on_agent_not_found(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)
    server.custom_handlers["agent.prompt"] = lambda p: {
        "__error__": {"code": -32000, "message": "agent_not_found: no agent in pane"}
    }

    try:
        async with HerdrSocketClient(sock_path) as client:
            with pytest.raises(HerdrRPCError) as exc_info:
                await client.prompt_agent("w1:p1", prompt="Fix bug")
            assert "agent_not_found" in exc_info.value.message
    finally:
        server.close()
        await server.wait_closed()



@posix_only
@pytest.mark.asyncio
async def test_client_subscribe_events(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    received_events = []

    async def on_event(event: dict):
        received_events.append(event)

    try:
        async with HerdrSocketClient(sock_path) as client:
            await client.subscribe_events(on_event)

            # Server emits event
            test_event = {"type": "agent_status", "pane_id": "w1:p2", "status": "done"}
            await server.emit_event(test_event)

            # Wait briefly for client listener task to process incoming notification
            for _ in range(20):
                if received_events:
                    break
                await asyncio.sleep(0.05)

            assert len(received_events) == 1
            assert received_events[0] == test_event
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_send_keys_and_notification(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            await client.send_keys("w1:p1", "ls -la\n")
            await client.send_interrupt("w1:p1")
            await client.show_notification("Orchestration finished successfully")

        # Verify requests reached the server
        methods = [r.get("method") for r in server.received_requests]
        assert "pane.send_keys" in methods
        assert "notification.show" in methods
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_socket_path_from_env(tmp_path, monkeypatch):
    sock_path = str(tmp_path / "herdr.sock")
    monkeypatch.setenv("HERDR_SOCKET_PATH", sock_path)
    server = await run_mock_herdr_server(sock_path)

    try:
        client = HerdrSocketClient()
        assert client.socket_path == sock_path
        await client.connect()
        assert client.is_connected
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_connection_error(tmp_path):
    bad_sock_path = str(tmp_path / "nonexistent.sock")
    client = HerdrSocketClient(bad_sock_path)
    with pytest.raises((FileNotFoundError, ConnectionRefusedError, OSError)):
        await client.connect()


def test_client_missing_socket_path(monkeypatch):
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
    monkeypatch.delenv("HERDR_SOCKET", raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    with pytest.raises(ValueError, match="HERDR_SOCKET_PATH"):
        HerdrSocketClient()


@posix_only
@pytest.mark.asyncio
async def test_client_rpc_error(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)
    server.custom_handlers["pane.split"] = lambda p: {
        "__error__": {"code": -32602, "message": "Invalid direction parameter"}
    }

    try:
        async with HerdrSocketClient(sock_path) as client:
            with pytest.raises(HerdrRPCError) as exc_info:
                await client.split_pane(direction="invalid_dir")
            assert exc_info.value.code == -32602
            assert "Invalid direction parameter" in exc_info.value.message
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_sync_event_callback(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    sync_events = []

    def on_sync_event(event: dict):
        sync_events.append(event)

    try:
        async with HerdrSocketClient(sock_path) as client:
            await client.subscribe_events(on_sync_event)
            test_event = {"type": "quota_exceeded", "pane_id": "w1:p3"}
            await server.emit_event(test_event)

            for _ in range(20):
                if sync_events:
                    break
                await asyncio.sleep(0.05)

            assert len(sync_events) == 1
            assert sync_events[0] == test_event
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_get_current_pane(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            current = await client.get_current_pane()
            assert isinstance(current, dict)
            assert current.get("pane_id") == "w1:p1"
            assert current.get("workspace_id") == "w1"
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_mock_herdr_server_validates_schema_and_rejects_invalid_params(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            # 1. Direct call with invalid parameters outside schema (Finding #12) must fail validation
            with pytest.raises(HerdrRPCError) as exc_info:
                await client._call("pane.split", {"direction": "right", "split_ratio": 0.5, "command": ["echo"]})
            assert exc_info.value.code == -32602
            assert "Schema validation failed" in exc_info.value.message
            assert "split_ratio" in exc_info.value.message or "command" in exc_info.value.message

            # 2. Client helper split_pane must conform to schema and succeed
            pane_id = await client.split_pane(direction="right", command=["echo", "hello"], split_ratio=0.5)
            assert pane_id == "w1:p2"

            # Verify the params sent to pane.split on the server had only schema fields
            split_reqs = [r for r in server.received_requests if r.get("method") == "pane.split"]
            assert len(split_reqs) == 2
            valid_params = split_reqs[1].get("params", {})
            assert "command" not in valid_params
            assert "split_ratio" not in valid_params
            assert valid_params.get("direction") == "right"
            assert valid_params.get("ratio") == 0.5
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_wait_for_output_and_wait_pane_ready(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            # 1. wait_pane_ready helper waits for shell prompt readiness before sending text
            ready = await client.wait_pane_ready("w1:p1", timeout=0.5)
            assert ready is True

            # Verify request conforms to herdr_schema.json, reached server, and used regex prompt pattern
            wait_reqs = [r for r in server.received_requests if r.get("method") == "pane.wait_for_output"]
            assert len(wait_reqs) == 1
            params = wait_reqs[0].get("params", {})
            assert params.get("pane_id") == "w1:p1"
            assert params.get("source") == "recent"
            match = params.get("match", {})
            assert match.get("type") == "regex", "match.type must be regex, not substring"
            assert match.get("value") != "", "match.value cannot be empty string"
            assert any(sym in match.get("value", "") for sym in ("$", "%", "#", ">", "❯"))

            # 2. Direct call to wait_for_output with custom regex or substring match
            res = await client.wait_for_output(
                "w1:p1",
                match={"type": "regex", "value": r"ready>"},
                source="recent",
                timeout=1.0,
            )
            assert res is True
            assert len([r for r in server.received_requests if r.get("method") == "pane.wait_for_output"]) == 2

            # 3. Ensure NO call ever sent empty substring
            for req in server.received_requests:
                if req.get("method") == "pane.wait_for_output":
                    m = req.get("params", {}).get("match", {})
                    assert not (m.get("type") == "substring" and m.get("value") == "")
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_wait_for_pane_exit_real_events(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            # 1. Evento real pane_exited
            wait_task1 = asyncio.create_task(client.wait_for_pane_exit("w9:pFM", timeout=2.0))
            await asyncio.sleep(0.05)
            await server.emit_event({
                "data": {"pane_id": "w9:pFM", "type": "pane_exited", "workspace_id": "w9"},
                "event": "pane_exited",
            })
            assert await wait_task1 is True

            # 2. Evento real pane_closed (também deve desbloquear wait_for_pane_exit)
            wait_task2 = asyncio.create_task(client.wait_for_pane_exit("w9:pFG", timeout=2.0))
            await asyncio.sleep(0.05)
            await server.emit_event({
                "data": {"pane_id": "w9:pFG", "type": "pane_closed", "workspace_id": "w9"},
                "event": "pane_closed",
            })
            assert await wait_task2 is True
    finally:
        server.close()
        await server.wait_closed()


@posix_only
@pytest.mark.asyncio
async def test_client_pane_exists(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            # 1. Pane existe: responde pane_info -> True
            server.custom_handlers["pane.get"] = lambda params: {
                "type": "pane_info",
                "pane": {"pane_id": params.get("pane_id"), "workspace_id": "w1"},
            }
            exists = await client.pane_exists("w1:p1")
            assert exists is True

            # 2. Pane inexistente: HerdrRPCError com pane_not_found -> False
            server.custom_handlers["pane.get"] = lambda params: {
                "__error__": {"code": -32000, "message": "pane_not_found: pane not found"}
            }
            exists_not_found = await client.pane_exists("w1:p999")
            assert exists_not_found is False

            # 3. Outro erro RPC (ex: erro interno) -> True (não derrubar worker saudável)
            server.custom_handlers["pane.get"] = lambda params: {
                "__error__": {"code": -32603, "message": "Internal error in daemon"}
            }
            exists_other_err = await client.pane_exists("w1:p1")
            assert exists_other_err is True

        # 4. Falha de conexão / socket fechado -> True
        bad_client = HerdrSocketClient(str(tmp_path / "nonexistent.sock"))
        assert await bad_client.pane_exists("w1:p1") is True
    finally:
        server.close()
        await server.wait_closed()




