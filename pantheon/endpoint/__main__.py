import os
import os.path
import shutil
from dotenv import load_dotenv

import fire
import yaml

from .core import Endpoint
from .hub import EndpointHub
from ..utils.log import logger


HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_TEMPLATE = os.path.join(HERE, "endpoint.yaml")


def generate_config(output_path: str = "endpoint.yaml", overwrite: bool = False):
    if os.path.exists(output_path) and not overwrite:
        logger.warning(f"Config file already exists at {output_path}, skipping")
        return
    shutil.copy(CONFIG_TEMPLATE, output_path)
    logger.info(f"Config file generated at {output_path}")


async def start_endpoint(
    config_path: str | None = None,
    workspace_path: str | None = None,
    id_hash: str | None = None,
):
    if config_path is not None and os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    elif os.path.exists("endpoint.yaml"):
        with open("endpoint.yaml", "r") as f:
            config = yaml.safe_load(f)
    else:
        logger.warning(f"Config file not found at {config_path} using default config.")
        logger.info(
            "If you want to use a custom config file, "
            "please run `python -m pantheon.toolsets.endpoint config` to generate a config file"
        )
        config = None

    # Create endpoint with optional workspace_path and id_hash
    kwargs = {}
    if id_hash is not None:
        kwargs["id_hash"] = id_hash

    endpoint = Endpoint(config, workspace_path=workspace_path, **kwargs)
    await endpoint.run()


async def start_endpoint_hub(
    config_dir: str | None = None, workspace_base_path: str | None = None, **kwargs
):
    if config_dir is None:
        config_dir = "endpoint_configs"
    if workspace_base_path is None:
        workspace_base_path = "./.endpoint-hub"
    hub = EndpointHub(config_dir, workspace_base_path, **kwargs)
    await hub.run()


if __name__ == "__main__":
    load_dotenv()
    fire.Fire(
        {
            "start": start_endpoint,
            "config": generate_config,
            "hub": start_endpoint_hub,
        }
    )
