import os
import json
import asyncio
import aiofiles
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from ..toolset import ToolSet, tool
from ..utils.log import logger
from ..rag.build import download_docs, download_from_huggingface
from ..rag.models import DatabaseInfo, DatabaseMetadata
from ..rag.utils import get_metadata_manager
from .rag import search_databases_impl


class RAGManagerToolSet(ToolSet):
    """RAG数据库管理工具集 - 简化版本，移除任务系统"""

    # 默认构建配置常量
    DEFAULT_BUILD_CONFIG = {
        "sources": [],
        "embedding_model": "text-embedding-3-large",
        "chunk_size": 4000,
        "chunk_overlap": 200,
    }

    def __init__(
        self,
        name: str = "ragmanager_toolset",
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)

        # 初始化存储路径
        self.rag_home = Path.home() / ".pantheon-rag"
        self.databases_dir = self.rag_home / "databases"
        self.metadata_file = self.rag_home / "metadata.json"

        # 创建目录结构
        self.rag_home.mkdir(exist_ok=True)
        self.databases_dir.mkdir(exist_ok=True)

        # 初始化元数据文件（如果不存在）
        if not self.metadata_file.exists():
            # 创建空的元数据文件（同步操作，只在初始化时使用）
            import json

            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(DatabaseMetadata().to_dict(), f, indent=2, ensure_ascii=False)

        # 元数据管理器（包含统一缓存）
        self._metadata_manager = get_metadata_manager(self.rag_home)

    async def run_setup(self):
        """重写基类的run_setup来调用initialize"""
        await super().run_setup()
        await self.initialize()

    async def _update_database_stats(self, db_info: DatabaseInfo) -> None:
        """更新数据库统计信息（文档数量和大小）"""
        try:
            vector_db = await self._metadata_manager.get_database(db_info.name)
            if vector_db:
                # 更新文档数量
                doc_count = await vector_db.get_document_count()
                db_info.doc_count = doc_count

                # 更新数据库大小
                db_path = Path(db_info.path)
                size_mb = 0.0
                try:
                    if db_path.exists():
                        for file_path in db_path.rglob("*"):
                            if file_path.is_file():
                                size_mb += file_path.stat().st_size / 1024 / 1024
                except Exception as e:
                    logger.warning(f"Failed to calculate database size for {db_path}: {e}")
                db_info.size_mb = round(size_mb, 2)

                logger.info(
                    f"Updated database stats for {db_info.name}: {doc_count} docs, {db_info.size_mb} MB"
                )
        except Exception as e:
            logger.warning(f"Failed to update database stats for {db_info.name}: {e}")

    async def initialize(self):
        """异步初始化方法"""
        # 确保默认数据库存在
        await self._ensure_default_database()

    async def _ensure_default_database(self):
        """确保存在默认数据库"""
        metadata = await self._metadata_manager.load_metadata()
        if metadata.get_database("default") is None:
            default_db_path = self.databases_dir / "default"
            default_db_path.mkdir(exist_ok=True)

            # 创建构建配置
            build_config = self.DEFAULT_BUILD_CONFIG.copy()

            # 创建数据库元数据文件
            db_metadata = {
                "description": "默认知识库 - 用于快速添加文件",
                "parameters": {
                    "embedding_model": self.DEFAULT_BUILD_CONFIG["embedding_model"],
                    "chunk_size": self.DEFAULT_BUILD_CONFIG["chunk_size"],
                    "chunk_overlap": self.DEFAULT_BUILD_CONFIG["chunk_overlap"],
                },
            }

            import yaml

            metadata_yaml = default_db_path / "metadata.yaml"
            yaml_content = yaml.dump(db_metadata, allow_unicode=True)
            async with aiofiles.open(metadata_yaml, "w", encoding="utf-8") as f:
                await f.write(yaml_content)

            # 创建数据库信息对象
            now = datetime.now()
            db_info = DatabaseInfo(
                name="default",
                path=str(default_db_path),
                description="默认知识库 - 用于快速添加文件",
                status="active",
                doc_count=0,
                size_mb=0.0,
                created_at=now,
                last_updated=now,
                source_type="local",
                build_config=build_config,
            )

            # 添加到元数据并保存
            metadata.add_database(db_info)
            await self._metadata_manager.save_metadata(metadata)
            logger.info("Created default database")

    @tool
    async def list_databases(self) -> List[Dict[str, Any]]:
        """列出所有可用数据库"""
        metadata = await self._metadata_manager.load_metadata()
        databases = []

        for db_info in metadata.databases.values():
            database_dict = db_info.to_dict()
            databases.append(database_dict)

        return databases

    @tool
    async def update_database(
        self,
        name: str,
        description: Optional[str] = None,
        build_config: Optional[Dict[str, Any]] = None,
        create_if_not_exists: bool = False,
    ) -> Dict[str, Any]:
        """统一的数据库创建/更新接口，返回数据库信息"""
        metadata = await self._metadata_manager.load_metadata()

        # 检查数据库是否存在
        existing_db = metadata.get_database(name)
        db_exists = existing_db is not None

        if not db_exists and not create_if_not_exists:
            raise ValueError(
                f"Database '{name}' does not exist and create_if_not_exists is False"
            )

        # 创建或更新数据库配置
        db_path = self.databases_dir / name
        db_path.mkdir(exist_ok=True)

        # 加载现有配置或创建新配置
        if db_exists:
            # 更新现有数据库
            if description is not None:
                existing_db.description = description
            existing_db.build_config.update(build_config or {})
            existing_db.last_updated = datetime.now()
        else:
            # 创建新数据库，使用辅助方法生成默认配置
            build_config = self.DEFAULT_BUILD_CONFIG.copy()
            build_config.update(build_config or {})
            now = datetime.now()
            existing_db = DatabaseInfo(
                name=name,
                path=str(db_path),
                description=description or f"{name} 数据库",
                status="active",
                doc_count=0,
                size_mb=0.0,
                created_at=now,
                last_updated=now,
                source_type="local",
                build_config=build_config,
            )
            metadata.add_database(existing_db)

        # 不再创建config.json文件，所有配置都在metadata.json中

        # 创建或更新数据库元数据文件
        db_metadata = {
            "description": existing_db.description,
            "parameters": {
                "embedding_model": existing_db.build_config.get(
                    "embedding_model", self.DEFAULT_BUILD_CONFIG["embedding_model"]
                ),
                "chunk_size": existing_db.build_config.get(
                    "chunk_size", self.DEFAULT_BUILD_CONFIG["chunk_size"]
                ),
                "chunk_overlap": existing_db.build_config.get(
                    "chunk_overlap", self.DEFAULT_BUILD_CONFIG["chunk_overlap"]
                ),
            },
        }

        import yaml

        metadata_yaml = db_path / "metadata.yaml"
        yaml_content = yaml.dump(db_metadata, allow_unicode=True)
        async with aiofiles.open(metadata_yaml, "w", encoding="utf-8") as f:
            await f.write(yaml_content)

        # 保存元数据
        await self._metadata_manager.save_metadata(metadata)

        # 如果有数据源，开始构建
        current_sources = existing_db.build_config.get("sources", [])
        if current_sources:
            # 设置构建状态
            existing_db.status = "building"
            existing_db.build_status = {
                "operation": "build",
                "progress": 0,
                "started_at": datetime.now().isoformat(),
                "total_items": len(current_sources),
                "processed_items": 0,
                "errors": [],
            }
            # 保存状态并开始异步构建
            await self._metadata_manager.save_metadata(metadata)
            asyncio.create_task(self._build_database_direct(existing_db))

        return existing_db.to_dict()

    async def _build_database_direct(self, db_info: DatabaseInfo):
        """直接构建数据库，更新database状态"""
        try:
            metadata = await self._metadata_manager.load_metadata()
            current_db = metadata.get_database(db_info.name)
            if not current_db:
                return

            db_path = Path(db_info.path)
            sources = current_db.build_config.get("sources", [])

            # 初始化数据库
            vector_db = await self._metadata_manager.get_database(db_info.name)
            if vector_db is None:
                current_db.status = "error"
                current_db.error = f"Failed to initialize database '{db_info.name}'"
                current_db.build_status = None
                current_db.last_operation = {
                    "type": "build",
                    "completed_at": datetime.now().isoformat(),
                    "success": False,
                    "error": current_db.error,
                }
                await self._metadata_manager.save_metadata(metadata)
                return

            processed = 0
            successful_sources = 0
            errors = []

            for source in sources:
                try:
                    # 更新当前处理项
                    current_item = (
                        source.get("name")
                        or source.get("path")
                        or source.get("url")
                        or "Unknown"
                    )

                    # 更新进度
                    current_db.build_status["progress"] = int(
                        (processed / len(sources)) * 100
                    )
                    current_db.build_status["current_item"] = current_item
                    current_db.build_status["processed_items"] = processed
                    await self._metadata_manager.save_metadata(metadata)

                    if source.get("type") == "file":
                        # 处理文件
                        file_path = source.get("path")
                        if file_path and os.path.exists(file_path):
                            await vector_db.insert_from_file(
                                file_path, {"source": file_path}
                            )
                        else:
                            raise FileNotFoundError(f"File not found: {file_path}")

                    elif source.get("type") == "folder":
                        # 处理文件夹
                        folder_path_str = source.get("path")
                        if folder_path_str:
                            folder_path = Path(folder_path_str)
                            if folder_path.exists():
                                for file_path in folder_path.rglob("*.md"):
                                    await vector_db.insert_from_file(
                                        str(file_path), {"source": str(file_path)}
                                    )
                                for file_path in folder_path.rglob("*.txt"):
                                    await vector_db.insert_from_file(
                                        str(file_path), {"source": str(file_path)}
                                    )
                            else:
                                raise FileNotFoundError(
                                    f"Folder not found: {folder_path}"
                                )

                    elif source.get("type") == "website":
                        # 处理网站
                        url = source.get("url")
                        if url:
                            temp_dir = db_path / "temp_download"
                            temp_dir.mkdir(exist_ok=True)

                            try:
                                await download_docs(url, str(temp_dir), max_depth=1)
                                # 处理下载的文件
                                for file_path in temp_dir.rglob("*.md"):
                                    await vector_db.insert_from_file(
                                        str(file_path), {"source": url}
                                    )
                            finally:
                                # 清理临时文件
                                import shutil

                                if temp_dir.exists():
                                    shutil.rmtree(temp_dir)

                    # 如果执行到这里说明source处理成功
                    successful_sources += 1
                    processed += 1

                except Exception as e:
                    logger.error(f"Error processing source {source}: {e}")
                    errors.append(f"Error processing {current_item}: {str(e)}")
                    processed += 1

            # 任务完成
            if successful_sources > 0:
                # 更新文档计数和大小信息
                await self._update_database_stats(current_db)

                current_db.status = "active"
                current_db.build_status = None
                current_db.last_operation = {
                    "type": "build",
                    "completed_at": datetime.now().isoformat(),
                    "success": True,
                    "sources_processed": successful_sources,
                    "total_sources": len(sources),
                }
                if errors:
                    current_db.last_operation["errors"] = errors
                logger.info(
                    f"Build completed for database {db_info.name}: {successful_sources}/{len(sources)} sources processed"
                )
            else:
                current_db.status = "error"
                current_db.error = "All sources failed to process"
                current_db.build_status = None
                current_db.last_operation = {
                    "type": "build",
                    "completed_at": datetime.now().isoformat(),
                    "success": False,
                    "error": current_db.error,
                    "errors": errors,
                }
                logger.error(
                    f"Build failed for database {db_info.name}: all {len(sources)} sources failed"
                )

            await self._metadata_manager.save_metadata(metadata)

        except Exception as e:
            logger.error(f"Build failed for database {db_info.name}: {e}")
            metadata = await self._metadata_manager.load_metadata()
            current_db = metadata.get_database(db_info.name)
            if current_db:
                current_db.status = "error"
                current_db.error = str(e)
                current_db.build_status = None
                current_db.last_operation = {
                    "type": "build",
                    "completed_at": datetime.now().isoformat(),
                    "success": False,
                    "error": str(e),
                }
                await self._metadata_manager.save_metadata(metadata)

    @tool
    async def delete_database(self, name: str) -> bool:
        """删除数据库"""
        if name == "default":
            logger.warning("Cannot delete default database")
            return False

        metadata = await self._metadata_manager.load_metadata()
        db_info = metadata.get_database(name)

        if db_info is None:
            return False

        try:
            # 删除数据库目录 - 始终删除数据库名称的顶级目录
            db_name_path = self.databases_dir / name
            if db_name_path.exists():
                import shutil

                shutil.rmtree(db_name_path)
                logger.info(f"Deleted database directory: {db_name_path}")

            # 如果db_path指向不同位置且仍存在，也删除它（防止残留）
            db_path = Path(db_info.path)
            if db_path != db_name_path and db_path.exists():
                shutil.rmtree(db_path)
                logger.info(f"Cleaned up additional path: {db_path}")

            # 从元数据中移除
            metadata.remove_database(name)
            await self._metadata_manager.save_metadata(metadata)

            logger.info(f"Deleted database: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete database {name}: {e}")
            return False

    @tool
    async def import_from_huggingface(
        self,
        repo_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        update_existing: bool = False,
    ) -> Dict[str, Any]:
        """从 Hugging Face 导入预构建数据库，返回数据库信息"""
        if name is None:
            name = repo_id.split("/")[-1]

        if description is None:
            description = f"从 {repo_id} 导入的数据库"

        # 检查数据库是否已存在
        metadata = await self._metadata_manager.load_metadata()
        existing_db = metadata.get_database(name)
        db_exists = existing_db is not None

        if db_exists:
            # 检查是否为已存在的imported database
            if existing_db.source_type != "imported":
                raise ValueError(
                    f"Cannot overwrite local database '{name}'. Local databases must be managed through the edit interface."
                )

            if not update_existing:
                raise ValueError(
                    f"Database '{name}' already exists. Use update_existing=True to update the imported database."
                )

        # 设置数据库路径（不创建目录，等下载时创建）
        db_path = self.databases_dir / name

        # 创建导入信息
        import_info = {"repo_id": repo_id, "last_import": datetime.now()}

        # 创建构建配置
        build_config = self.DEFAULT_BUILD_CONFIG.copy()
        build_config.update({"sources": [{"type": "huggingface", "repo_id": repo_id}]})

        if db_exists:
            # 更新现有数据库
            existing_db.description = description
            existing_db.status = "importing"
            existing_db.last_updated = datetime.now()
            existing_db.build_config = build_config
            existing_db.import_info = import_info
            existing_db.build_status = {
                "operation": "import",
                "progress": 0,
                "started_at": datetime.now().isoformat(),
                "total_items": 1,
                "processed_items": 0,
                "current_item": f"导入 {repo_id}",
                "errors": [],
            }
        else:
            # 创建新数据库记录
            now = datetime.now()
            existing_db = DatabaseInfo(
                name=name,
                path=str(db_path),
                description=description,
                status="importing",
                doc_count=0,
                size_mb=0.0,
                created_at=now,
                last_updated=now,
                source_type="imported",
                build_config=build_config,
                import_info=import_info,
                build_status={
                    "operation": "import",
                    "progress": 0,
                    "started_at": now.isoformat(),
                    "total_items": 1,
                    "processed_items": 0,
                    "current_item": f"导入 {repo_id}",
                    "errors": [],
                },
            )
            metadata.add_database(existing_db)

        await self._metadata_manager.save_metadata(metadata)

        # 异步执行导入任务
        asyncio.create_task(self._import_database_direct(existing_db, repo_id))

        return existing_db.to_dict()

    async def _import_database_direct(self, db_info: DatabaseInfo, repo_id: str):
        """直接导入数据库"""
        try:
            metadata = await self._metadata_manager.load_metadata()
            current_db = metadata.get_database(db_info.name)
            if not current_db:
                return

            # 对于导入数据库，始终使用数据库名称的顶级目录作为下载目标
            db_path = self.databases_dir / db_info.name

            # 如果是更新操作，先清空现有目录
            if db_path.exists():
                import shutil

                shutil.rmtree(db_path)
                logger.info(
                    f"Cleared existing database directory for update: {db_path}"
                )

            # 更新进度
            current_db.build_status["progress"] = 50
            current_db.build_status["current_item"] = f"下载 {repo_id}"
            await self._metadata_manager.save_metadata(metadata)

            # 下载和解压数据库
            await download_from_huggingface(str(db_path), repo_id)

            # 处理解压后的目录结构：如果存在单个子目录，更新db_path指向实际目录
            if db_path.exists():
                subdirs = [d for d in db_path.iterdir() if d.is_dir()]
                if len(subdirs) == 1:
                    # 只有一个子目录，更新路径指向该子目录
                    actual_db_path = subdirs[0]
                    current_db.path = str(actual_db_path)
                    logger.info(f"Updated database path to: {actual_db_path}")
                    logger.info(
                        f"Database will use the extracted subdirectory structure"
                    )

            # 不再创建config.json文件，所有配置都在metadata.json中

            # 先保存路径更新，确保_metadata_manager能正确找到数据库
            await self._metadata_manager.save_metadata(metadata)

            # 更新文档计数和大小信息
            await self._update_database_stats(current_db)

            # 任务完成
            current_db.status = "active"
            current_db.build_status = None
            current_db.last_operation = {
                "type": "import",
                "completed_at": datetime.now().isoformat(),
                "success": True,
                "repo_id": repo_id,
            }

            # 更新import_info中的最后导入时间
            if current_db.import_info:
                current_db.import_info["last_import"] = datetime.now()

            await self._metadata_manager.save_metadata(metadata)
            logger.info(f"Import completed for database {db_info.name} from {repo_id}")

        except Exception as e:
            logger.error(f"Import failed for database {db_info.name}: {e}")
            metadata = await self._metadata_manager.load_metadata()
            current_db = metadata.get_database(db_info.name)
            if current_db:
                current_db.status = "error"
                current_db.error = str(e)
                current_db.build_status = None
                current_db.last_operation = {
                    "type": "import",
                    "completed_at": datetime.now().isoformat(),
                    "success": False,
                    "error": str(e),
                    "repo_id": repo_id,
                }
                await self._metadata_manager.save_metadata(metadata)

    @tool
    async def search_databases(
        self, query: str, databases: Optional[List[str]] = None, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """查询多个数据库，返回聚合结果

        Args:
            query: 查询字符串
            databases: 要查询的数据库列表，None表示查询所有可用数据库
            top_k: 每个数据库返回的最大结果数

        Returns:
            聚合的搜索结果列表
        """
        return await search_databases_impl(query, databases, top_k, self.rag_home)


__all__ = ["RAGManagerToolSet"]
