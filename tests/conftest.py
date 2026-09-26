import pathlib
import tempfile
import pytest


@pytest.fixture
def tmp_path():
    """Override tmp_path fixture to use /tmp to avoid macOS AF_UNIX 104-char path limit."""
    with tempfile.TemporaryDirectory(dir="/tmp") as d:
        yield pathlib.Path(d)
