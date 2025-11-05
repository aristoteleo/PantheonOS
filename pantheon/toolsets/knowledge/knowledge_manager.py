"""
Knowledge Base Manager - 知识库管理核心类
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

from .config import get_qdrant_params, get_storage_path, load_config
from .models import ChatKnowledgeConfig, CollectionInfo, SearchResult, SourceInfo
from .vector_store import VectorStoreBackend


class KnowledgeToolSet(ToolSet):
    """
    知识库管理 ToolSet

    职责:
    - 管理 Collections 和 Sources 的 CRUD 操作
    - 文档加载、解析、分块、索引
    - 混合检索 (Vector + BM25 + Reranking)
    - 元数据提取和过滤
    - 异步任务管理和进度跟踪
    """

    def __init__(self, config_path: str = None, name="knowledge", **kwargs):
        super().__init__(name=name, **kwargs)

        # 加载配置
        self.config = load_config(config_path)
        self.knowledge_config = self.config["knowledge"]
        self.storage_path = get_storage_path(self.config)

        # 元数据文件路径
        self.metadata_path = Path(self.knowledge_config["metadata"]["path"])
        self._metadata: Dict[str, Any] = {
            "collections": {},
            "sources": {},
            "chat_configs": {},
        }

        # 异步任务跟踪 (内存)
        self._source_tasks: Dict[str, asyncio.Task] = {}
        self._source_status: Dict[str, Dict[str, Any]] = {}

        # 向量存储后端（延迟初始化）
        self._vector_store: Optional[VectorStoreBackend] = None
        self._setup_completed = False

        logger.info(
            f"KnowledgeManager initialized with storage_path: {self.storage_path}"
        )

    async def run_setup(self):
        """初始化组件 (延迟加载)"""
        if self._setup_completed:
            return

        logger.info("Setting up KnowledgeManager components...")

        try:
            # 创建向量存储后端
            qdrant_params = get_qdrant_params(self.config)
            self._vector_store = VectorStoreBackend(
                qdrant_params=qdrant_params,
                storage_path=self.storage_path,
                embedding_config=self.knowledge_config["embedding"],
                chunking_config=self.knowledge_config["chunking"],
                retrieval_config=self.knowledge_config["retrieval"],
            )

            # 初始化后端组件
            await self._vector_store.setup()

            # 初始化 Metadata Extractors (保留在 KnowledgeManager 中)
            metadata_config = self.knowledge_config.get("metadata", {})
            self._metadata_extractors = []

            # 创建 LLM 实例的辅助函数（支持自定义 API base）
            def _create_llm():
                from llama_index.llms.openai import OpenAI

                llm_kwargs = {
                    "model": "gpt-4o-mini",
                    "temperature": 0.1,
                    "api_key": os.getenv("OPENAI_API_KEY"),
                }
                if os.getenv("OPENAI_API_BASE"):
                    llm_kwargs["api_base"] = os.getenv("OPENAI_API_BASE")
                return OpenAI(**llm_kwargs)

            # 标题提取器（需要明确启用，因为需要 LLM）
            if metadata_config.get("extract_title", False):
                try:
                    from llama_index.core.extractors import TitleExtractor

                    llm = _create_llm()
                    self._metadata_extractors.append(TitleExtractor(nodes=5, llm=llm))
                except ImportError:
                    logger.warning(
                        "llama-index-llms-openai not installed, skipping TitleExtractor"
                    )

            # 关键词提取器（需要 LLM）
            if metadata_config.get("extract_keywords", False):
                from llama_index.core.extractors import KeywordExtractor

                llm = _create_llm()
                self._metadata_extractors.append(KeywordExtractor(keywords=5, llm=llm))

            # 摘要提取器（需要 LLM，较重）
            if metadata_config.get("extract_summary", False):
                from llama_index.core.extractors import SummaryExtractor

                llm = _create_llm()
                self._metadata_extractors.append(
                    SummaryExtractor(summaries=["self"], llm=llm)
                )

            if self._metadata_extractors:
                logger.info(
                    f"Initialized {len(self._metadata_extractors)} metadata extractors"
                )

            # 加载元数据
            self._load_metadata()

            self._setup_completed = True
            logger.info(
                "KnowledgeManager setup completed (Qdrant Hybrid Search + FlashRank)"
            )

        except Exception as e:
            logger.error(f"Failed to setup KnowledgeManager: {e}")
            raise

    # ============================================================================
    # 内部方法 - 元数据管理
    # ============================================================================

    def _load_metadata(self):
        """加载元数据从 JSON 文件"""
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    self._metadata = json.load(f)
                logger.info(
                    f"Metadata loaded: {len(self._metadata.get('collections', {}))} collections"
                )
            except Exception as e:
                logger.error(f"Failed to load metadata: {e}")
                self._metadata = {"collections": {}, "sources": {}, "chat_configs": {}}
        else:
            self._save_metadata()

    def _save_metadata(self):
        """保存元数据到 JSON 文件 (原子写入)"""
        try:
            # 先写临时文件
            temp_path = self.metadata_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2, ensure_ascii=False)

            # 原子替换
            temp_path.replace(self.metadata_path)
            logger.debug("Metadata saved successfully")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            raise

    def _get_collection(self, collection_id: str) -> Optional[CollectionInfo]:
        """获取集合信息"""
        data = self._metadata["collections"].get(collection_id)
        return CollectionInfo.from_dict(data) if data else None

    def _get_source(self, source_id: str) -> Optional[SourceInfo]:
        """获取源信息"""
        data = self._metadata["sources"].get(source_id)
        return SourceInfo.from_dict(data) if data else None

    def _get_chat_config(self, chat_id: str) -> ChatKnowledgeConfig:
        """获取 Chat 配置"""
        data = self._metadata["chat_configs"].get(chat_id)
        if data:
            return ChatKnowledgeConfig.from_dict(data)
        else:
            # 创建默认配置
            config = ChatKnowledgeConfig(chat_id=chat_id)
            self._metadata["chat_configs"][chat_id] = config.to_dict()
            self._save_metadata()
            return config

    # ============================================================================
    # Tool 方法 - Collection 管理 (3个)
    # ============================================================================

    @tool(exclude=True)
    async def list_collections(self) -> dict:
        """
        列出所有集合

        Returns:
            {
                "success": bool,
                "collections": List[CollectionInfo],
                "total": int,
                "active_for_chat": List[str]  # 当前 session 激活的集合
            }
        """
        try:
            collections = [
                CollectionInfo.from_dict(data)
                for data in self._metadata["collections"].values()
            ]

            result = {
                "success": True,
                "collections": [c.to_dict() for c in collections],
                "total": len(collections),
            }

            # Get chat_id from session context
            chat_id = self.get_session_id()
            if chat_id:
                chat_config = self._get_chat_config(chat_id)
                result["active_for_chat"] = chat_config.active_collection_ids

            return result

        except Exception as e:
            logger.error(f"Failed to list collections: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def create_collection(self, name: str, description: str = "") -> dict:
        """
        创建新集合

        Args:
            name: 集合名称
            description: 集合描述

        Returns:
            {
                "success": bool,
                "collection": CollectionInfo
            }
        """
        try:
            collection_id = f"col_{uuid.uuid4().hex[:16]}"
            now = datetime.now().timestamp()

            collection = CollectionInfo(
                id=collection_id,
                name=name,
                description=description,
                status="active",
                source_ids=[],
                total_docs=0,
                embedding_model=self.knowledge_config["embedding"]["model"],
                created_at=now,
                updated_at=now,
            )

            self._metadata["collections"][collection_id] = collection.to_dict()
            self._save_metadata()

            logger.info(f"Collection created: {collection_id} - {name}")

            return {"success": True, "collection": collection.to_dict()}

        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def delete_collection(self, collection_id: str) -> dict:
        """
        删除集合

        Args:
            collection_id: 集合 ID

        Returns:
            {"success": bool}
        """
        try:
            if collection_id not in self._metadata["collections"]:
                return {"success": False, "error": "Collection not found"}

            collection = self._get_collection(collection_id)

            # 删除所有关联的 sources
            for source_id in collection.source_ids:
                if source_id in self._metadata["sources"]:
                    del self._metadata["sources"][source_id]

            # 删除集合
            del self._metadata["collections"][collection_id]

            # 从所有 chat configs 中移除
            for chat_config_data in self._metadata["chat_configs"].values():
                if collection_id in chat_config_data["active_collection_ids"]:
                    chat_config_data["active_collection_ids"].remove(collection_id)

            self._save_metadata()

            # 使用 VectorStoreBackend 删除向量数据和索引
            await self._vector_store.delete_collection(collection_id)

            logger.info(f"Collection deleted: {collection_id}")

            return {"success": True}

        except Exception as e:
            logger.error(f"Failed to delete collection: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================================
    # Tool 方法 - Source 管理 (3个，简化版)
    # ============================================================================

    @tool(exclude=True)
    async def add_sources(self, collection_id: str, sources: list[dict] | dict) -> dict:
        """
        添加源到集合（支持单个或批量，后台异步处理）

        Args:
            collection_id: 集合 ID
            sources: 单个源对象或源列表
                - 单个: {"type": "file"|"folder"|"url", "path": str, "name": str(optional)}
                - 批量: [{"type": "file"|"folder"|"url", "path": str, "name": str(optional)}, ...]

        Returns:
            {
                "success": bool,
                "source_ids": list[str],  # 单个源时也返回列表（包含1个元素）
                "message": str
            }
        """
        try:
            # 检查集合是否存在
            collection = self._get_collection(collection_id)
            if not collection:
                return {"success": False, "error": "Collection not found"}

            # 标准化输入：统一转为列表处理
            sources_list = [sources] if isinstance(sources, dict) else sources

            source_ids = []
            for source_data in sources_list:
                try:
                    source_type = source_data.get("type")
                    path = source_data.get("path")
                    name = source_data.get("name")

                    if not source_type or not path:
                        logger.warning(f"Invalid source data: {source_data}")
                        continue

                    # 创建 source
                    source_id = f"src_{uuid.uuid4().hex[:16]}"
                    source_name = name or Path(path).name

                    source = SourceInfo(
                        id=source_id,
                        collection_id=collection_id,
                        name=source_name,
                        type=source_type,
                        path=path,
                        status="processing",
                        doc_count=0,
                        file_types=[],
                        added_at=datetime.now().timestamp(),
                    )

                    # 保存到元数据
                    self._metadata["sources"][source_id] = source.to_dict()
                    collection.source_ids.append(source_id)
                    source_ids.append(source_id)

                    # 启动后台任务处理文档索引（文档加载在线程池中运行，不阻塞事件循环）
                    task = asyncio.create_task(self._process_source_async(source_id))
                    self._source_tasks[source_id] = task

                    logger.info(f"Source added: {source_id} - {source_name}")

                except Exception as e:
                    logger.error(f"Failed to add source {source_data}: {e}")
                    continue

            # 保存更新后的集合元数据
            if source_ids:
                self._metadata["collections"][collection_id] = collection.to_dict()
                self._save_metadata()

            return {
                "success": True,
                "source_ids": source_ids,
                "message": f"Added {len(source_ids)} source(s), processing in background",
            }

        except Exception as e:
            logger.error(f"Failed to add sources: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def remove_source(self, collection_id: str, source_id: str) -> dict:
        """
        从集合中移除源

        Args:
            collection_id: 集合 ID
            source_id: 源 ID

        Returns:
            {"success": bool}
        """
        try:
            if source_id not in self._metadata["sources"]:
                return {"success": False, "error": "Source not found"}

            source = self._get_source(source_id)
            if source.collection_id != collection_id:
                return {
                    "success": False,
                    "error": "Source does not belong to this collection",
                }

            # 从集合中移除
            collection = self._get_collection(collection_id)
            if source_id in collection.source_ids:
                collection.source_ids.remove(source_id)
                self._metadata["collections"][collection_id] = collection.to_dict()

            # 删除源
            del self._metadata["sources"][source_id]

            # 更新集合的文档数量
            if collection.total_docs >= source.doc_count:
                collection.total_docs -= source.doc_count
                collection.updated_at = datetime.now().timestamp()
                self._metadata["collections"][collection_id] = collection.to_dict()

            self._save_metadata()

            # 取消后台任务（如果还在运行）
            if source_id in self._source_tasks:
                self._source_tasks[source_id].cancel()
                del self._source_tasks[source_id]

            # 使用 VectorStoreBackend 删除源的向量数据
            await self._vector_store.delete_source_vectors(collection_id, source_id)

            logger.info(f"Source removed: {source_id}")

            return {"success": True}

        except Exception as e:
            logger.error(f"Failed to remove source: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def list_sources(self, collection_id: str) -> dict:
        """
        列出集合中的所有源

        Args:
            collection_id: 集合 ID

        Returns:
            {
                "success": bool,
                "sources": List[SourceInfo],
                "total": int
            }
        """
        try:
            collection = self._get_collection(collection_id)
            if not collection:
                return {"success": False, "error": "Collection not found"}

            sources = [
                self._get_source(source_id).to_dict()
                for source_id in collection.source_ids
                if source_id in self._metadata["sources"]
            ]

            return {"success": True, "sources": sources, "total": len(sources)}

        except Exception as e:
            logger.error(f"Failed to list sources: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================================
    # Tool 方法 - Chat 配置 (4个)
    # ============================================================================

    @tool(exclude=True)
    async def get_chat_knowledge(self) -> dict:
        """
        获取当前 session 的知识库配置

        Returns:
            {
                "success": bool,
                "config": ChatKnowledgeConfig,
                "collections": List[CollectionInfo]
            }
        """
        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            config = self._get_chat_config(chat_id)

            # 获取激活的集合信息
            collections = [
                self._get_collection(cid).to_dict()
                for cid in config.active_collection_ids
                if self._get_collection(cid)
            ]

            return {
                "success": True,
                "config": config.to_dict(),
                "collections": collections,
            }

        except Exception as e:
            logger.error(f"Failed to get chat knowledge config: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def enable_collection(self, collection_id: str) -> dict:
        """
        为当前 session 启用集合

        Args:
            collection_id: 集合 ID

        Returns:
            {
                "success": bool,
                "config": ChatKnowledgeConfig
            }
        """
        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # 检查集合是否存在
            if not self._get_collection(collection_id):
                return {"success": False, "error": "Collection not found"}

            config = self._get_chat_config(chat_id)

            if collection_id not in config.active_collection_ids:
                config.active_collection_ids.append(collection_id)
                self._metadata["chat_configs"][chat_id] = config.to_dict()
                self._save_metadata()

            logger.info(f"Collection {collection_id} enabled for chat {chat_id}")

            return {"success": True, "config": config.to_dict()}

        except Exception as e:
            logger.error(f"Failed to enable collection: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def disable_collection(self, collection_id: str) -> dict:
        """
        为当前 session 禁用集合

        Args:
            collection_id: 集合 ID

        Returns:
            {
                "success": bool,
                "config": ChatKnowledgeConfig
            }
        """
        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            config = self._get_chat_config(chat_id)

            if collection_id in config.active_collection_ids:
                config.active_collection_ids.remove(collection_id)
                self._metadata["chat_configs"][chat_id] = config.to_dict()
                self._save_metadata()

            logger.info(f"Collection {collection_id} disabled for chat {chat_id}")

            return {"success": True, "config": config.to_dict()}

        except Exception as e:
            logger.error(f"Failed to disable collection: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def set_auto_search(self, enabled: bool) -> dict:
        """
        设置当前 session 的自动搜索开关

        Args:
            enabled: 是否启用自动搜索

        Returns:
            {
                "success": bool,
                "config": ChatKnowledgeConfig
            }
        """
        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            config = self._get_chat_config(chat_id)
            config.auto_search = enabled
            self._metadata["chat_configs"][chat_id] = config.to_dict()
            self._save_metadata()

            logger.info(f"Auto search set to {enabled} for chat {chat_id}")

            return {"success": True, "config": config.to_dict()}

        except Exception as e:
            logger.error(f"Failed to set auto search: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================================
    # Tool 方法 - 检索 (1个)
    # ============================================================================

    @tool
    async def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        collection_ids: Optional[List[str]] = None,
        use_hybrid: bool = True,
    ) -> dict:
        """
        搜索知识库

        Args:
            query: 查询文本
            top_k: 返回结果数量
            collection_ids: 可选, 集合ID列表（如果不指定则使用当前 session 的激活集合）
            use_hybrid: 是否使用混合检索

        Returns:
            {
                "success": bool,
                "results": List[SearchResult],
                "searched_collections": List[str]
            }
        """
        try:
            # 确定要搜索的集合
            target_collections = []
            if collection_ids:
                target_collections = collection_ids
            else:
                # Get chat_id from session context
                chat_id = self.get_session_id()
                if chat_id:
                    config = self._get_chat_config(chat_id)
                    target_collections = config.active_collection_ids
                else:
                    # 搜索所有集合
                    target_collections = list(self._metadata["collections"].keys())

            if not target_collections:
                return {"success": True, "results": [], "searched_collections": []}

            # 对每个集合执行检索（已包含 reranking）
            all_results = []
            for collection_id in target_collections:
                try:
                    results = await self._search_in_collection(
                        query=query,
                        collection_id=collection_id,
                        top_k=top_k,  # 直接使用最终 top_k（postprocessor 已处理）
                        use_hybrid=use_hybrid,
                    )
                    all_results.extend(results)
                except Exception as e:
                    logger.warning(
                        f"Failed to search in collection1 {collection_id}: {e}"
                    )

            # 跨集合排序并截取（单集合内已通过 postprocessor reranked）
            all_results.sort(key=lambda x: x.score, reverse=True)
            all_results = all_results[:top_k]

            logger.info(
                f"Search completed: {len(all_results)} results from {len(target_collections)} collections"
            )

            return {
                "success": True,
                "results": [r.to_dict() for r in all_results],
                "searched_collections": target_collections,
            }

        except Exception as e:
            logger.error(f"Failed to search knowledge: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================================
    # 内部方法 - 文档处理和索引
    # ============================================================================

    async def _process_source_async(self, source_id: str):
        """
        后台异步处理源（加载→分块→索引）
        使用 LlamaIndex 的异步 API
        """
        try:
            source = self._get_source(source_id)
            logger.info(f"Processing source: {source_id} - {source.name}")

            # 1. 异步加载文档
            documents = await self._load_documents(source.type, source.path)
            logger.info(f"Loaded {len(documents)} documents from {source.name}")

            # 2. 提取基础元数据并分块
            all_nodes = []
            file_types = set()
            for doc in documents:
                # 提取基础元数据（来源、路径等）
                metadata = self._extract_basic_metadata(doc, source)
                doc.metadata.update(metadata)

                # 记录文件类型
                if "doc_type" in metadata:
                    file_types.add(metadata["doc_type"])

                # 分块
                nodes = self._vector_store.parse_nodes([doc])
                all_nodes.extend(nodes)

            logger.info(f"Created {len(all_nodes)} chunks for {source.name}")

            # 3. 应用 LlamaIndex MetadataExtractors（批量处理）
            if self._metadata_extractors:
                logger.info(
                    f"Applying {len(self._metadata_extractors)} metadata extractors..."
                )
                for extractor in self._metadata_extractors:
                    try:
                        # 批量提取元数据
                        metadata_list = extractor.extract(all_nodes)
                        # 更新节点元数据
                        for node, metadata in zip(all_nodes, metadata_list):
                            node.metadata.update(metadata)
                    except Exception as e:
                        logger.warning(
                            f"Metadata extractor {extractor.__class__.__name__} failed: {e}"
                        )

                logger.info(f"Metadata extraction completed for {len(all_nodes)} nodes")

            # 4. 构建索引
            await self._build_index(source.collection_id, all_nodes)

            # 5. 更新源状态
            source.status = "active"
            source.doc_count = len(documents)
            source.file_types = list(file_types)
            self._metadata["sources"][source_id] = source.to_dict()

            # 6. 更新集合文档数
            collection = self._get_collection(source.collection_id)
            collection.total_docs += len(documents)
            collection.updated_at = datetime.now().timestamp()
            self._metadata["collections"][source.collection_id] = collection.to_dict()

            self._save_metadata()

            logger.info(f"Source processing completed: {source_id}")

        except Exception as e:
            logger.error(f"Failed to process source {source_id}: {e}")
            # 更新为错误状态
            source = self._get_source(source_id)
            source.status = "error"
            source.error = str(e)
            self._metadata["sources"][source_id] = source.to_dict()
            self._save_metadata()

        finally:
            # 清理任务引用
            if source_id in self._source_tasks:
                del self._source_tasks[source_id]

    async def _load_documents(self, source_type: str, path: str):
        """
        异步加载文档（使用 SimpleDirectoryReader 的 aload_data）
        """
        from llama_index.core import SimpleDirectoryReader

        if source_type == "file":
            # 单文件 - SimpleDirectoryReader 会自动使用 pypdf/python-docx
            reader = SimpleDirectoryReader(input_files=[path])
            documents = await reader.aload_data()
        elif source_type == "folder":
            # 文件夹 - 自动递归扫描并使用内置 readers
            # 移除 required_exts 限制，让 SimpleDirectoryReader 自动检测所有支持的格式
            reader = SimpleDirectoryReader(
                input_dir=path,
                recursive=True,
            )
            documents = await reader.aload_data()
        elif source_type == "url":
            # URL（暂不实现）
            raise NotImplementedError("URL source not implemented yet")
        else:
            raise ValueError(f"Unknown source type: {source_type}")

        return documents

    def _extract_basic_metadata(self, document, source: SourceInfo) -> dict:
        """
        提取基础文档元数据（来源信息 + 文档原始 metadata）

        注意: 高级元数据（如标题、关键词、摘要）由 LlamaIndex MetadataExtractors 处理
        """
        metadata = {
            "source_id": source.id,
            "source_name": source.name,
            "source_path": source.path,
            "collection_id": source.collection_id,
            "added_at": source.added_at,
        }

        # 从文档中提取原始元数据
        if hasattr(document, "metadata") and document.metadata:
            # 文档标题
            if "title" in document.metadata:
                metadata["doc_title"] = document.metadata["title"]

            # 文件类型
            if "file_type" in document.metadata:
                metadata["doc_type"] = document.metadata["file_type"]
            elif "file_path" in document.metadata:
                metadata["doc_type"] = Path(document.metadata["file_path"]).suffix[1:]

            # 其他字段（根据配置提取）
            extract_fields = self.knowledge_config["metadata"].get("extract_fields", [])
            for field in extract_fields:
                if field in document.metadata and field not in metadata:
                    metadata[field] = document.metadata[field]

        return metadata

    async def _build_index(self, collection_id: str, nodes: List):
        """构建 Qdrant 原生混合索引（Dense + Sparse Vectors）- 在线程池中运行"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._vector_store.build_index_sync, collection_id, nodes
        )
        if not result["success"]:
            raise Exception(result.get("error", "Failed to build index"))

    async def _search_in_collection(
        self,
        query: str,
        collection_id: str,
        top_k: int,
        use_hybrid: bool,
    ) -> List[SearchResult]:
        """在单个集合中检索（使用 Qdrant 原生混合检索）"""
        results = []

        try:
            # 使用 VectorStoreBackend 进行检索
            nodes = await self._vector_store.search(
                collection_id=collection_id,
                query=query,
                top_k=top_k,
                use_hybrid=use_hybrid,
            )

            # 转换为 SearchResult
            for node in nodes:
                results.append(
                    SearchResult(
                        id=node.node_id,
                        text=node.get_content(),
                        metadata=node.metadata,
                        score=node.score if hasattr(node, "score") else 0.0,
                        collection_id=collection_id,
                    )
                )

        except Exception as e:
            logger.error(f"Failed to search in collection2 {collection_id}: {e}")

        return results
