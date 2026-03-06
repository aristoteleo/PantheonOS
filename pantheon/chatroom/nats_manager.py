"""
NATS Server Manager for Local Chatroom Auto-Start

Manages lifecycle of local NATS server subprocess with:
- Binary detection (.venv/bin or PATH)
- Port availability checking
- Config generation from template with runtime substitution
- Subprocess startup/cleanup with health monitoring
- Error handling and logging
- Instance tracking via pantheon_dir for multi-instance isolation
"""

import asyncio
import json
import os
import socket
import shutil
import aiohttp
from pathlib import Path
from typing import Optional, Tuple

from pantheon.utils.log import logger


class NATSManager:
    """
    Manages local NATS server subprocess lifecycle.

    Responsibilities:
    - Detect nats-server binary (.venv/bin or PATH)
    - Check port availability (4222, 8080, 8222)
    - Generate config file from template with runtime substitution
    - Start/stop subprocess with proper cleanup
    - Health monitoring via HTTP endpoint

    Example:
        manager = NATSManager(
            config_template_path=Path("docker/nats-ws.conf"),
            work_dir=Path("./.pantheon/chatroom"),
        )
        server_info = await manager.start()
        # use server_info["tcp_url"], server_info["ws_url"], etc.
        await manager.stop()
    """

    def __init__(
        self,
        config_template_path: Path,
        tcp_port: int = 4222,
        ws_port: int = 8080,
        http_port: int = 8222,
        work_dir: Optional[Path] = None,
        pantheon_dir: Optional[Path] = None,
    ):
        """
        Initialize NATS Manager.

        Args:
            config_template_path: Path to nats-ws.conf template
            tcp_port: TCP port for NATS server (default: 4222)
            ws_port: WebSocket port (default: 8080)
            http_port: HTTP monitoring port (default: 8222)
            work_dir: Directory for logs and JetStream storage (default: current dir)
            pantheon_dir: Pantheon config directory for instance tracking (default: work_dir/.pantheon)
        """
        self.config_template_path = Path(config_template_path)
        self.tcp_port = tcp_port
        self.ws_port = ws_port
        self.http_port = http_port
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.pantheon_dir = Path(pantheon_dir) if pantheon_dir else (self.work_dir / ".pantheon")

        self._process: Optional[asyncio.subprocess.Process] = None
        self._config_file: Optional[Path] = None
        self._instance_file = self.pantheon_dir / ".nats-instance.json"

    def check_binary_available(self) -> Tuple[bool, str]:
        """
        Check if nats-server binary is available.

        Returns:
            (available, path_or_error_message): Tuple of availability and path/error
        """
        # 1. Check .venv/bin/nats-server (project's venv)
        venv_binary = (
            Path(__file__).parent.parent.parent / ".venv" / "bin" / "nats-server"
        )
        if venv_binary.exists():
            logger.debug(f"Found nats-server at: {venv_binary}")
            return (True, str(venv_binary))

        # 2. Check PATH
        binary = shutil.which("nats-server")
        if binary:
            logger.debug(f"Found nats-server in PATH: {binary}")
            return (True, binary)

        # 3. Return error message with install instructions
        error_msg = (
            "nats-server binary not found.\n\n"
            "Installation options:\n"
            "1. Via Go: go install github.com/nats-io/nats-server/v2@latest\n"
            "2. Via Homebrew (macOS): brew install nats-server\n"
            "3. Via wget (Linux): wget https://github.com/nats-io/nats-server/releases/download/v2.10.9/nats-server-v2.10.9-linux-amd64.tar.gz\n"
            "4. Via Docker: docker run -p 4222:4222 -p 8080:8080 nats:alpine"
        )
        logger.error(error_msg)
        return (False, error_msg)

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        """Check if a port is in use by attempting to bind to it."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return False  # Port is available (bind succeeded)
            except OSError:
                return True  # Port is in use (bind failed)

    def check_ports_available(self) -> Tuple[bool, list]:
        """
        Check if required ports are available.

        Returns:
            (all_free, occupied_ports): Tuple of availability status and list of occupied ports
        """
        occupied = []

        for port_name, port in [
            ("TCP", self.tcp_port),
            ("WebSocket", self.ws_port),
            ("HTTP", self.http_port),
        ]:
            if self._is_port_in_use(port):
                logger.debug(f"Port {port_name}:{port} is occupied")
                occupied.append(port)
            else:
                logger.debug(f"Port {port_name}:{port} is available")

        return (len(occupied) == 0, occupied)

    def find_available_port(self, start_port: int = 4222, max_attempts: int = 100) -> int:
        """
        Find an available port starting from start_port.

        Args:
            start_port: Starting port number
            max_attempts: Maximum attempts to find available port

        Returns:
            int: Available port number

        Raises:
            RuntimeError: If no available port found after max_attempts
        """
        for port in range(start_port, start_port + max_attempts):
            if not self._is_port_in_use(port):
                logger.debug(f"Found available port: {port}")
                return port

        raise RuntimeError(
            f"Could not find available port in range {start_port}-{start_port + max_attempts}"
        )

    def auto_configure_ports(self):
        """
        Automatically configure ports by finding available alternatives if default ports are occupied.

        This method:
        1. Checks if default ports are available
        2. If any are occupied, automatically finds alternatives
        3. Updates self.tcp_port, self.ws_port, self.http_port with available ports
        """
        all_free, occupied = self.check_ports_available()

        if all_free:
            logger.info(
                f"[NATS] Default ports available: TCP:{self.tcp_port} WS:{self.ws_port} HTTP:{self.http_port}"
            )
            return

        logger.info(f"[NATS] Some ports occupied: {occupied}. Auto-finding alternatives...")

        # Find available alternatives
        if self.tcp_port in occupied:
            old_port = self.tcp_port
            self.tcp_port = self.find_available_port(start_port=4222)
            logger.info(f"[NATS] TCP port: {old_port} → {self.tcp_port} (auto-allocated)")

        if self.ws_port in occupied:
            old_port = self.ws_port
            self.ws_port = self.find_available_port(start_port=8080)
            logger.info(f"[NATS] WebSocket port: {old_port} → {self.ws_port} (auto-allocated)")

        if self.http_port in occupied:
            old_port = self.http_port
            self.http_port = self.find_available_port(start_port=8222)
            logger.info(f"[NATS] HTTP port: {old_port} → {self.http_port} (auto-allocated)")

        logger.info(
            f"[NATS] Final ports: TCP:{self.tcp_port} WS:{self.ws_port} HTTP:{self.http_port}"
        )

    def _generate_config(self) -> Path:
        """
        Generate NATS config file from template with runtime substitutions.

        Returns:
            Path to generated config file

        Raises:
            RuntimeError: If template not found or config generation fails
        """
        import re

        if not self.config_template_path.exists():
            raise RuntimeError(
                f"NATS config template not found: {self.config_template_path}"
            )

        logger.debug(f"Reading template: {self.config_template_path}")
        template = self.config_template_path.read_text(encoding="utf-8")

        # Substitute ports using regex to match only actual config lines, not comments
        # Match: line start + optional whitespace + "port: 4222" + optional comment/whitespace
        config = re.sub(
            r'^(\s*)port:\s*4222(\s*(?:#.*)?)?$',
            rf'\1port: {self.tcp_port}\2',
            template,
            flags=re.MULTILINE
        )

        # WebSocket port - match only the websocket section
        config = re.sub(
            r'^(\s*)port:\s*8080(\s*(?:#.*)?)?$',
            rf'\1port: {self.ws_port}\2',
            config,
            flags=re.MULTILINE,
            count=1  # Only replace first occurrence in websocket block
        )

        # HTTP monitoring port
        config = re.sub(
            r'^(\s*)http_port:\s*8222(\s*(?:#.*)?)?$',
            rf'\1http_port: {self.http_port}\2',
            config,
            flags=re.MULTILINE
        )

        # Update JetStream storage path to use work_dir
        jetstream_dir = self.work_dir / ".nats-jetstream"
        jetstream_dir.mkdir(parents=True, exist_ok=True)
        config = re.sub(
            r'store_dir:\s*["\']?/tmp/nats/jetstream["\']?',
            f'store_dir: "{jetstream_dir.as_posix()}"',
            config
        )

        # Update server name (this one can stay as simple replace since it's unique)
        config = config.replace(
            "server_name: pantheon-nats", "server_name: pantheon-nats-local"
        )

        # Write to temporary config file
        config_file = self.work_dir / ".nats-config.conf"
        config_file.write_text(config, encoding="utf-8")

        logger.debug(f"Generated NATS config: {config_file}")
        return config_file

    async def detect_existing(self) -> Optional[dict]:
        """
        Detect if a NATS server is already running for this pantheon_dir.

        Reads instance tracking file and validates:
        1. PID is still alive
        2. Ports match and are accessible
        3. NATS healthz endpoint responds

        Returns:
            server_info dict if valid instance found, None otherwise
        """
        if not self._instance_file.exists():
            logger.debug(f"[NATS] No instance file found: {self._instance_file}")
            return None

        try:
            # Read instance file
            with open(self._instance_file, 'r') as f:
                instance_data = json.load(f)

            pid = instance_data.get("pid")
            tcp_port = instance_data.get("tcp_port")
            ws_port = instance_data.get("ws_port")
            http_port = instance_data.get("http_port")

            if not all([pid, tcp_port, ws_port, http_port]):
                logger.debug("[NATS] Instance file missing required fields")
                return None

            # Check if process is alive
            try:
                os.kill(pid, 0)  # Signal 0 checks if process exists
            except (OSError, ProcessLookupError):
                logger.debug(f"[NATS] Process PID={pid} is not alive")
                # Clean up stale instance file
                self._instance_file.unlink()
                return None

            # Check HTTP healthz endpoint
            http_url = f"http://localhost:{http_port}/healthz"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        http_url, timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(f"[NATS] Healthz check failed: {resp.status}")
                            return None
            except Exception as e:
                logger.debug(f"[NATS] Healthz check failed: {e}")
                return None

            # Check TCP connectivity
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", tcp_port),
                    timeout=2.0,
                )
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug(f"[NATS] TCP connection failed: {e}")
                return None

            # Check WS port connectivity
            try:
                r2, w2 = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", ws_port),
                    timeout=2.0,
                )
                w2.close()
                await w2.wait_closed()
            except Exception as e:
                logger.debug(f"[NATS] WebSocket connection failed: {e}")
                return None

            # All checks passed - reuse existing instance
            logger.info(f"[NATS] Detected existing NATS server (PID={pid})")
            logger.info(f"[NATS]   TCP:{tcp_port} WS:{ws_port} HTTP:{http_port}")

            # Update self ports to match existing instance
            self.tcp_port = tcp_port
            self.ws_port = ws_port
            self.http_port = http_port

            return {
                "tcp_url": f"nats://localhost:{tcp_port}",
                "ws_url": f"ws://127.0.0.1:{ws_port}",
                "http_url": f"http://localhost:{http_port}",
                "config_file": instance_data.get("config_file"),
                "log_file": instance_data.get("log_file"),
                "pid": pid,
                "reused": True,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"[NATS] Invalid instance file: {e}")
            # Clean up corrupted file
            self._instance_file.unlink()
            return None
        except Exception as e:
            logger.debug(f"[NATS] Error detecting existing instance: {e}")
            return None

    async def start(self) -> dict:
        """
        Start local NATS server subprocess.

        Returns:
            dict: Server connection info including:
                - tcp_url: nats://localhost:4222
                - ws_url: ws://127.0.0.1:8080
                - http_url: http://localhost:8222
                - config_file: Path to generated config
                - log_file: Path to server logs
                - pid: Process ID

        Raises:
            RuntimeError: If binary not found, ports occupied, or config invalid
            ConnectionError: If server fails to start or doesn't become ready
        """
        logger.info("[NATS] Starting local NATS server...")

        # 1. Binary check
        logger.debug("[NATS] Checking nats-server binary...")
        available, binary_path = self.check_binary_available()
        if not available:
            raise RuntimeError(f"Cannot start NATS: {binary_path}")

        logger.info(f"[NATS] Found binary: {binary_path}")

        # 2. Port check with auto-allocation
        logger.debug("[NATS] Checking and auto-allocating ports if needed...")
        self.auto_configure_ports()

        # 3. Generate config
        logger.debug("[NATS] Generating config from template...")
        self._config_file = self._generate_config()
        logger.info(f"[NATS] Config generated: {self._config_file}")

        # 4. Create log file
        log_file = self.work_dir / ".nats-server.log"
        logger.info(f"[NATS] Log file: {log_file}")

        # 5. Start subprocess
        logger.info(f"[NATS] Starting subprocess: {binary_path}")
        with open(log_file, "w", encoding="utf-8") as f:
            self._process = await asyncio.create_subprocess_exec(
                binary_path,
                "-c",
                str(self._config_file),
                stdout=f,
                stderr=asyncio.subprocess.STDOUT,
            )

        logger.info(f"[NATS] Server started (PID={self._process.pid})")

        # 6. Wait for health check
        logger.info("[NATS] Waiting for server to be ready...")
        if not await self.wait_for_ready(timeout=30):
            logger.error("[NATS] Server failed to start within timeout, terminating...")
            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    logger.warning("[NATS] Process did not terminate, force killing...")
                    self._process.kill()
            else:
                logger.error(f"[NATS] Process already exited with code {self._process.returncode}")

            # Read last few lines of log for better error context
            log_tail = ""
            try:
                lines = log_file.read_text(encoding="utf-8").strip().splitlines()
                log_tail = "\n".join(lines[-5:])
            except Exception:
                pass

            raise ConnectionError(
                f"NATS server failed to start.\n"
                f"Log file: {log_file}\n"
                f"Last log lines:\n{log_tail}"
            )

        # 7. Return connection info
        server_info = {
            "tcp_url": f"nats://localhost:{self.tcp_port}",
            "ws_url": f"ws://127.0.0.1:{self.ws_port}",
            "http_url": f"http://localhost:{self.http_port}",
            "config_file": str(self._config_file),
            "log_file": str(log_file),
            "pid": self._process.pid,
        }

        logger.info(f"[NATS] ✓ Server ready!")
        logger.info(f"[NATS]   TCP: {server_info['tcp_url']}")
        logger.info(f"[NATS]   WebSocket: {server_info['ws_url']}")
        logger.info(f"[NATS]   Monitoring: {server_info['http_url']}")

        # Write instance tracking file
        self._write_instance_file(server_info)

        return server_info

    async def wait_for_ready(self, timeout: int = 30) -> bool:
        """
        Poll HTTP monitoring endpoint and TCP port until server is ready.

        This method ensures NATS is fully operational by checking:
        1. HTTP /healthz endpoint (server process started)
        2. TCP port connectivity (NATS accepting connections)

        Args:
            timeout: Maximum seconds to wait for server readiness

        Returns:
            bool: True if server became ready, False if timeout
        """
        http_url = f"http://localhost:{self.http_port}/healthz"
        start_time = asyncio.get_event_loop().time()
        http_ready = False
        tcp_ready = False

        while asyncio.get_event_loop().time() - start_time < timeout:
            # Check if process died
            if self._process.returncode is not None:
                logger.error(
                    f"[NATS] Process exited with code {self._process.returncode}"
                )
                return False

            # Check HTTP healthz endpoint
            if not http_ready:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            http_url, timeout=aiohttp.ClientTimeout(total=1)
                        ) as resp:
                            if resp.status == 200:
                                logger.debug("[NATS] HTTP healthz check passed")
                                http_ready = True
                except Exception as e:
                    logger.debug(f"[NATS] HTTP check failed: {type(e).__name__}")

            # Check TCP port connectivity (crucial for actual client connections)
            if http_ready and not tcp_ready:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", self.tcp_port),
                        timeout=1.0
                    )
                    writer.close()
                    await writer.wait_closed()
                    logger.debug(f"[NATS] TCP port {self.tcp_port} is ready")
                    tcp_ready = True
                except Exception as e:
                    logger.debug(f"[NATS] TCP check failed: {type(e).__name__}")

            # Both checks passed, server is ready
            if http_ready and tcp_ready:
                logger.debug("[NATS] Server is fully ready (HTTP + TCP)")
                return True

            await asyncio.sleep(0.5)

        # Timeout reached
        if http_ready and not tcp_ready:
            logger.error(f"[NATS] HTTP endpoint ready but TCP port {self.tcp_port} not responding after {timeout}s")
        else:
            logger.error(f"[NATS] Health check timeout after {timeout}s (HTTP: {http_ready}, TCP: {tcp_ready})")
        return False

    async def stop(self):
        """
        Gracefully stop NATS server with fallback to force kill.

        Performs:
        - Send SIGTERM for graceful shutdown
        - Wait up to 5 seconds
        - Force kill with SIGKILL if needed
        - Cleanup temporary config file
        - Remove instance tracking file
        """
        if self._process is None:
            logger.debug("[NATS] No process to stop")
            return

        logger.info(f"[NATS] Stopping server (PID={self._process.pid})...")

        if self._process.returncode is not None:
            logger.info(f"[NATS] Process already exited with code {self._process.returncode}")
        else:
            # Send SIGTERM
            self._process.terminate()

            try:
                # Wait up to 5 seconds for graceful shutdown
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                logger.info("[NATS] Server stopped gracefully")
            except asyncio.TimeoutError:
                # Force kill
                logger.warning("[NATS] Server did not stop gracefully, force killing...")
                self._process.kill()
                await self._process.wait()
                logger.info("[NATS] Server force killed")

        # Cleanup temp config file
        if self._config_file and self._config_file.exists():
            try:
                self._config_file.unlink()
                logger.debug(f"[NATS] Removed config file: {self._config_file}")
            except Exception as e:
                logger.warning(f"[NATS] Failed to remove config file: {e}")

        # Remove instance tracking file
        if self._instance_file.exists():
            try:
                self._instance_file.unlink()
                logger.debug(f"[NATS] Removed instance file: {self._instance_file}")
            except Exception as e:
                logger.warning(f"[NATS] Failed to remove instance file: {e}")

        self._process = None

    def _write_instance_file(self, server_info: dict):
        """
        Write instance tracking file to pantheon_dir.

        Args:
            server_info: Server connection info dict
        """
        try:
            # Ensure pantheon_dir exists
            self.pantheon_dir.mkdir(parents=True, exist_ok=True)

            instance_data = {
                "pid": server_info["pid"],
                "tcp_port": self.tcp_port,
                "ws_port": self.ws_port,
                "http_port": self.http_port,
                "config_file": server_info.get("config_file"),
                "log_file": server_info.get("log_file"),
                "tcp_url": server_info["tcp_url"],
                "ws_url": server_info["ws_url"],
                "http_url": server_info["http_url"],
            }

            with open(self._instance_file, 'w') as f:
                json.dump(instance_data, f, indent=2)

            logger.debug(f"[NATS] Wrote instance file: {self._instance_file}")

        except Exception as e:
            logger.warning(f"[NATS] Failed to write instance file: {e}")

    def cleanup_instance_file(self):
        """
        Remove instance tracking file if it exists.

        This is useful for cleanup operations when NATS process
        was killed externally.
        """
        if self._instance_file.exists():
            try:
                self._instance_file.unlink()
                logger.debug(f"[NATS] Cleaned up instance file: {self._instance_file}")
            except Exception as e:
                logger.warning(f"[NATS] Failed to cleanup instance file: {e}")
