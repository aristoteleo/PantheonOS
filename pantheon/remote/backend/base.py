import typing as T
from abc import ABC, abstractmethod
from typing import Any, Dict, Callable, Optional
from dataclasses import dataclass
from funcdesc import Description


@dataclass
class ServiceInfo:
    service_id: str
    service_name: str
    description: str
    functions_description: T.Dict[str, Description]


class RemoteBackend(ABC):
    """Abstract interface for remote communication backends"""

    @abstractmethod
    async def connect(self, service_id: str, **kwargs) -> "RemoteService":
        """Connect to a remote service"""
        pass

    @abstractmethod
    def create_worker(self, service_name: str, **kwargs) -> "RemoteWorker":
        """Create a worker for serving functions (synchronous, connection delayed until run)"""
        pass

    @property
    @abstractmethod
    def servers(self) -> list[str]:
        pass


class RemoteService(ABC):
    """Abstract remote service interface"""

    @abstractmethod
    async def invoke(self, method: str, parameters: Dict[str, Any] = None) -> Any:
        """Invoke a remote method"""
        pass

    @abstractmethod
    async def close(self):
        """Close connection"""
        pass

    @property
    @abstractmethod
    def service_info(self) -> ServiceInfo:
        """Get service information"""
        pass

    @abstractmethod
    async def fetch_service_info(self) -> ServiceInfo:
        """"""
        pass


class RemoteWorker(ABC):
    """Abstract remote worker interface"""

    @abstractmethod
    def register(self, func: Callable, **kwargs):
        """Register a function for remote access"""
        pass

    @abstractmethod
    async def run(self):
        """Start the worker"""
        pass

    @abstractmethod
    async def stop(self):
        """Stop the worker"""
        pass

    @property
    @abstractmethod
    def service_id(self) -> str:
        """Get the service ID"""
        pass

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Get the service name"""
        pass

    @property
    def servers(self):
        """Get the servers (optional, for backward compatibility)"""
        return []

    @property
    def functions(self) -> Dict[str, tuple]:
        return {}
