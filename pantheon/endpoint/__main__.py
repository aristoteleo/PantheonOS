import os
import os.path
import shutil
import json
from dotenv import load_dotenv

import fire
import yaml

from .core import Endpoint
from .hub import EndpointHub
from pantheon.utils.log import logger
from pantheon.settings import get_settings, load_jsonc

# Load .env files: cwd/.env > ~/.pantheon/.env > ~/.env (legacy fallback)
load_dotenv(override=True)
load_dotenv(os.path.join(os.path.expanduser("~"), ".pantheon", ".env"), override=False)
load_dotenv(os.path.join(os.path.expanduser("~"), ".env"), override=False)


# Template locations
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(os.path.dirname(HERE), "factory", "templates")


def generate_config(output_path: str = ".pantheon/settings.json", overwrite: bool = False):
    """Generate a settings.json config file from the template.
    
    Args:
        output_path: Output path for the config file (default: .pantheon/settings.json)
        overwrite: Whether to overwrite existing file
    """
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    
    if os.path.exists(output_path) and not overwrite:
        logger.warning(f"Config file already exists at {output_path}, skipping (use --overwrite to force)")
        return
    
    # Copy settings.json template
    template_path = os.path.join(TEMPLATES_DIR, "settings.json")
    if os.path.exists(template_path):
        shutil.copy(template_path, output_path)
        logger.info(f"Config file generated at {output_path}")
    else:
        logger.error(f"Template file not found at {template_path}")


def generate_mcp_config(output_path: str = ".pantheon/mcp.json", overwrite: bool = False):
    """Generate a mcp.json config file from the template.
    
    Args:
        output_path: Output path for the config file (default: .pantheon/mcp.json)
        overwrite: Whether to overwrite existing file
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    
    if os.path.exists(output_path) and not overwrite:
        logger.warning(f"Config file already exists at {output_path}, skipping (use --overwrite to force)")
        return
    
    template_path = os.path.join(TEMPLATES_DIR, "mcp.json")
    if os.path.exists(template_path):
        shutil.copy(template_path, output_path)
        logger.info(f"MCP config file generated at {output_path}")
    else:
        logger.error(f"Template file not found at {template_path}")


async def start_endpoint(
    config_path: str | None = None,
    workspace_path: str | None = None,
    id_hash: str | None = None,
):
    """Start the Endpoint service.
    
    Args:
        config_path: Path to config file (supports .json, .jsonc, .yaml, .yml)
                    If not provided, uses Settings module to load from .pantheon/settings.json
        workspace_path: Override workspace path
        id_hash: Fixed id_hash for stable service_id generation
    """
    config = None
    
    # Priority 1: Explicit config_path
    if config_path is not None and os.path.exists(config_path):
        ext = os.path.splitext(config_path)[1].lower()
        if ext in (".json", ".jsonc"):
            from pathlib import Path
            config = load_jsonc(Path(config_path))
            logger.info(f"Loaded config from {config_path}")
        elif ext in (".yaml", ".yml"):
            # Backward compatibility: support legacy YAML configs
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded YAML config from {config_path} (consider migrating to .pantheon/settings.json)")
    
    # Priority 2: Legacy endpoint.yaml in current directory (backward compatibility)
    elif os.path.exists("endpoint.yaml"):
        with open("endpoint.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        logger.warning(
            "Using legacy endpoint.yaml. Consider migrating to .pantheon/settings.json. "
            "Run `python -m pantheon.endpoint config` to generate the new config format."
        )
    
    # Priority 3: Use Settings module (new default behavior)
    else:
        # Settings module will handle the three-layer config loading
        logger.info("Using Settings module for configuration")
        # config=None will trigger Endpoint.default_config() which uses Settings
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
    fire.Fire(
        {
            "start": start_endpoint,
            "config": generate_config,
            "mcp-config": generate_mcp_config,
            "hub": start_endpoint_hub,
        }
    )

