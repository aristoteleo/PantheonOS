import os
import sys
from pathlib import Path
import base64

from magique.ai.constant import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
from magique.worker import MagiqueWorker
from magique.ai.toolset import run_toolsets, ToolSet, tool
from magique.ai.tools.python import PythonInterpreterToolSet
from magique.ai.tools.file_manager import FileManagerToolSet
from magique.ai.tools.file_transfer import FileTransferToolSet
from magique.ai.tools.web_browse import WebBrowseToolSet
from magique.ai import connect_remote


class PythonInterpreterToolSetPatchMatplotlib(PythonInterpreterToolSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_code = """import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import uuid

GLOBAL_FIG_PATH = None
GLOBAL_FIG_DIR = ".matplotlib_figs"

original_show = plt.show

def __custom_plt_show(*args, **kwargs):
    global GLOBAL_FIG_PATH
    fig = plt.gcf()
    if not fig.get_axes():
        print("No active figure to save.")
        plt.close(fig)
        return

    fig_uuid = str(uuid.uuid4())
    os.makedirs(GLOBAL_FIG_DIR, exist_ok=True)
    GLOBAL_FIG_PATH = os.path.join(GLOBAL_FIG_DIR, fig_uuid + ".png")
    fig.savefig(GLOBAL_FIG_PATH, format='png')
    plt.close(fig)

__plt_show = plt.show
plt.show = __custom_plt_show
"""

    @tool
    async def run_code_in_interpreter(
            self,
            code: str,
            interpreter_id: str,
            result_var_name: str | None = None,
            ) -> dict:
        """Run code in an interpreter.

        Args:
            code: The code to run.
            interpreter_id: The id of the interpreter to run the code in.
            result_var_name: The name of the variable you want to get the result from.
                If not needed, set to None. Default is None.

        Returns:
            A dictionary with the result, stdout, and stderr.
        """
        code = "GLOBAL_FIG_PATH = None\n" + code
        res = await super().run_code_in_interpreter(
            code,
            interpreter_id,
            result_var_name,
        )
        res2 = await super().run_code_in_interpreter(
            "None",
            interpreter_id,
            "GLOBAL_FIG_PATH",
        )
        fig_path = res2["result"]
        if fig_path is not None:
            res["fig_storage_path"] = fig_path
            open_path = fig_path
            if self.workdir:
                open_path = os.path.join(self.workdir, fig_path)
            with open(open_path, "rb") as f:
                base64_img = base64.b64encode(f.read()).decode("utf-8")
            base64_uri = f"data:image/png;base64,{base64_img}"
            res["plt_show_base64_uri"] = base64_uri
            res["hidden_to_model"] = ["plt_show_base64_uri"]
        return res


class Endpoint:
    def __init__(
        self,
        name: str = "pantheon-chatroom-endpoint",
        workspace_path: str = "./.pantheon-chatroom-workspace",
        worker_params: dict | None = None,
    ):
        self.name = name
        _worker_params = {
            "service_name": name,
            "server_host": DEFAULT_SERVER_HOST,
            "server_port": DEFAULT_SERVER_PORT,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.services: list[ToolSet] = []
        self.outer_services: list[dict] = []
        self.setup_handlers()
        self.workspace_path = Path(workspace_path)
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.create_services()

    def setup_handlers(self):
        self.worker.register(self.list_services)
        self.worker.register(self.add_service)
        self.worker.register(self.get_service)
        self.worker.register(self.fetch_image_base64)

    async def list_services(self) -> list[dict]:
        res = []
        for service in self.services:
            res.append({
                "name": service.worker.service_name,
                "id": service.worker.service_id,
            })
        for s in self.outer_services:
            res.append({
                "name": s["name"],
                "id": s["id"],
            })
        return res

    async def fetch_image_base64(self, image_path: str) -> dict:
        """Fetch an image and return the base64 encoded image."""
        if '..' in image_path:
            return {"success": False, "error": "Image path cannot contain '..'"}
        i_path = self.workspace_path / image_path
        if not i_path.exists():
            return {"success": False, "error": "Image does not exist"}
        format = i_path.suffix.lower()
        if format not in [".jpg", ".jpeg", ".png", ".gif"]:
            return {"success": False, "error": "Image format must be jpg, jpeg, png or gif"}
        with open(i_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
            data_uri = f"data:image/{format};base64,{b64}"
        return {
            "success": True,
            "image_path": image_path,
            "data_uri": data_uri,
        }

    async def add_service(self, service_id: str):
        """Add a service to the endpoint."""
        for s in self.services:
            if s.worker.service_id == service_id:
                return {"success": False, "error": "Service already exists"}
        try:
            s = await connect_remote(service_id, self.worker.server_host, self.worker.server_port)
            info = await s.fetch_service_info()
            self.outer_services.append({
                "id": service_id,
                "name": info.service_name,
            })
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_service(self, service_id_or_name: str) -> dict | None:
        """Get a service by id or name."""
        for s in self.services:
            if (
                s.worker.service_id == service_id_or_name
                or s.worker.service_name == service_id_or_name
            ):
                return {
                    "id": s.worker.service_id,
                    "name": s.worker.service_name,
                }
        for s in self.outer_services:
            if (
                s["id"] == service_id_or_name
                or s["name"] == service_id_or_name
            ):
                return s
        return None

    def create_services(self):
        toolset = PythonInterpreterToolSetPatchMatplotlib(
            name="python_interpreter",
            workdir=str(self.workspace_path),
        )
        self.services.append(toolset)
        toolset = FileManagerToolSet(
            name="file_manager",
            path=str(self.workspace_path),
        )
        self.services.append(toolset)
        toolset = FileTransferToolSet(
            name="file_transfer",
            path=str(self.workspace_path),
        )
        self.services.append(toolset)
        toolset = WebBrowseToolSet(
            name="web_browse",
        )
        self.services.append(toolset)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        async with run_toolsets(self.services, log_level=log_level):
            logger.info(f"Remote Server: {self.worker.server_uri}")
            logger.info(f"Service Name: {self.worker.service_name}")
            logger.info(f"Service ID: {self.worker.service_id}")
            return await self.worker.run()
