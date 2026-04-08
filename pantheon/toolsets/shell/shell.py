import os
import re
import shlex
import uuid
from pathlib import Path

from ._shell import AsyncShell, ShellStatus
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
from pantheon.utils.image_detection import snapshot_images, diff_snapshots, encode_images_to_uris
from pantheon.internal.package_runtime.context import build_context_env

_PYTHON_CMD_RE = re.compile(r"(?:^|\s|&&|\|)python[23]?\s", re.IGNORECASE)


class ShellToolSet(ToolSet):
    """Shell toolset for running shell commands.

    Args:
        name: The name of the toolset.
        **kwargs: Additional keyword arguments.
    """

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.clientid_to_shellid = {}
        self.shells = {}
        self.workdir = Path(workdir).expanduser().resolve() if workdir else Path.cwd()

    def _prepare_shell_env(self) -> dict:
        return os.environ.copy()

    def _current_context_dict(self) -> dict:
        ctx = dict(self.get_context() or {})
        return ctx

    def _build_runtime_env(self) -> dict:
        effective_workdir = self._get_effective_workdir() or str(self.workdir)
        return build_context_env(
            workdir=effective_workdir,
            context_variables=self._current_context_dict(),
        )

    async def _apply_context_to_shell(self, shell: AsyncShell):
        # cd to effective workdir if set
        effective_workdir = self._get_effective_workdir()
        if effective_workdir:
            try:
                await shell.run_command(f"cd {shlex.quote(effective_workdir)}")
            except Exception:
                logger.warning("Failed to cd to workdir in shell session")

        env = self._build_runtime_env()
        if not env:
            return

        # Calculate environment variables size
        total_size = sum(len(str(k)) + len(str(v)) for k, v in env.items())

        # For small environments, use traditional method (faster)
        if total_size < 10000:  # 10KB threshold
            # Use appropriate command separator for platform
            separator = " & " if shell.is_windows else " && "
            set_cmd = "set" if shell.is_windows else "export"

            exports = separator.join(
                f"{set_cmd} {key}={shlex.quote(value)}" for key, value in env.items()
            )
            try:
                await shell.run_command(exports)
                return
            except Exception as e:
                logger.debug(f"Traditional export failed: {e}, trying alternative method")

        # For large environments, use platform-specific method to avoid ARG_MAX limit
        logger.debug(
            f"Using alternative method to set {len(env)} environment variables ({total_size} bytes)"
        )

        # Windows: Use multiple set commands (cmd.exe doesn't have heredoc)
        # Unix: Use source with heredoc to execute in current shell
        if shell.is_windows:
            # Windows: Execute set commands in batches to avoid ARG_MAX
            # Split into chunks of ~5KB each
            chunk_size = 5000
            current_chunk = []
            current_size = 0

            for key, value in env.items():
                cmd = f"set {key}={shlex.quote(value)}"
                cmd_size = len(cmd)

                if current_size + cmd_size > chunk_size and current_chunk:
                    # Execute current chunk
                    chunk_cmd = " & ".join(current_chunk)
                    try:
                        await shell.run_command(chunk_cmd)
                    except Exception as e:
                        logger.error(f"Failed to set environment variables chunk: {e}")
                        raise
                    current_chunk = []
                    current_size = 0

                current_chunk.append(cmd)
                current_size += cmd_size

            # Execute remaining chunk
            if current_chunk:
                chunk_cmd = " & ".join(current_chunk)
                try:
                    await shell.run_command(chunk_cmd)
                except Exception as e:
                    logger.error(f"Failed to set environment variables chunk: {e}")
                    raise
        else:
            # Unix: Use source with heredoc (bash/zsh)
            export_lines = [
                f"export {key}={shlex.quote(value)}"
                for key, value in env.items()
            ]
            export_script = "\n".join(export_lines)

            # Use process substitution with heredoc to execute in current shell
            # This ensures environment variables persist
            heredoc_command = f"""source <(cat <<'PANTHEON_ENV_EOF'
{export_script}
PANTHEON_ENV_EOF
)"""

            try:
                await shell.run_command(heredoc_command)
            except Exception as e:
                logger.error(f"Failed to propagate context variables via heredoc: {e}")
                raise

    @tool(exclude=True)
    async def new_shell(self) -> dict:
        """Create a new shell and return its id.
        Use `run_command` to run commands."""
        shell = AsyncShell()
        shell.env = self._prepare_shell_env()
        initial_output = await shell.start()
        shell_id = str(uuid.uuid4())
        self.shells[shell_id] = shell

        # Show shell creation status
        logger.info(f"[dim]New shell created: {shell_id[:8]}[/dim]")

        return {
            "success": True,
            "shell_id": shell_id,
            "initial_output": initial_output,
        }

    @tool(exclude=True)
    async def close_shell(self, shell_id: str) -> dict:
        """Close a shell.

        Args:
            shell_id: The id of the shell to close.
        """
        shell = self.shells.get(shell_id)
        if shell is None:
            return {"success": False, "error": "Shell not found", "shell_id": shell_id}

        await shell.close()
        del self.shells[shell_id]

        # Show shell closure status
        logger.info(f"[dim]Shell closed: {shell_id[:8]}[/dim]")
        return {"success": True, "shell_id": shell_id}

    @tool(exclude=True)
    async def get_shell_output(
        self,
        shell_id: str,
        timeout: int = 5,
        max_output: int | None = None,
    ) -> dict:
        """Get output from a shell, used to check status of background commands.

        When a command times out, it continues running in the background.
        Use this tool with the shell_id returned from run_command to fetch
        the remaining output and check if the command has completed.

        Args:
            shell_id: The shell ID returned from run_command when it timed out.
            timeout: Seconds to wait for output. Default 5 seconds.
            max_output: Optional maximum length for the output. If the output exceeds this,
                it will be smartly truncated (head + tail).

        Returns:
            dict: {
                "output": str,  # Command output since last read
                "status": "completed" | "timeout"  # Whether command finished
            }
        """
        result = await self.run_command_in_shell(
            shell_id=shell_id,
            command=None,
            timeout=timeout,
        )

        # Apply max_output truncation
        truncated = False
        if max_output and result.get("success") and result.get("output"):
            output = result["output"]
            if len(output) > max_output:
                from pantheon.utils.truncate import truncate_string
                result["output"] = truncate_string(output, max_output)
                truncated = True
        
        # Add truncated flag (always present)
        if "success" in result and result["success"]:
            result["truncated"] = truncated

        return result

    @tool(exclude=True)
    async def run_command_in_shell(
        self,
        shell_id: str,
        command: str | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Execute a command or fetch pending output from an existing shell.

        Args:
            shell_id: Identifier for the shell session (from `new_shell` or managed automatically by `run_command`).
            command: Command string to run. If omitted, the tool drains buffered output from the shell (e.g., after a timeout).
            timeout: Optional timeout in seconds. On timeout, the command keeps running and you can re-call this tool with
                only the shell_id to fetch remaining output.

        Returns:
            dict: Structured result including success flag, `status` ("completed" or "timeout"), and the raw output text.
        """

        shell = self.shells.get(shell_id)
        if shell is None:
            return {"success": False, "error": "Shell not found", "shell_id": shell_id}

        status = "completed"

        try:

            def _handle_timeout(message: str) -> None:
                nonlocal status, output
                status = "timeout"
                logger.info(f"[yellow]⚠️ {message}[/yellow]")
                output += "\n[Warning] The execution of the command was interrupted because of the timeout. "

            if command is not None:
                await self._apply_context_to_shell(shell)
                output, finished = await shell.run_command(command, timeout=timeout)
                if timeout is not None and not finished:
                    _handle_timeout(
                        f"Command timed out after {timeout}s - call run_command without a command to fetch remaining output"
                    )
            else:
                marker = shell.current_marker if shell.current_marker else None
                output, finished = await shell.read_until_marker(
                    marker, timeout=timeout
                )
                if finished and shell.current_marker:
                    shell.status = ShellStatus.IDLE
                    shell.current_marker = None

                if timeout is not None and not finished:
                    _handle_timeout(f"Reading shell output timed out after {timeout}s")
        except Exception as exc:
            return {"success": False, "error": str(exc), "shell_id": shell_id}

        response = {
            "success": True,
            "shell_id": shell_id,
            "status": status,
            "output": output,
        }
        if command is not None:
            response["command"] = command
        return response

    def _is_shell_alive(self, shell_id: str) -> bool:
        """Check if a shell is still alive and responsive."""
        if shell_id not in self.shells:
            return False

        shell = self.shells[shell_id]
        if not shell.process:
            return False

        # Check if process is still running
        if shell.process.returncode is not None:
            return False

        return True

    async def _get_available_shell(self) -> str:
        """Get an available (idle) shell, creating a new one if necessary."""
        # First, try to find an idle shell
        for shell_id, shell in self.shells.items():
            if shell.is_idle() and self._is_shell_alive(shell_id):
                return shell_id

        # No idle shell available, create a new one
        result = await self.new_shell()
        return result["shell_id"]

    async def _restart_shell(self, client_id: str) -> str:
        """Restart a shell for a given client_id."""
        old_shell_id = self.clientid_to_shellid.get(client_id)

        # Clean up old shell if it exists
        if old_shell_id and old_shell_id in self.shells:
            try:
                await self.close_shell(old_shell_id)
            except Exception:
                pass  # Ignore cleanup errors
            finally:
                if old_shell_id in self.shells:
                    del self.shells[old_shell_id]

        # Create new shell
        logger.warning(f"Shell crashed (client_id: {client_id}), restarting...")
        res = await self.new_shell()
        new_shell_id = res["shell_id"]
        self.clientid_to_shellid[client_id] = new_shell_id
        logger.info(f"Shell restarted (client_id: {client_id})")

        return new_shell_id

    @tool
    async def run_command(
        self,
        command: str | None = None,
        shell_id: str | None = None,
        timeout: int | None = None,
        max_output: int | None = None,
    ):
        """Run a shell command and return the result.

        This tool automatically manages shell sessions. Just provide the `command`
        to execute. Environment variables and working directory are preserved
        across commands in the same session.

        For long-running commands (builds, training, data processing), set
        _background=True, which provides incremental output tracking and
        auto-notification on completion.

        Args:
            command: The command to run.
            timeout: Optional timeout in seconds.
            shell_id: Optional. Specify a particular shell ID to use.
                If not provided, automatically uses an available shell.
            max_output: Optional. Max output characters (for verbose commands like
                R package install, npm install). Use smaller values to save tokens.

        Returns:
            dict: {
                "success": bool,       # True if executed successfully
                "output": str,         # Command output (stdout + stderr)
                "status": str,         # "completed" or "timeout"
            }

        Tips:
            - Limit output for paging commands (e.g. `git log -n 5`, `head -n 20`).
            - Use `max_output` as a safety net for verbose commands.

        Examples:
            run_command(command="ls -la")
            run_command(command="R -e 'install(...)'", max_output=5000)
        """
        # Snapshot image files before execution so we can detect new ones
        pre_snapshot: dict[str, float] = {}
        if command and _PYTHON_CMD_RE.search(command):
            pre_snapshot = self._snapshot_images()

        # If shell_id is provided, use it directly (Manual Mode)
        if shell_id:
            result = await self.run_command_in_shell(
                shell_id=shell_id,
                command=command,
                timeout=timeout,
            )
        else:
            # Auto Mode (Client ID based)
            context_dict = dict(self.get_context() or {})
            client_id = context_dict.get("client_id")
            if client_id is None:
                client_id = "default"
                # Debug level - not a real problem, just informational
                logger.debug("No client id provided, using default client id.")

            initial_output = ""
            # Resolve shell_id from client_id mapping
            _mapped_shell_id = self.clientid_to_shellid.get(client_id)

            # Check if we need to create a new shell
            if (_mapped_shell_id is None) or (_mapped_shell_id not in self.shells):
                res = await self.new_shell()
                _mapped_shell_id = res["shell_id"]
                initial_output = res["initial_output"]
                self.clientid_to_shellid[client_id] = _mapped_shell_id

            # Check if shell is still alive before running command
            if not self._is_shell_alive(_mapped_shell_id):
                _mapped_shell_id = await self._restart_shell(client_id)
                initial_output = ""  # New shell will have its own initial output

            # If mapped shell is busy, get an available shell
            mapped_shell = self.shells.get(_mapped_shell_id)
            if mapped_shell and not mapped_shell.is_idle():
                _mapped_shell_id = await self._get_available_shell()

            result = await self.run_command_in_shell(
                shell_id=_mapped_shell_id,
                command=command,
                timeout=timeout,
            )

            # If the shell crashed, restart it but do not rerun the command automatically
            if not result.get("success") and self._should_restart(result.get("error")):
                logger.warning(
                    f"Shell command failed for shell {_mapped_shell_id[:8]}: {result.get('error')}"
                )
                _mapped_shell_id = await self._restart_shell(client_id)  # Update mapping
                return result

            if not result.get("success"):
                return result

            # Prepend initial output from new shell creation if applicable
            if initial_output and result.get("success"):
                combined_output = result.get("output") or ""
                result["output"] = (
                    f"{initial_output}\n{combined_output}"
                    if combined_output
                    else initial_output
                )

        # Apply early truncation if max_output specified
        if max_output and result.get("success") and result.get("output"):
            output = result["output"]
            if len(output) > max_output:
                from pantheon.utils.truncate import truncate_string
                result["output"] = truncate_string(output, max_output)
                result["truncated"] = True
            else:
                result["truncated"] = False
        elif result.get("success"):
            # No max_output specified
            result["truncated"] = False

        # Detect images produced by Python/matplotlib commands so claw
        # channels (e.g. Telegram) can forward them to the user.
        if result.get("success") and command and _PYTHON_CMD_RE.search(command):
            result = self._attach_new_images(result, pre_snapshot)

        return result

    # ------------------------------------------------------------------
    # Image detection helpers
    # ------------------------------------------------------------------

    def _snapshot_images(self) -> dict[str, float]:
        """Return {path: mtime} for image files in the working directory."""
        scan_dir = self._get_effective_workdir() or str(self.workdir)
        return snapshot_images(scan_dir)

    def _attach_new_images(
        self, result: dict, pre_snapshot: dict[str, float]
    ) -> dict:
        """Compare pre/post snapshots; base64-encode any new or updated images.

        Paths under the designated image output directory are excluded
        because ``room.py`` handles those via its own post-execution scan.
        """
        from pantheon.utils.image_detection import IMAGE_OUTPUT_DIR
        post = self._snapshot_images()
        new_paths = diff_snapshots(pre_snapshot, post)
        # Exclude files in the designated image output dir to avoid duplicates
        new_paths = [p for p in new_paths if IMAGE_OUTPUT_DIR not in p]
        if new_paths:
            uris = encode_images_to_uris(new_paths)
            if uris:
                result["base64_uri"] = uris
                result["hidden_to_model"] = ["base64_uri"]
        return result

    def _should_restart(self, error_message: str | None) -> bool:
        if not error_message:
            return False
        error_msg = error_message.lower()
        return any(
            keyword in error_msg
            for keyword in ["broken", "closed", "process", "pipe", "not found"]
        )

    async def run_setup(self):
        """Setup the toolset before running it."""
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )
