"""app_start spec building (control plane -> fleet runner)."""

import sys

import pytest

from pantheon.apps.spec import apphost_spec, instance_service_seed
from pantheon.utils.misc import generate_service_id


def test_spec_shape_matches_the_wire_contract(tmp_path):
    spec = apphost_spec("shell", user_seed="u1", workdir=str(tmp_path))
    assert spec["app_id"] == "shell"
    assert spec["scope"] == "app"
    assert spec["command"][0] == sys.executable
    assert spec["command"][1:5] == ["-m", "pantheon.apphost", "--app-id", "shell"]
    assert "--id-hash" in spec["command"]
    # the service id the brain will dial == what the apphost registers
    seed = spec["command"][spec["command"].index("--id-hash") + 1]
    assert spec["service_id"] == generate_service_id(seed)


def test_instance_seeds_are_stable_and_distinct():
    a = instance_service_seed("u1", "shell")
    assert a == instance_service_seed("u1", "shell")
    assert a != instance_service_seed("u1", "pty")
    assert a != instance_service_seed("u2", "shell")
    assert a != instance_service_seed("u1", "shell", scope="window-3")


def test_unknown_app_id_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown app id"):
        apphost_spec("no-such-app", user_seed="u1", workdir=str(tmp_path))
