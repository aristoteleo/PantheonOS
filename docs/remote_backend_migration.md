# Remote Backend Migration Guide

This document describes the new pluggable remote backend system implemented in Pantheon, which allows switching between different remote communication backends (Magique, NATS, etc.) without changing application code.

## Overview

The remote system has been refactored to use a plugin architecture that supports:
- **Zero Breaking Changes**: All existing code continues to work unchanged
- **Environment Configuration**: Switch backends via environment variables
- **Multiple Backends**: Currently supports Magique (default) and NATS
- **Extensible**: Easy to add new backends (Redis, RabbitMQ, etc.)

## Architecture

### Core Components

```
pantheon/remote/
├── backend/
│   ├── base.py           # Abstract interfaces
│   ├── registry.py       # Backend registration system
│   ├── magique.py        # Magique backend wrapper
│   └── nats.py           # NATS backend implementation
├── config.py             # Configuration system
├── factory.py            # Backend factory
├── agent.py              # Updated RemoteAgent/AgentService
└── compat.py             # Backward compatibility
```

### Key Abstractions

- **RemoteBackend**: Abstract base for all backends
- **RemoteService**: Represents a connection to a remote service
- **RemoteWorker**: Represents a worker that serves functions remotely
- **BackendRegistry**: Central registry for all backend implementations
- **RemoteConfig**: Configuration for backend selection and parameters

## Usage

### Current Code (Still Works)

```python
from pantheon.remote import RemoteAgent, AgentService

# Uses magique by default
service = AgentService(agent)
remote_agent = RemoteAgent("service_id")
```

### Environment-based Backend Selection

```bash
# Use NATS backend
export PANTHEON_REMOTE_BACKEND=nats
export NATS_SERVERS=nats://localhost:4222

# Use magique (default)
export PANTHEON_REMOTE_BACKEND=magique
```

### Programmatic Configuration

```python
from pantheon.remote import RemoteAgent, AgentService, RemoteConfig

# NATS configuration
nats_config = RemoteConfig(
    backend="nats",
    backend_config={"servers": ["nats://localhost:4222"]}
)

service = AgentService(agent, backend_config=nats_config)
remote_agent = RemoteAgent("service_id", backend_config=nats_config)

# Magique configuration
magique_config = RemoteConfig(
    backend="magique", 
    backend_config={"server_urls": ["ws://server1", "ws://server2"]}
)
```

## Backend Implementations

### Magique Backend

The Magique backend wraps the existing magique implementation:
- **Full backward compatibility** with existing code
- **All features preserved** (PyFunction, service registration, etc.)
- **Same performance characteristics**

### NATS Backend

The NATS backend provides:
- **High performance** messaging with request-reply pattern
- **Built-in clustering** and failover
- **Persistence** options via JetStream
- **Language agnostic** - services can be written in any language

## Installation

### NATS Support

```bash
# Install with NATS support
pip install "pantheon-agents[nats]"

# Start NATS server (for development)
docker run -p 4222:4222 -p 8222:8222 nats:latest
```

## Migration Guide

### Phase 1: No Changes Required
- All existing code continues to work unchanged
- Uses magique backend by default

### Phase 2: Optional NATS Usage
```bash
# Switch to NATS via environment
export PANTHEON_REMOTE_BACKEND=nats
export NATS_SERVERS=nats://localhost:4222
```

### Phase 3: Explicit Configuration
```python
# Use explicit configuration for fine control
config = RemoteConfig(backend="nats", backend_config={"servers": ["nats://server:4222"]})
service = AgentService(agent, backend_config=config)
```

## Testing

The implementation includes comprehensive tests:

```bash
# Run all remote backend tests
python -m pytest tests/remote/ -v

# Run original tests (still work)
python -m pytest tests/test_remote_agent.py -v

# Test specific backend
python -m pytest tests/remote/test_nats_backend.py -v
```

## Extending with New Backends

To add a new backend (e.g., Redis):

1. **Implement Backend Classes**:
```python
# pantheon/remote/backend/redis.py
class RedisBackend(RemoteBackend):
    async def connect(self, service_id: str, **kwargs) -> RedisService:
        # Implementation
    
    async def create_worker(self, service_name: str, **kwargs) -> RedisRemoteWorker:
        # Implementation

class RedisService(RemoteService):
    # Implementation

class RedisRemoteWorker(RemoteWorker):  
    # Implementation
```

2. **Register Backend**:
```python
# In factory.py
from .backend.redis import RedisBackend
BackendRegistry.register("redis", RedisBackend)
```

3. **Add Configuration Support**:
```python
# In config.py
elif backend == "redis":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    backend_config = {"redis_url": redis_url}
```

## Benefits

### Performance
- **NATS**: High-performance messaging with microsecond latencies
- **Magique**: Existing WebSocket-based performance characteristics
- **Pluggable**: Choose optimal backend for your use case

### Operational
- **Environment Control**: Switch backends without code changes
- **Gradual Migration**: Test new backends alongside existing ones
- **Service Discovery**: Each backend can implement its own discovery mechanism

### Development
- **Clean Architecture**: Clear separation between transport and application logic
- **Testability**: Mock backends for unit testing
- **Extensibility**: Easy to add support for new messaging systems

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure proper installation of optional dependencies
2. **Connection Issues**: Verify backend services are running
3. **Configuration**: Check environment variables and config objects

### Debug Information

```python
from pantheon.remote import BackendRegistry

print("Available backends:", BackendRegistry.list_backends())

config = RemoteConfig.from_env()
print(f"Using backend: {config.backend}")
print(f"Backend config: {config.backend_config}")
```

## Future Enhancements

Planned improvements:
- **Redis Backend**: For high-performance caching scenarios
- **RabbitMQ Backend**: For complex routing and message persistence
- **HTTP Backend**: For simple request-response patterns
- **Service Mesh Integration**: Istio, Linkerd support
- **Load Balancing**: Built-in client-side load balancing
- **Circuit Breakers**: Resilience patterns for remote calls