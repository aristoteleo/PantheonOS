"""Local model authority with an optional, explicitly bound cloud launcher."""
import re

from .client import ModelServices
from .errors import ControlError
from .local_directory import LocalModelDirectory


class LocalManagementDirectory(LocalModelDirectory):
    """Keep publications local; only Modal inventory/launches go to the Hub.

    The profile owns directory initialization. Opening a manager must not create
    an empty replacement catalog or borrow an ambient cloud login.
    """
    def __init__(self, root, *, owner, cloud=None):
        super().__init__(root, owner=owner)
        self.cloud = cloud
        self.modal_available = cloud is not None
        self.closed = False

    async def hub_request(self, method, path, data=None):
        if self.closed:
            raise RuntimeError('Model management directory is closed')
        if re.fullmatch(r'/api/model-services/modal-gpu(?:/[a-z0-9][a-z0-9-]{0,40})?', path):
            if self.cloud is None:
                raise ControlError(503, 'Modal requires an explicitly configured cloud account')
            return await self.cloud.hub_request(method, path, data)
        return await super().hub_request(method, path, data)

    async def route_operation(self, action='list', route=None, route_id='', revision=0, requires=None):
        # Reuse the existing route protocol, with this directory as its authority.
        return await ModelServices.route_operation(self, action, route, route_id, revision, requires)

    async def aclose(self):
        self.closed = True
        if self.cloud is not None:
            await self.cloud.aclose()
