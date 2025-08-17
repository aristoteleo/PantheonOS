from typing import Dict, Type, List
from .base import RemoteBackend


class BackendRegistry:
    """Registry for remote backends"""
    _backends: Dict[str, Type[RemoteBackend]] = {}
    
    @classmethod
    def register(cls, name: str, backend_class: Type[RemoteBackend]):
        """Register a backend implementation"""
        cls._backends[name] = backend_class
    
    @classmethod
    def get_backend(cls, name: str) -> Type[RemoteBackend]:
        """Get backend by name"""
        if name not in cls._backends:
            raise ValueError(f"Backend '{name}' not registered")
        return cls._backends[name]
    
    @classmethod
    def list_backends(cls) -> List[str]:
        """List available backends"""
        return list(cls._backends.keys())