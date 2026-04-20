"""
VectorStoreBackend - Vector Store Backend

Responsibilities:
- Manage Qdrant clients (sync + async)
- Embedding model
- Document chunking (Node Parser)
- Index building and persistence
- Vector retrieval (hybrid search + reranking)
"""

import asyncio
from pathlib import Path
from typing import List, Dict, Any, TYPE_CHECKING

from pantheon.utils.log import logger

if TYPE_CHECKING:
    from llama_index.core.schema import TextNode, NodeWithScore


class VectorStoreBackend:
    """
    Vector store backend that encapsulates all Qdrant + LlamaIndex operations.
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
        Initialize the vector store backend.

        Args:
            qdrant_params: Qdrant client parameters (from get_qdrant_params)
            storage_path: Storage path (for index files)
            embedding_config: Embedding configuration
            chunking_config: Chunking configuration
            retrieval_config: Retrieval configuration
        """
        self.qdrant_params = qdrant_params
        self.storage_path = storage_path
        self.embedding_config = embedding_config
        self.chunking_config = chunking_config
        self.retrieval_config = retrieval_config

        # Lazily initialized components
        self._qdrant_client = None
        self._qdrant_aclient = None
        self._use_async = False
        self._embed_model = None
        self._node_parser = None
        self._reranker = None
        self._setup_completed = False

    async def setup(self):
        """
        Initialize all components (lazy loading).

        Includes:
        - Qdrant client
        - Embedding model
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

            # 1. Initialize Qdrant client
            if "url" in self.qdrant_params:
                # URL mode: create sync + async clients
                self._qdrant_client = QdrantClient(**self.qdrant_params)
                self._qdrant_aclient = AsyncQdrantClient(**self.qdrant_params)
                self._use_async = True
                logger.info(
                    f"Qdrant clients initialized (URL mode) at: {self.qdrant_params['url']}"
                )
            else:
                # Local mode (path or :memory:): create sync client only
                self._qdrant_client = QdrantClient(**self.qdrant_params)
                self._qdrant_aclient = None
                self._use_async = False
                location = self.qdrant_params.get("location") or self.qdrant_params.get(
                    "path"
                )
                logger.info(f"Qdrant client initialized (local mode) at: {location}")

            # 2. Initialize embedding model
            from pantheon.utils.llm_providers import get_openai_effective_config

            api_base, api_key = get_openai_effective_config()
            embed_kwargs = {
                "model": self.embedding_config["model"],
                "api_key": api_key,
            }
            if api_base:
                embed_kwargs["api_base"] = api_base

            self._embed_model = OpenAIEmbedding(**embed_kwargs)
            logger.info(
                f"Embedding model initialized: {self.embedding_config['model']}"
            )

            # 3. Initialize Node Parser (Semantic Chunking)
            self._node_parser = SemanticSplitterNodeParser(
                buffer_size=self.chunking_config["buffer_size"],
                breakpoint_percentile_threshold=self.chunking_config[
                    "breakpoint_percentile"
                ],
                embed_model=self._embed_model,
            )
            logger.info("Semantic chunking initialized")

            # 4. Reranker lazy loading (initialize only when needed)
            # This avoids startup failures due to missing model files
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
        self, collection_id: str, nodes: List["TextNode"]
    ) -> Dict[str, Any]:
        """
        Build index synchronously and persist (runs in thread pool).

        Args:
            collection_id: Collection ID
            nodes: List of document nodes

        Returns:
            Result dict {"success": bool, "node_count": int}
        """
        try:
            from llama_index.core import VectorStoreIndex, StorageContext
            from llama_index.vector_stores.qdrant import QdrantVectorStore

            # Create Qdrant Vector Store
            qdrant_collection_name = f"collection_{collection_id}"
            vector_store = QdrantVectorStore(
                collection_name=qdrant_collection_name,
                client=self._qdrant_client,
                aclient=self._qdrant_aclient,
                enable_hybrid=self.retrieval_config["use_hybrid"],
                batch_size=self.embedding_config["batch_size"],
                parallel=self.embedding_config.get("parallel", 4),
            )

            # Create storage context
            index_dir = self.storage_path / "indexes" / collection_id
            index_dir.mkdir(parents=True, exist_ok=True)

            storage_context = StorageContext.from_defaults(vector_store=vector_store)

            # Build index
            index = VectorStoreIndex(
                nodes=nodes,
                storage_context=storage_context,
                embed_model=self._embed_model,
                show_progress=True,
                use_async=self._use_async,
            )

            # Persist
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
    ) -> List["NodeWithScore"]:
        """
        Search in the specified collection.

        Args:
            collection_id: Collection ID
            query: Query text
            top_k: Number of results to return
            use_hybrid: Whether to use hybrid search

        Returns:
            List of search result nodes
        """
        results = []

        try:
            from llama_index.core import VectorStoreIndex, StorageContext
            from llama_index.vector_stores.qdrant import QdrantVectorStore

            # Load index
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

            # Build retriever
            retriever_kwargs = {"similarity_top_k": top_k}
            if use_hybrid:
                retriever_kwargs["vector_store_query_mode"] = "hybrid"
                retriever_kwargs["sparse_top_k"] = self.retrieval_config["sparse_top_k"]

            # Add reranker as postprocessor (lazy loading)
            reranker = self._get_reranker()
            if reranker:
                retriever_kwargs["node_postprocessors"] = [reranker]

            retriever = index.as_retriever(**retriever_kwargs)

            # Execute retrieval
            if self._use_async:
                nodes = await retriever.aretrieve(query)
            else:
                # Local mode: use run_in_executor
                loop = asyncio.get_event_loop()
                nodes = await loop.run_in_executor(None, retriever.retrieve, query)

            results = nodes

        except Exception as e:
            logger.error(f"Failed to search in collection3 {collection_id}: {e}")

        return results

    async def delete_collection(self, collection_id: str) -> Dict[str, Any]:
        """
        Delete the collection's vector data and index files.

        Args:
            collection_id: Collection ID

        Returns:
            Result dict {"success": bool}
        """
        try:
            qdrant_collection_name = f"collection_{collection_id}"

            # Delete Qdrant collection
            if self._use_async:
                await self._qdrant_aclient.delete_collection(qdrant_collection_name)
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, self._qdrant_client.delete_collection, qdrant_collection_name
                )

            logger.info(f"Qdrant collection deleted: {qdrant_collection_name}")

            # Delete index files
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
        Delete vectors for the specified source.

        Args:
            collection_id: Collection ID
            source_id: Source ID

        Returns:
            Result dict {"success": bool, "deleted_count": int}
        """
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            qdrant_collection_name = f"collection_{collection_id}"

            # Build filter
            filter_condition = Filter(
                must=[
                    FieldCondition(key="source_id", match=MatchValue(value=source_id))
                ]
            )

            # Delete vectors
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
        Lazy load the reranker.
        Only initialize on first use to avoid startup failures due to missing models.

        Returns:
            FlashRankRerank instance or None
        """
        if not self.retrieval_config["use_rerank"]:
            return None

        # Return immediately if already initialized
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

    def parse_nodes(self, documents: List) -> List["TextNode"]:
        """
        Parse documents into nodes (chunking).

        Args:
            documents: List of LlamaIndex Documents

        Returns:
            List of nodes
        """
        return self._node_parser.get_nodes_from_documents(documents)

    def close(self):
        """Close client connections."""
        if self._qdrant_client:
            self._qdrant_client.close()

    async def aclose(self):
        """Close client connections asynchronously."""
        if self._qdrant_aclient:
            await self._qdrant_aclient.close()
