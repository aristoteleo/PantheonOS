"""Apps that may reach their bus over WebSocket (fleet-key) need nats-py's aiohttp transport."""
import pytest

from pantheon.apps.builtin.fleet.build_managed import build as build_fleet
from pantheon.models.management_package import build_package as build_model_management


@pytest.mark.parametrize('build', [build_fleet, build_model_management])
def test_bus_app_packages_install_the_websocket_transport(tmp_path, build):
    build(tmp_path / 'package', 'linux-amd64')
    requirements = (tmp_path / 'package' / 'requirements.txt').read_text()
    assert 'nats-py[nkeys,aiohttp]' in requirements
