import json
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from tests.mocks.mock_herdr_server import run_mock_herdr_server
from meister.herdr.client import HerdrSocketClient
from meister.herdr.workers import WorkerSpawner
from meister.config import MeisterConfig, WorkersConfig, WorkerTier
from meister.worker import run_worker_in_herdr_tab_async, write_atomic_json
from meister.cli import main


@pytest.mark.asyncio
async def test_herdr_client_tab_create_and_close(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            # Achado #13: tab.create com cwd=worktree, focus=False, label=worker:t1
            tab_id, pane_id = await client.create_tab(
                cwd=str(tmp_path),
                label="worker:t1",
                focus=False,
            )
            assert tab_id.startswith("t")
            assert pane_id.startswith("w1:p")

            # Verifica se o pedido foi validado contra o schema oficial
            create_req = next(r for r in server.received_requests if r.get("method") == "tab.create")
            params = create_req.get("params", {})
            assert params.get("focus") is False
            assert params.get("label") == "worker:t1"
            assert params.get("cwd") == str(tmp_path)

            # Fecha a tab
            ok = await client.close_tab(tab_id)
            assert ok is True
            close_req = next(r for r in server.received_requests if r.get("method") == "tab.close")
            assert close_req["params"]["tab_id"] == tab_id
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_herdr_client_wait_for_pane_exit(tmp_path):
    # Achado #10: Evento pane.exited
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        async with HerdrSocketClient(sock_path) as client:
            wait_task = asyncio.create_task(client.wait_for_pane_exit("w1:p99", timeout=2.0))

            await asyncio.sleep(0.05)
            # Servidor emite pane_exited
            await server.emit_event({"type": "pane_exited", "pane_id": "w1:p99", "workspace_id": "w1"})

            res = await wait_task
            assert res is True
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_worker_spawner_spawn_tab(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)

    cfg = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="native", model="gpt-6-luna"),
            ]
        )
    )

    try:
        async with HerdrSocketClient(sock_path) as client:
            spawner = WorkerSpawner(config=cfg, herdr_client=client)
            tab_id, pane_id, tier = await spawner.spawn_worker_tab(
                tier_name="luna",
                task_context={"id": "step_1", "description": "Update database models"},
                cwd=str(tmp_path),
                focus=False,
            )

            assert tab_id.startswith("t")
            assert pane_id.startswith("w1:p")
            assert tier.name == "luna"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_run_worker_in_herdr_tab_async_completes_and_closes_tab(tmp_path):
    mock_client = MagicMock()
    mock_client.create_tab = AsyncMock(return_value=("t1", "w1:p2"))
    mock_client.send_text = AsyncMock()
    mock_client.close_tab = AsyncMock()
    mock_client.subscribe_events = AsyncMock()

    # Simula escrita do result.json durante a execução
    async def fake_create_tab(**kwargs):
        runs_dir = tmp_path / ".meister" / "runs"
        # Grava o resultado após criação da tab
        asyncio.get_event_loop().call_later(
            0.1,
            lambda: [
                write_atomic_json(
                    payload["result_file"],
                    {"status": "done", "modified_files": ["model.py"]},
                )
                for f in runs_dir.glob("*_task.json")
                if (payload := json.loads(f.read_text(encoding="utf-8")))
            ]
        )
        return ("t1", "w1:p2")

    mock_client.create_tab.side_effect = fake_create_tab

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        res = await run_worker_in_herdr_tab_async(
            model="luna",
            task="Update database models",
            cwd=str(tmp_path),
            timeout=5.0,
        )

    assert res["status"] == "done"
    assert "model.py" in res["modified_files"]
    mock_client.create_tab.assert_called_once()
    mock_client.close_tab.assert_called_once_with("t1")


@pytest.mark.asyncio
async def test_run_worker_in_herdr_tab_async_timeout_closes_tab(tmp_path):
    mock_client = MagicMock()
    mock_client.create_tab = AsyncMock(return_value=("t1", "w1:p2"))
    mock_client.send_text = AsyncMock()
    mock_client.send_interrupt = AsyncMock()
    mock_client.close_tab = AsyncMock()
    mock_client.subscribe_events = AsyncMock()

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client), \
         pytest.raises(TimeoutError) as exc_info:
        await run_worker_in_herdr_tab_async(
            model="luna",
            task="Hanging task",
            cwd=str(tmp_path),
            timeout=0.05,
        )

    assert "timeout" in str(exc_info.value).lower()
    mock_client.send_interrupt.assert_called_once_with("w1:p2")
    mock_client.close_tab.assert_called_once_with("t1")


def test_cli_worker_with_tab_flag(tmp_path):
    runner = CliRunner()
    mock_result = {
        "status": "done",
        "modified_files": ["schema.py"],
    }
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_tab", return_value=mock_result) as mock_tab:
        result = runner.invoke(
            main,
            ["worker", "--tab", "--model", "luna", "--task", "Fix schema validation", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Worker task finished in Herdr tab" in result.output
        assert "schema.py" in result.output
        mock_tab.assert_called_once()
