"""
Knowledge Base 配置加载

配置优先级（从低到高）：
1. config.yaml 默认配置文件
2. 用户指定的配置文件（如果提供）
3. 环境变量覆盖
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml


def expand_path(path: str, context: Dict[str, str]) -> str:
    """
    展开路径中的变量
    例如: ${storage_path}/qdrant_storage -> /home/user/.pantheon-knowledge/qdrant_storage
    """
    for key, value in context.items():
        path = path.replace(f"${{{key}}}", value)
    return os.path.expanduser(path)


def deep_update(base_dict: Dict, update_dict: Dict) -> None:
    """深度合并字典（就地修改 base_dict）"""
    for key, value in update_dict.items():
        if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
            deep_update(base_dict[key], value)
        else:
            base_dict[key] = value


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载配置文件

    配置加载顺序：
    1. 加载 knowledge/config.yaml 作为默认配置
    2. 如果提供 config_path，深度合并用户配置
    3. 使用环境变量覆盖特定字段

    Args:
        config_path: 用户配置文件路径（可选）

    Returns:
        配置字典

    环境变量覆盖：
        QDRANT_LOCATION: 覆盖 qdrant.location
        QDRANT_PATH: 覆盖 qdrant.path
        QDRANT_API_KEY: 覆盖 qdrant.api_key
        QDRANT_PREFER_GRPC: 覆盖 qdrant.prefer_grpc
    """
    import copy

    # 1. 加载默认配置文件 (knowledge/config.yaml)
    default_config_path = Path(__file__).parent / "config.yaml"
    with open(default_config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 2. 如果提供了用户配置文件，深度合并
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f)
            if user_config and "knowledge" in user_config:
                deep_update(config["knowledge"], user_config["knowledge"])

    knowledge_config = config["knowledge"]

    # 3. 环境变量覆盖（优先级最高）
    # 注意：此时还未展开路径变量，环境变量可以包含变量
    if os.getenv("QDRANT_LOCATION"):
        knowledge_config["qdrant"]["location"] = os.getenv("QDRANT_LOCATION")

    if os.getenv("QDRANT_PATH"):
        knowledge_config["qdrant"]["path"] = os.getenv("QDRANT_PATH")

    if os.getenv("QDRANT_API_KEY"):
        knowledge_config["qdrant"]["api_key"] = os.getenv("QDRANT_API_KEY")

    if os.getenv("QDRANT_PREFER_GRPC"):
        knowledge_config["qdrant"]["prefer_grpc"] = os.getenv("QDRANT_PREFER_GRPC").lower() == "true"

    # 4. 展开路径变量
    storage_path = os.path.expanduser(knowledge_config["storage_path"])
    context = {"storage_path": storage_path}

    # 展开 qdrant.location（如果包含变量）
    if knowledge_config["qdrant"]["location"]:
        location = knowledge_config["qdrant"]["location"]
        if isinstance(location, str):
            knowledge_config["qdrant"]["location"] = expand_path(location, context)

    # 展开 qdrant.path（如果存在且不为空）
    if knowledge_config["qdrant"]["path"]:
        knowledge_config["qdrant"]["path"] = expand_path(
            knowledge_config["qdrant"]["path"], context
        )

    # 展开 metadata.path
    knowledge_config["metadata"]["path"] = expand_path(
        knowledge_config["metadata"]["path"], context
    )

    # 5. 确保必要的目录存在
    Path(storage_path).mkdir(parents=True, exist_ok=True)

    # 如果 location 是本地路径（不是 :memory: 也不是 URL），创建目录
    location = knowledge_config["qdrant"]["location"]
    if location and location != ":memory:" and not location.startswith("http"):
        Path(location).mkdir(parents=True, exist_ok=True)

    return config


def get_storage_path(config: Optional[Dict[str, Any]] = None) -> Path:
    """获取存储路径"""
    if config is None:
        config = load_config()
    return Path(os.path.expanduser(config["knowledge"]["storage_path"]))


def get_qdrant_params(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    从配置中提取 Qdrant 客户端参数

    返回可直接传递给 QdrantClient 的参数字典

    Qdrant 客户端支持的参数：
    - location: str | None - 存储位置（":memory:", 本地路径, 或留空用 url）
    - url: str | None - 远程服务器 URL
    - path: str | None - 本地存储路径（已废弃，使用 location）
    - api_key: str | None - API key
    - prefer_grpc: bool - 是否使用 gRPC
    """
    qdrant_config = config["knowledge"]["qdrant"]
    params = {}

    location = qdrant_config.get("location")

    # 判断 location 类型
    if location == ":memory:":
        # 内存模式
        params["location"] = ":memory:"
    elif location and location.startswith("http"):
        # URL 模式（远程服务器）
        params["url"] = location
        if qdrant_config.get("api_key"):
            params["api_key"] = qdrant_config["api_key"]
        if qdrant_config.get("prefer_grpc"):
            params["prefer_grpc"] = qdrant_config["prefer_grpc"]
    elif location:
        # 本地路径模式
        params["path"] = location
    elif qdrant_config.get("path"):
        # 向后兼容：如果没有 location 但有 path
        params["path"] = qdrant_config["path"]
    else:
        # 默认使用内存模式
        params["location"] = ":memory:"

    return params
