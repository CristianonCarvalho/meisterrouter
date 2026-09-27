import os
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from meister.cli import main
from meister.worker import (
    resolve_worker_model,
    parse_and_apply_file_edits,
    execute_worker_task,
    NativeWorker,
)


def test_resolve_worker_model():
    assert resolve_worker_model("luna") == "openai/gpt-6-luna"
    assert resolve_worker_model("gemini_flash") == "google/gemini-2.5-flash"
    assert resolve_worker_model("haiku") == "anthropic/claude-3-5-haiku-20241022"
    assert resolve_worker_model("custom/model:free") == "custom/model:free"


def test_parse_and_apply_file_edits(tmp_path):
    response_text = """
I have analyzed the code and applied the required fixes.

```file:src/app.py
def hello():
    return "Hello MeisterRouter"
```

Also updated the styles:
```file:styles/tooltip.css
.tooltip {
    overflow: visible;
}
```

Task completed.
"""
    modified = parse_and_apply_file_edits(response_text, base_dir=str(tmp_path))
    assert len(modified) == 2
    assert "src/app.py" in modified
    assert "styles/tooltip.css" in modified

    app_py = tmp_path / "src" / "app.py"
    assert app_py.exists()
    assert "Hello MeisterRouter" in app_py.read_text()

    css_file = tmp_path / "styles" / "tooltip.css"
    assert css_file.exists()
    assert "overflow: visible;" in css_file.read_text()


def test_native_worker_execute_task_success(tmp_path):
    target_file = tmp_path / "test.txt"
    target_file.write_text("old content")

    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "content": "```file:test.txt\nnew content\n```\nDone."
                }
            }
        ],
        "usage": {
            "total_tokens": 42
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_llm_response
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_resp) as mock_post, \
         patch("meister.worker.get_api_key", return_value="sk-or-test-key"):
        worker = NativeWorker(model="luna", cwd=str(tmp_path))
        result = worker.run_task("Update test.txt to say new content", target_files=["test.txt"])

        assert result["status"] == "done"
        assert "test.txt" in result["modified_files"]
        assert target_file.read_text() == "new content\n"

        # Check call arguments
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["model"] == "openai/gpt-6-luna"
        assert "Authorization" in kwargs["headers"]
        assert kwargs["headers"]["Authorization"] == "Bearer sk-or-test-key"


def test_native_worker_handles_quota_429(tmp_path):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = '{"error": {"code": 429, "message": "Rate limit / quota exceeded"}}'

    import requests
    http_error = requests.exceptions.HTTPError(response=mock_resp)
    mock_resp.raise_for_status.side_effect = http_error

    with patch("requests.post", return_value=mock_resp), \
         patch("meister.worker.get_api_key", return_value="sk-or-test-key"):
        worker = NativeWorker(model="luna", cwd=str(tmp_path))
        with pytest.raises(Exception) as exc_info:
            worker.run_task("Do something")
        assert "429" in str(exc_info.value) or "Rate limit" in str(exc_info.value)


def test_cli_worker_with_task_flag(tmp_path):
    runner = CliRunner()
    mock_result = {
        "status": "done",
        "modified_files": ["app.py"],
        "output": "Successfully implemented",
    }

    with patch("meister.worker.execute_worker_task", return_value=mock_result) as mock_exec:
        result = runner.invoke(
            main,
            ["worker", "--no-pane", "--model", "luna", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Status: done" in result.output
        mock_exec.assert_called_once()


def test_cli_worker_with_pane_dispatch(tmp_path):
    runner = CliRunner()
    mock_result = {
        "status": "done",
        "modified_files": ["app.py"],
    }
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_pane", return_value=mock_result) as mock_pane:
        result = runner.invoke(
            main,
            ["worker", "--model", "luna", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Worker task finished in Herdr pane" in result.output
        mock_pane.assert_called_once()
