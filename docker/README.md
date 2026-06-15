# Pantheon Agents - Docker Image

[![Docker Pulls](https://img.shields.io/docker/pulls/nanguage/pantheon-agents)](https://hub.docker.com/r/nanguage/pantheon-agents)
[![Docker Image Size](https://img.shields.io/docker/image-size/nanguage/pantheon-agents/latest)](https://hub.docker.com/r/nanguage/pantheon-agents)
[![GitHub](https://img.shields.io/badge/GitHub-PantheonOS-blue)](https://github.com/aristoteleo/PantheonOS)

Run Pantheon AI agents in a containerized environment with everything pre-configured.

---

## 🚀 Quick Start

```bash
docker run -it --rm \
  -e PANTHEON_MODE=standalone \
  -e PANTHEON_TEMPLATE_SYNC_SCOPE=project \
  -e OPENAI_API_KEY="sk-your-key" \
  -v $(pwd)/workspace:/workspace \
  -p 8080:8080 \
  nanguage/pantheon-agents:latest
```

**After startup, you'll see a connection URL like this:**

```
🔗 Full Connection URL:
   https://pantheon-ui.aristoteleo.com/#/?nats=ws://localhost:8080&service=pantheon-chatroom-abc123&auto=true

👉 Copy the URL above and paste it in your browser
```

**Note:** At least one API key is required (OpenAI, Anthropic, Gemini, etc.). Without API keys, the container will prompt for interactive configuration.

---

## 🔧 Configuration

### Basic Usage

```bash
docker run -it --rm \
  -e PANTHEON_MODE=standalone \
  -e PANTHEON_TEMPLATE_SYNC_SCOPE=project \
  -v $(pwd)/workspace:/workspace \
  -p 8080:8080 \
  nanguage/pantheon-agents:latest
```

### With API Keys

```bash
docker run -it --rm \
  -e PANTHEON_MODE=standalone \
  -e PANTHEON_TEMPLATE_SYNC_SCOPE=project \
  -e OPENAI_API_KEY="sk-your-key" \
  -e ANTHROPIC_API_KEY="sk-ant-your-key" \
  -v $(pwd)/workspace:/workspace \
  -p 8080:8080 \
  nanguage/pantheon-agents:latest
```

### With Custom Port

```bash
docker run -it --rm \
  -e PANTHEON_MODE=standalone \
  -e PANTHEON_TEMPLATE_SYNC_SCOPE=project \
  -e NATS_EXTERNAL_PORT=9000 \
  -v $(pwd)/workspace:/workspace \
  -p 9000:8080 \
  nanguage/pantheon-agents:latest
```

The connection URL will automatically use port 9000 instead of 8080.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PANTHEON_MODE` | Set to `standalone` for local use | `hub` |
| `NATS_EXTERNAL_PORT` | External port for NATS WebSocket | `8080` |
| `PANTHEON_TEMPLATE_SYNC_SCOPE` | Where factory agents/teams/prompts/skills are synced on startup: `project` writes to `/workspace/.pantheon/` (recommended for Docker), `global` writes to the container user's home, `none` skips factory template sync. The image defaults to `project`; pass this explicitly when using older images or custom entrypoints. | `project` |
| `PANTHEON_RESET_TEMPLATES` | Hub mode only. Set to `true` to clear stale project-level templates in `/workspace/.pantheon/` and force-sync factory defaults using the current `PANTHEON_TEMPLATE_SYNC_SCOPE`. Useful after image upgrades when the persistent volume contains outdated templates. Unset after one successful start. | - |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `GEMINI_API_KEY` | Google Gemini API key | - |
| `DEEPSEEK_API_KEY` | DeepSeek API key | - |

---

## 📁 Workspace Directory

The container creates a `workspace` directory for your files:

```
workspace/
├── .pantheon/          # Pantheon configuration
│   ├── .env           # API keys (auto-created)
│   ├── agents/        # Project-scoped factory/user agents
│   ├── teams/         # Project-scoped factory/user teams
│   ├── prompts/       # Project-scoped prompt snippets
│   ├── skills/        # Project-scoped skills and LiveView adapters
│   └── memory/        # Agent memory
├── your-code/         # Your code files
└── your-data/         # Your data files
```

Mount your local directory to persist data:
```bash
-v $(pwd)/workspace:/workspace
```

The Docker image defaults to `PANTHEON_TEMPLATE_SYNC_SCOPE=project` so factory
templates and hash stamps are stored under `/workspace/.pantheon/`. This keeps
templates, skills, and user edits persistent across `docker run --rm` restarts
as long as you mount the same workspace directory.

---

## 🌐 Remote Access

### From Same Machine
Use `localhost` in the connection URL (default).

### From Another Device

1. Get your machine's IP:
   ```bash
   hostname -I  # Linux/macOS
   ipconfig     # Windows
   ```

2. Replace `localhost` with your IP in the connection URL:
   ```
   https://pantheon-ui.aristoteleo.com/#/?nats=ws://192.168.1.100:8080&service=xxx&auto=true
   ```

3. Allow port 8080 through firewall:
   ```bash
   sudo ufw allow 8080  # Linux
   ```

---

## 🐛 Troubleshooting

### Connection URL Not Displayed

Wait 30-60 seconds for first startup, then check logs:
```bash
docker logs <container-id>
```

### Browser Cannot Connect

1. Verify port mapping: `docker ps | grep 8080`
2. Try `ws://127.0.0.1:8080` instead of `ws://localhost:8080`
3. Check firewall settings
4. Ensure no other service uses port 8080

### API Keys Not Working

1. Verify environment variables are set correctly
2. Restart container after changing `.env` file
3. Check API key validity and quota

---

## 📊 Image Information

- **Base Image**: `python:3.12-slim-bookworm`
- **Size**: ~2.5 GB (compressed: ~1.2 GB)
- **Platforms**: `linux/amd64`, `linux/arm64`
- **Included**: Python 3.12, NATS server, Playwright, Scientific computing stack

---

## 🏷️ Image Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `v1.0.0` | Specific version (recommended for production) |
| `develop` | Development branch |

**Recommended for production:**
```bash
docker pull nanguage/pantheon-agents:v1.0.0
```

---

## 🔗 Links

- **GitHub**: [https://github.com/aristoteleo/PantheonOS](https://github.com/aristoteleo/PantheonOS)
- **Documentation**: [https://github.com/aristoteleo/PantheonOS/tree/main/pantheon-agents](https://github.com/aristoteleo/PantheonOS/tree/main/pantheon-agents)
- **Issues**: [https://github.com/aristoteleo/PantheonOS/issues](https://github.com/aristoteleo/PantheonOS/issues)

---

## 📝 Advanced: Hub Mode

For production Kubernetes deployments with centralized NATS server:

```bash
docker run -d \
  -e ID_HASH=agent-001 \
  -e NATS_SERVERS=nats://hub-nats:4222 \
  -e PANTHEON_TEMPLATE_SYNC_SCOPE=project \
  -v $(pwd)/workspace:/workspace \
  nanguage/pantheon-agents:latest
```

See [full documentation](https://github.com/aristoteleo/PantheonOS/tree/main/pantheon-agents/docker) for details.
