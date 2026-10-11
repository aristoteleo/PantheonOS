"""Model Services management for the classic ChatRoom backend.

The same operations the model-management App serves to a deployed Agent
(pantheon.models.services_api), over this backend's own manager. Starting
Model Services Apps is the Fleet controller's (docs/fleet-orchestration.md).
"""
from pantheon.models.services_api import ModelServicesAPI as _ModelServicesAPI


class ModelServicesAPI(_ModelServicesAPI):
    def _model_services_manager(self):
        from pantheon.models.manager import ModelServiceManager
        if not hasattr(self, '_model_services'):
            self._model_services = ModelServiceManager()
        return self._model_services
