import asyncio

from pantheon.chatroom.services import fleet_api
from pantheonos.appkit.builtin.fleet import update


def _url(monkeypatch, tag, explicit=""):
    async def published(max_age=600):
        return tag

    monkeypatch.setattr(update, "published_release", published)
    if explicit:
        monkeypatch.setenv("FLEET_INSTALL_URL", explicit)
    else:
        monkeypatch.delenv("FLEET_INSTALL_URL", raising=False)
    return asyncio.run(fleet_api.fleet_install_url())


def test_local_add_node_installer_follows_the_controller(monkeypatch):
    assert _url(monkeypatch, "fleet-v0.5.1-staging.4").endswith("/fleet-v0.5.1-staging.4/install.sh")
    for tag in ["", "latest", "fleet-v0.5.1/../../x"]:
        assert _url(monkeypatch, tag) == fleet_api.DEFAULT_INSTALL_URL
    assert _url(monkeypatch, "fleet-v0.5.1-staging.4", "https://example.com/install.sh") == "https://example.com/install.sh"
