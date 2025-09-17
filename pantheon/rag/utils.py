"""
RAG工具模块 - 提供公共的元数据管理和搜索功能
"""

import json
import aiofiles
from pathlib import Path
from typing import Optional, List, Dict, Any

from .models import DatabaseMetadata
from ..utils.log import logger


class RAGMetadataManager:
    """RAG元数据管理器 - 提供统一的元数据加载和保存功能"""

    def __init__(self, rag_home: Optional[Path] = None):
        self.rag_home = rag_home or Path.home() / ".pantheon-rag"
        self.metadata_file = self.rag_home / "metadata.json"
        # 统一的数据库实例缓存
        self._database_cache: Dict[str, Any] = {}

    async def load_metadata(self) -> DatabaseMetadata:
        """异步加载元数据"""
        try:
            if self.metadata_file.exists():
                async with aiofiles.open(self.metadata_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                    return DatabaseMetadata.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load metadata: {e}")
        return DatabaseMetadata()

    async def save_metadata(self, metadata: DatabaseMetadata):
        """异步保存元数据"""
        try:
            content = json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False)
            async with aiofiles.open(self.metadata_file, "w", encoding="utf-8") as f:
                await f.write(content)
            # 清空数据库缓存，因为元数据已更新
            self.clear_database_cache()
            logger.debug("Metadata saved and database cache cleared")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")

    async def get_available_databases(self, metadata: Optional[DatabaseMetadata] = None) -> List[str]:
        """获取所有可用数据库列表"""
        if metadata is None:
            metadata = await self.load_metadata()

        available_databases = []
        for db_info in metadata.databases.values():
            # 检查数据库状态
            if db_info.status == "active":
                db_path = Path(db_info.path)
                # 验证数据库路径存在
                if db_path.exists() and (db_path / "metadata.yaml").exists():
                    available_databases.append(db_info.name)
                else:
                    logger.warning(f"Database '{db_info.name}' marked as active but files missing")

        return available_databases

    async def get_database(self, db_name: str):
        """统一的数据库实例获取方法，带缓存"""
        if db_name in self._database_cache:
            return self._database_cache[db_name]

        metadata = await self.load_metadata()
        db_info = metadata.get_database(db_name)

        if db_info is None:
            logger.warning(f"Database '{db_name}' not found in metadata")
            return None

        db_path = Path(db_info.path)

        # 检查数据库是否可用
        if not db_path.exists():
            logger.warning(f"Database path does not exist: {db_path}")
            return None

        metadata_yaml = db_path / "metadata.yaml"
        if not metadata_yaml.exists():
            logger.warning(f"Database metadata.yaml not found: {metadata_yaml}")
            return None

        try:
            # 延迟导入以避免循环依赖
            from .vectordb import VectorDB
            vector_db = VectorDB(db_path)
            self._database_cache[db_name] = vector_db
            return vector_db
        except Exception as e:
            logger.error(f"Failed to initialize database '{db_name}': {e}")
            return None

    def clear_database_cache(self) -> None:
        """清空数据库缓存"""
        self._database_cache.clear()
        logger.info("Database cache cleared")

    async def force_reload_metadata(self) -> DatabaseMetadata:
        """强制重新加载元数据，忽略任何缓存"""
        return await self.load_metadata()


# 全局实例缓存，按 rag_home 路径区分
_manager_cache: Dict[str, RAGMetadataManager] = {}

def get_metadata_manager(rag_home: Optional[Path] = None) -> RAGMetadataManager:
    """获取元数据管理器实例"""
    if rag_home is None:
        rag_home = Path.home() / ".pantheon-rag"

    # 使用规范化的路径作为缓存键
    cache_key = str(rag_home.resolve())

    if cache_key not in _manager_cache:
        _manager_cache[cache_key] = RAGMetadataManager(rag_home)
        logger.debug(f"Created new metadata manager for {cache_key}")

    return _manager_cache[cache_key]