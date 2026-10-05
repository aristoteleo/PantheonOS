"""Reap actual HTTP servers created by legacy Desktop data tests."""
import pytest
from pantheon.apps.builtin.desktop.data_server import LiveViewDataServer


@pytest.fixture(autouse=True)
async def owned_data_servers(monkeypatch):
    servers = []
    original = LiveViewDataServer.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        servers.append(self)
    monkeypatch.setattr(LiveViewDataServer, '__init__', initialize)
    yield
    for server in reversed(servers):
        await server.close()
