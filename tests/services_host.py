"""A minimal host for the classic ChatRoom service mixins, without an Agent."""
from pathlib import Path
import threading
import time

from pantheon.chatroom.services.apps_api import AppServicesAPI
from pantheon.chatroom.services.fleet_api import FleetAPI
from pantheon.chatroom.services.health import PlatformHealth
from pantheon.chatroom.services.model_directory import ModelDirectoryAPI
from pantheon.chatroom.services.models_api import ModelServicesAPI
from pantheon.chatroom.services.oauth_api import OAuthAPI
from pantheon.chatroom.services.projects_api import ProjectsAPI
from pantheon.chatroom.services.store_api import StoreAPI
from pantheonos.toolset import ToolSet


class ServicesHost(OAuthAPI, ModelDirectoryAPI, StoreAPI, PlatformHealth, AppServicesAPI, FleetAPI,
                   ModelServicesAPI, ProjectsAPI, ToolSet):
    def __init__(self, name='services-host', workspace_path=None, **kwargs):
        self.workspace_path = str(Path(workspace_path or Path.cwd()).resolve())
        self._project_manager = None
        self._project_manager_lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        kwargs['allow_in_place_restart'] = False
        super().__init__(name=name, **kwargs)
