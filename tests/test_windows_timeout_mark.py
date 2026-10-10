import sys

from tests import conftest


class FakeItem:
    def __init__(self):
        self.markers = []

    def add_marker(self, marker):
        self.markers.append(marker.mark)


def run_hook(items):
    conftest.pytest_collection_modifyitems(session=None, config=None, items=items)


def test_windows_adds_thread_timeout_mark_to_every_item(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("MEISTER_TEST_TIMEOUT", raising=False)
    items = [FakeItem(), FakeItem()]

    run_hook(items)

    for item in items:
        assert len(item.markers) == 1
        mark = item.markers[0]
        assert mark.name == "timeout"
        assert mark.args == (180.0,)
        assert mark.kwargs == {"method": "thread"}


def test_windows_uses_configured_timeout(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("MEISTER_TEST_TIMEOUT", "42")
    item = FakeItem()

    run_hook([item])

    assert [m.args for m in item.markers] == [(42.0,)]


def test_zero_timeout_adds_no_mark_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("MEISTER_TEST_TIMEOUT", "0")
    item = FakeItem()

    run_hook([item])

    assert item.markers == []


def test_unix_adds_no_mark(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("MEISTER_TEST_TIMEOUT", raising=False)
    item = FakeItem()

    run_hook([item])

    assert item.markers == []
