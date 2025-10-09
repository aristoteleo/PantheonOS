# Pantheon Docker Deployment Guide

This guide explains how to deploy Pantheon ChatRoom using Docker with a multi-container setup.

## Architecture

The Docker deployment consists of three services:

1. **NATS Server** - Message transfer backend with WebSocket support
2. **Qdrant** - Vector database for embeddings and RAG
3. **Pantheon ChatRoom** - Multi-agent orchestration service

## Prerequisites

- Docker Engine 20.10+
- Docker Compose V2
- At least 4GB RAM available
- Internet connection for pulling images

## Quick Start

### 1. Setup Environment

```bash
cd pantheon-agents

# Copy environment template
cp .env.example .env

# Edit .env and fill in your API keys
nano .env
```

**Required configuration in `.env`:**
```bash
ID_HASH=54c33f3a  # Your chatroom instance ID
OPENAI_API_KEY=sk-your-key-here
# Add other API keys as needed
```

### 2. Start Services

```bash
# Navigate to docker directory
cd docker

# Build and start all services
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build
```

### 3. Verify Services

```bash
# Check service status
docker-compose ps

# View logs
docker-compose logs -f pantheon

# Check individual services
docker-compose logs nats
docker-compose logs qdrant
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ID_HASH` | ChatRoom instance identifier | `default` |
| `ENV_FILE` | Path to environment file | `.env` |
| `WORKSPACE_DIR` | Host workspace directory | `./workspace` |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |

### Custom Workspace Directory

```bash
# Use custom workspace directory
export WORKSPACE_DIR=/path/to/your/workspace
docker-compose up
```

### Custom Environment File

```bash
# Use different env file
export ENV_FILE=./production.env
docker-compose up
```

## Service Endpoints

| Service | Port | Purpose |
|---------|------|---------|
| NATS Client | 4222 | Backend NATS protocol |
| NATS WebSocket | 8080 | Frontend WebSocket connection |
| NATS Monitoring | 8222 | Health checks and metrics |
| NATS Cluster | 6222 | Cluster routing (future) |
| Qdrant HTTP | 6333 | Vector database API |
| Qdrant gRPC | 6334 | High-performance API |

### Frontend Connection

Frontend applications should connect to NATS WebSocket:
```
ws://localhost:8080/ws
```

## Advanced Usage

### Custom ChatRoom Command

Override the default command:
```bash
docker-compose run pantheon python -m pantheon.chatroom --id_hash="custom123" --config=/workspace/my-config.yaml
```

### Scaling

Scale Pantheon instances (requires load balancer):
```bash
docker-compose up --scale pantheon=3
```

### Development Mode

Mount local code for development:
```yaml
# Add to docker-compose.yml under pantheon service
volumes:
  - ./pantheon:/app/pantheon  # Mount local code
  - ${WORKSPACE_DIR:-./workspace}:/workspace
```

## Maintenance

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f pantheon
docker-compose logs -f nats
docker-compose logs -f qdrant

# Last 100 lines
docker-compose logs --tail=100 pantheon
```

### Restart Services

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart pantheon
```

### Stop Services

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (CAUTION: deletes data)
docker-compose down -v
```

### Update Services

```bash
# Pull latest images
docker-compose pull

# Rebuild Pantheon
docker-compose build pantheon

# Restart with new images
docker-compose up -d
```

## Troubleshooting

### Service Won't Start

```bash
# Check service health
docker-compose ps

# Check logs for errors
docker-compose logs pantheon

# Verify environment variables
docker-compose config
```

### NATS Connection Issues

```bash
# Test NATS health
curl http://localhost:8222/healthz

# Check NATS logs
docker-compose logs nats

# Verify WebSocket endpoint
curl -i -N -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  http://localhost:8080/ws
```

### Qdrant Connection Issues

```bash
# Test Qdrant health
curl http://localhost:6333/healthz

# Check Qdrant logs
docker-compose logs qdrant

# List collections
curl http://localhost:6333/collections
```

### Permission Issues

```bash
# Fix workspace permissions
sudo chown -R $(id -u):$(id -g) ./workspace
```

### Clean Restart

```bash
# Stop everything
docker-compose down -v

# Remove old images
docker-compose rm -f

# Rebuild from scratch
docker-compose up --build --force-recreate
```

## Data Persistence

### Qdrant Data

Qdrant data is stored in a Docker volume:
```bash
# Backup Qdrant data
docker run --rm -v pantheon-agents_qdrant_data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/qdrant-backup.tar.gz -C /data .

# Restore Qdrant data
docker run --rm -v pantheon-agents_qdrant_data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar xzf /backup/qdrant-backup.tar.gz -C /data
```

### Workspace Data

Workspace is mounted from host:
```bash
# Default location
./workspace/

# Or custom location
export WORKSPACE_DIR=/path/to/workspace
```

## Production Deployment

### Security Considerations

1. **Enable NATS Authentication**
   - Edit `nats-ws.conf` to add authentication
   - Update environment variables with credentials

2. **Use TLS/SSL**
   - Configure NATS TLS in `nats-ws.conf`
   - Use reverse proxy (nginx/traefik) for HTTPS

3. **Restrict Network Access**
   - Use Docker networks for service isolation
   - Expose only necessary ports
   - Use firewall rules

4. **Secrets Management**
   - Use Docker secrets or external secret manager
   - Never commit `.env` files to git

### Example Production Setup

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  nats:
    restart: always
    # ... other config

  qdrant:
    restart: always
    # ... other config

  pantheon:
    restart: always
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G
```

## Support

For issues and questions:
- GitHub Issues: https://github.com/your-org/pantheon/issues
- Documentation: https://docs.pantheon.ai
- Community: https://discord.gg/pantheon
