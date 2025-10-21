"""
VectorStoreBackend - 向量存储后端

职责：
- 管理 Qdrant 客户端（sync + async）
- Embedding 模型
- 文档分块（Node Parser）
- 索引构建和持久化
- 向量检索（混合搜索 + Reranking）
"""

import os
import asyncio
from pathlib import Path
from typing import List, Dict, Any

from llama_index.core.schema import TextNode, NodeWithScore
from pantheon.utils.log import logger


class VectorStoreBackend:
    """
    向量存储后端，封装 Qdrant + LlamaIndex 的所有操作
    """

    def __init__(
        self,
        qdrant_params: Dict[str, Any],
        storage_path: Path,
        embedding_config: Dict[str, Any],
        chunking_config: Dict[str, Any],
        retrieval_config: Dict[str, Any],
    ):
        """
        初始化向量存储后端

        Args:
            qdrant_params: Qdrant 客户端参数（从 get_qdrant_params 获取）
            storage_path: 存储路径（用于索引文件）
            embedding_config: Embedding 配置
            chunking_config: 分块配置
            retrieval_config: 检索配置
        """
        self.qdrant_params = qdrant_params
        self.storage_path = storage_path
        self.embedding_config = embedding_config
        self.chunking_config = chunking_config
        self.retrieval_config = retrieval_config

        # 延迟初始化的组件
        self._qdrant_client = None
        self._qdrant_aclient = None
        self._use_async = False
        self._embed_model = None
        self._node_parser = None
        self._reranker = None
        self._setup_completed = False

    async def setup(self):
        """
        初始化所有组件（延迟加载）

        包括：
        - Qdrant 客户端
        - Embedding 模型
        - Node Parser
        - Reranker
        """
        if self._setup_completed:
            return

        logger.info("Setting up VectorStoreBackend components...")

        try:
            from qdrant_client import QdrantClient, AsyncQdrantClient
            from llama_index.embeddings.openai import OpenAIEmbedding
            from llama_index.core.node_parser import SemanticSplitterNodeParser
            from llama_index.postprocessor.flashrank_rerank import FlashRankRerank

            # 1. 初始化 Qdrant 客户端
            if "url" in self.qdrant_params:
                # URL 模式：创建 sync + async 客户端
                self._qdrant_client = QdrantClient(**self.qdrant_params)
                self._qdrant_aclient = AsyncQdrantClient(**self.qdrant_params)
                self._use_async = True
                logger.info(
                    f"Qdrant clients initialized (URL mode) at: {self.qdrant_params['url']}"
                )
            else:
                # 本地模式（path 或 :memory:）：只创建同步客户端
                self._qdrant_client = QdrantClient(**self.qdrant_params)
                self._qdrant_aclient = None
                self._use_async = False
                location = self.qdrant_params.get("location") or self.qdrant_params.get(
                    "path"
                )
                logger.info(f"Qdrant client initialized (local mode) at: {location}")

            # 2. 初始化 Embedding 模型
            embed_kwargs = {
                "model": self.embedding_config["model"],
                "api_key": os.getenv("OPENAI_API_KEY"),
            }
            if os.getenv("OPENAI_API_BASE"):
                embed_kwargs["api_base"] = os.getenv("OPENAI_API_BASE")

            self._embed_model = OpenAIEmbedding(**embed_kwargs)
            logger.info(
                f"Embedding model initialized: {self.embedding_config['model']}"
            )

            # 3. 初始化 Node Parser (Semantic Chunking)
            self._node_parser = SemanticSplitterNodeParser(
                buffer_size=self.chunking_config["buffer_size"],
                breakpoint_percentile_threshold=self.chunking_config[
                    "breakpoint_percentile"
                ],
                embed_model=self._embed_model,
            )
            logger.info("Semantic chunking initialized")

            # 4. Reranker 延迟加载（仅在需要时初始化）
            # 这样可以避免启动时因为模型文件缺失而失败
            if self.retrieval_config["use_rerank"]:
                logger.info(
                    f"FlashRank reranker lazy loading enabled for: {self.retrieval_config['rerank_model']}"
                )

            self._setup_completed = True
            logger.info("VectorStoreBackend setup completed")

        except Exception as e:
            logger.error(f"Failed to setup VectorStoreBackend: {e}")
            raise

    def build_index_sync(
        self, collection_id: str, nodes: List[TextNode]
    ) -> Dict[str, Any]:
        """
        同步构建索引并持久化（在线程池中运行）

        Args:
            collection_id: Collection ID
            nodes: 文档节点列表

        Returns:
            结果字典 {"success": bool, "node_count": int}
        """
        try:
            from llama_index.core import VectorStoreIndex, StorageContext
            from llama_index.vector_stores.qdrant import QdrantVectorStore

            # 创建 Qdrant Vector Store
            qdrant_collection_name = f"collection_{collection_id}"
            vector_store = QdrantVectorStore(
                collection_name=qdrant_collection_name,
                client=self._qdrant_client,
                aclient=self._qdrant_aclient,
                enable_hybrid=self.retrieval_config["use_hybrid"],
                batch_size=self.embedding_config["batch_size"],
                parallel=self.embedding_config.get("parallel", 4),
            )

            # 创建存储上下文
            index_dir = self.storage_path / "indexes" / collection_id
            index_dir.mkdir(parents=True, exist_ok=True)

            storage_context = StorageContext.from_defaults(vector_store=vector_store)

            # 构建索引
            index = VectorStoreIndex(
                nodes=nodes,
                storage_context=storage_context,
                embed_model=self._embed_model,
                show_progress=True,
                use_async=self._use_async,
            )

            # 持久化
            index.storage_context.persist(persist_dir=str(index_dir))

            logger.info(
                f"Qdrant hybrid index built and persisted for collection {collection_id}"
            )

            return {"success": True, "node_count": len(nodes)}

        except Exception as e:
            logger.error(f"Failed to build index for collection {collection_id}: {e}")
            return {"success": False, "error": str(e)}

    async def search(
        self,
        collection_id: str,
        query: str,
        top_k: int = 5,
        use_hybrid: bool = True,
    ) -> List[NodeWithScore]:
        """
        在指定 collection 中搜索

        Args:
            collection_id: Collection ID
            query: 查询文本
            top_k: 返回结果数量
            use_hybrid: 是否使用混合搜索

        Returns:
            搜索结果节点列表
        """
        results = []

        try:
            from llama_index.core import VectorStoreIndex, StorageContext
            from llama_index.vector_stores.qdrant import QdrantVectorStore

            # 加载索引
            qdrant_collection_name = f"collection_{collection_id}"
            index_dir = self.storage_path / "indexes" / collection_id

            vector_store = QdrantVectorStore(
                collection_name=qdrant_collection_name,
                client=self._qdrant_client,
                aclient=self._qdrant_aclient,
                enable_hybrid=use_hybrid,
                batch_size=self.embedding_config["batch_size"],
                parallel=self.embedding_config.get("parallel", 4),
            )

            storage_context = StorageContext.from_defaults(
                vector_store=vector_store, persist_dir=str(index_dir)
            )

            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                storage_context=storage_context,
                embed_model=self._embed_model,
                use_async=self._use_async,
            )

            # 构建检索器
            retriever_kwargs = {"similarity_top_k": top_k}
            if use_hybrid:
                retriever_kwargs["vector_store_query_mode"] = "hybrid"
                retriever_kwargs["sparse_top_k"] = self.retrieval_config["sparse_top_k"]

            # 添加 reranker 作为 postprocessor（延迟加载）
            reranker = self._get_reranker()
            if reranker:
                retriever_kwargs["node_postprocessors"] = [reranker]

            retriever = index.as_retriever(**retriever_kwargs)

            # 执行检索
            if self._use_async:
                nodes = await retriever.aretrieve(query)
            else:
                # 本地模式：使用 run_in_executor
                loop = asyncio.get_event_loop()
                nodes = await loop.run_in_executor(None, retriever.retrieve, query)

            results = nodes

        except Exception as e:
            logger.error(f"Failed to search in collection3 {collection_id}: {e}")

        return results

    async def delete_collection(self, collection_id: str) -> Dict[str, Any]:
        """
        删除 collection 的向量数据和索引文件

        Args:
            collection_id: Collection ID

        Returns:
            结果字典 {"success": bool}
        """
        try:
            qdrant_collection_name = f"collection_{collection_id}"

            # 删除 Qdrant collection
            if self._use_async:
                await self._qdrant_aclient.delete_collection(qdrant_collection_name)
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, self._qdrant_client.delete_collection, qdrant_collection_name
                )

            logger.info(f"Qdrant collection deleted: {qdrant_collection_name}")

            # 删除索引文件
            index_dir = self.storage_path / "indexes" / collection_id
            if index_dir.exists():
                import shutil

                shutil.rmtree(index_dir)
                logger.info(f"Index files deleted: {index_dir}")

            return {"success": True}

        except Exception as e:
            logger.error(f"Failed to delete collection {collection_id}: {e}")
            return {"success": False, "error": str(e)}

    async def delete_source_vectors(
        self, collection_id: str, source_id: str
    ) -> Dict[str, Any]:
        """
        删除指定 source 的向量

        Args:
            collection_id: Collection ID
            source_id: Source ID

        Returns:
            结果字典 {"success": bool, "deleted_count": int}
        """
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            qdrant_collection_name = f"collection_{collection_id}"

            # 构建过滤器
            filter_condition = Filter(
                must=[
                    FieldCondition(key="source_id", match=MatchValue(value=source_id))
                ]
            )

            # 删除向量
            if self._use_async:
                result = await self._qdrant_aclient.delete(
                    collection_name=qdrant_collection_name,
                    points_selector=filter_condition,
                )
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    self._qdrant_client.delete,
                    qdrant_collection_name,
                    None,
                    None,
                    filter_condition,
                )

            logger.info(f"Deleted {result} points from Qdrant for source {source_id}")

            return {"success": True, "deleted_count": result}

        except Exception as e:
            logger.error(f"Failed to delete source vectors for {source_id}: {e}")
            return {"success": False, "error": str(e)}

    def _get_reranker(self):
        """
        延迟加载 Reranker
        仅在第一次需要时初始化，避免启动时模型加载失败

        Returns:
            FlashRankRerank 实例或 None
        """
        if not self.retrieval_config["use_rerank"]:
            return None

        # 如果已经初始化过，直接返回
        if self._reranker is not None:
            return self._reranker

        try:
            from llama_index.postprocessor.flashrank_rerank import FlashRankRerank

            logger.info(f"Initializing FlashRank reranker: {self.retrieval_config['rerank_model']}")
            self._reranker = FlashRankRerank(
                model=self.retrieval_config["rerank_model"],
                top_n=self.retrieval_config["rerank_top_n"],
            )
            logger.info(f"FlashRank reranker initialized successfully")
            return self._reranker
        except Exception as e:
            logger.warning(f"Failed to initialize reranker, continuing without reranking: {e}")
            return None

    def parse_nodes(self, documents: List) -> List[TextNode]:
        """
        将文档解析为节点（分块）

        Args:
            documents: LlamaIndex Document 列表

        Returns:
            节点列表
        """
        return self._node_parser.get_nodes_from_documents(documents)

    def close(self):
        """关闭客户端连接"""
        if self._qdrant_client:
            self._qdrant_client.close()

    async def aclose(self):
        """异步关闭客户端连接"""
        if self._qdrant_aclient:
            await self._qdrant_aclient.close()
