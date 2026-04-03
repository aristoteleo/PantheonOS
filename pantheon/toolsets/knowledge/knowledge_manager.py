"""
Knowledge Base Manager - Core knowledge base management class.
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
    Knowledge Base Management ToolSet.

    Responsibilities:
    - Manage CRUD operations for Collections and Sources
    - Document loading, parsing, chunking, and indexing
    - Hybrid retrieval (Vector + BM25 + Reranking)
    - Metadata extraction and filtering
    - Async task management and progress tracking
    """

    def __init__(self, config_path: str = None, name="knowledge", **kwargs):
        super().__init__(name=name, **kwargs)

        # Load configuration
        self.config = load_config(config_path)
        self.knowledge_config = self.config["knowledge"]
        self.storage_path = get_storage_path(self.config)

        # Metadata file path
        self.metadata_path = Path(self.knowledge_config["metadata"]["path"])
        self._metadata: Dict[str, Any] = {
            "collections": {},
            "sources": {},
            "chat_configs": {},
        }

        # Async task tracking (in memory)
        self._source_tasks: Dict[str, asyncio.Task] = {}
        self._source_status: Dict[str, Dict[str, Any]] = {}

        # Vector store backend (lazy initialization)
        self._vector_store: Optional[VectorStoreBackend] = None
        self._setup_completed = False

        logger.info(
            f"KnowledgeManager initialized with storage_path: {self.storage_path}"
        )

    async def run_setup(self):
        """Initialize components (lazy loading)."""
        if self._setup_completed:
            return

        logger.info("Setting up KnowledgeManager components...")

        try:
            # Create vector store backend
            qdrant_params = get_qdrant_params(self.config)
            self._vector_store = VectorStoreBackend(
                qdrant_params=qdrant_params,
                storage_path=self.storage_path,
                embedding_config=self.knowledge_config["embedding"],
                chunking_config=self.knowledge_config["chunking"],
                retrieval_config=self.knowledge_config["retrieval"],
            )

            # Initialize backend components
            await self._vector_store.setup()

            # Initialize Metadata Extractors (kept in KnowledgeManager)
            metadata_config = self.knowledge_config.get("metadata", {})
            self._metadata_extractors = []

            # Helper function to create LLM instance (supports custom API base + proxy)
            def _create_llm():
                from llama_index.llms.openai import OpenAI
                from pantheon.settings import get_settings
                from pantheon.utils.llm_providers import get_proxy_kwargs
                settings = get_settings()

                llm_kwargs = {
                    "model": "gpt-4o-mini",
                    "temperature": 0.1,
                    "api_key": settings.get_api_key("OPENAI_API_KEY"),
                }
                api_base = settings.get_api_key("OPENAI_API_BASE")
                if api_base:
                    llm_kwargs["api_base"] = api_base

                # Use proxy if enabled (overrides api_base/api_key)
                proxy_kwargs = get_proxy_kwargs()
                if proxy_kwargs:
                    llm_kwargs["api_base"] = proxy_kwargs["base_url"]
                    llm_kwargs["api_key"] = proxy_kwargs["api_key"]

                return OpenAI(**llm_kwargs)

            # Title extractor (requires explicit enable, needs LLM)
            if metadata_config.get("extract_title", False):
                try:
                    from llama_index.core.extractors import TitleExtractor

                    llm = _create_llm()
                    self._metadata_extractors.append(TitleExtractor(nodes=5, llm=llm))
                except ImportError:
                    logger.warning(
                        "llama-index-llms-openai not installed, skipping TitleExtractor"
                    )

            # Keyword extractor (requires LLM)
            if metadata_config.get("extract_keywords", False):
                from llama_index.core.extractors import KeywordExtractor

                llm = _create_llm()
                self._metadata_extractors.append(KeywordExtractor(keywords=5, llm=llm))

            # Summary extractor (requires LLM, heavier)
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

            # Load metadata
            self._load_metadata()

            self._setup_completed = True
            logger.info(
                "KnowledgeManager setup completed (Qdrant Hybrid Search + FlashRank)"
            )

        except Exception as e:
            logger.error(f"Failed to setup KnowledgeManager: {e}")
            raise

    # ============================================================================
    # Internal Methods - Metadata Management
    # ============================================================================

    def _load_metadata(self):
        """Load metadata from JSON file."""
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
        """Save metadata to JSON file (atomic write)."""
        try:
            # Write to temporary file first
            temp_path = self.metadata_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2, ensure_ascii=False)

            # Atomic replace
            temp_path.replace(self.metadata_path)
            logger.debug("Metadata saved successfully")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            raise

    def _get_collection(self, collection_id: str) -> Optional[CollectionInfo]:
        """Get collection info."""
        data = self._metadata["collections"].get(collection_id)
        return CollectionInfo.from_dict(data) if data else None

    def _get_source(self, source_id: str) -> Optional[SourceInfo]:
        """Get source info."""
        data = self._metadata["sources"].get(source_id)
        return SourceInfo.from_dict(data) if data else None

    def _get_chat_config(self, chat_id: str) -> ChatKnowledgeConfig:
        """Get chat configuration."""
        data = self._metadata["chat_configs"].get(chat_id)
        if data:
            return ChatKnowledgeConfig.from_dict(data)
        else:
            # Create default config
            config = ChatKnowledgeConfig(chat_id=chat_id)
            self._metadata["chat_configs"][chat_id] = config.to_dict()
            self._save_metadata()
            return config

    # ============================================================================
    # Tool Methods - Collection Management (3)
    # ============================================================================

    @tool(exclude=True)
    async def list_collections(self) -> dict:
        """
        List all collections.

        Returns:
            {
                "success": bool,
                "collections": List[CollectionInfo],
                "total": int,
                "active_for_chat": List[str]  # Active collections for current session
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
        Create a new collection.

        Args:
            name: Collection name
            description: Collection description

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
        Delete a collection.

        Args:
            collection_id: Collection ID

        Returns:
            {"success": bool}
        """
        try:
            if collection_id not in self._metadata["collections"]:
                return {"success": False, "error": "Collection not found"}

            collection = self._get_collection(collection_id)

            # Delete all associated sources
            for source_id in collection.source_ids:
                if source_id in self._metadata["sources"]:
                    del self._metadata["sources"][source_id]

            # Delete collection
            del self._metadata["collections"][collection_id]

            # Remove from all chat configs
            for chat_config_data in self._metadata["chat_configs"].values():
                if collection_id in chat_config_data["active_collection_ids"]:
                    chat_config_data["active_collection_ids"].remove(collection_id)

            self._save_metadata()

            # Use VectorStoreBackend to delete vector data and indexes
            await self._vector_store.delete_collection(collection_id)

            logger.info(f"Collection deleted: {collection_id}")

            return {"success": True}

        except Exception as e:
            logger.error(f"Failed to delete collection: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================================
    # Tool Methods - Source Management (3, simplified)
    # ============================================================================

    @tool(exclude=True)
    async def add_sources(self, collection_id: str, sources: list[dict] | dict) -> dict:
        """
        Add sources to a collection (supports single or batch, async background processing).

        Args:
            collection_id: Collection ID
            sources: Single source object or list of sources
                - Single: {"type": "file"|"folder"|"url", "path": str, "name": str(optional)}
                - Batch: [{"type": "file"|"folder"|"url", "path": str, "name": str(optional)}, ...]

        Returns:
            {
                "success": bool,
                "source_ids": list[str],  # Returns list even for single source (contains 1 element)
                "message": str
            }
        """
        try:
            # Check if collection exists
            collection = self._get_collection(collection_id)
            if not collection:
                return {"success": False, "error": "Collection not found"}

            # Normalize input: convert to list for uniform processing
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

                    # Create source
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

                    # Save to metadata
                    self._metadata["sources"][source_id] = source.to_dict()
                    collection.source_ids.append(source_id)
                    source_ids.append(source_id)

                    # Start background task for document indexing (document loading runs in thread pool, non-blocking)
                    task = asyncio.create_task(self._process_source_async(source_id))
                    self._source_tasks[source_id] = task

                    logger.info(f"Source added: {source_id} - {source_name}")

                except Exception as e:
                    logger.error(f"Failed to add source {source_data}: {e}")
                    continue

            # Save updated collection metadata
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
        Remove a source from a collection.

        Args:
            collection_id: Collection ID
            source_id: Source ID

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

            # Remove from collection
            collection = self._get_collection(collection_id)
            if source_id in collection.source_ids:
                collection.source_ids.remove(source_id)
                self._metadata["collections"][collection_id] = collection.to_dict()

            # Delete source
            del self._metadata["sources"][source_id]

            # Update collection document count
            if collection.total_docs >= source.doc_count:
                collection.total_docs -= source.doc_count
                collection.updated_at = datetime.now().timestamp()
                self._metadata["collections"][collection_id] = collection.to_dict()

            self._save_metadata()

            # Cancel background task (if still running)
            if source_id in self._source_tasks:
                self._source_tasks[source_id].cancel()
                del self._source_tasks[source_id]

            # Use VectorStoreBackend to delete source vector data
            await self._vector_store.delete_source_vectors(collection_id, source_id)

            logger.info(f"Source removed: {source_id}")

            return {"success": True}

        except Exception as e:
            logger.error(f"Failed to remove source: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def list_sources(self, collection_id: str) -> dict:
        """
        List all sources in a collection.

        Args:
            collection_id: Collection ID

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
    # Tool Methods - Chat Configuration (4)
    # ============================================================================

    @tool(exclude=True)
    async def get_chat_knowledge(self) -> dict:
        """
        Get the current session's knowledge base configuration.

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

            # Get active collection info
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
        Enable a collection for the current session.

        Args:
            collection_id: Collection ID

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
            # Check if collection exists
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
        Disable a collection for the current session.

        Args:
            collection_id: Collection ID

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
        Set the auto-search toggle for the current session.

        Args:
            enabled: Whether to enable auto-search

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
    # Tool Methods - Retrieval (1)
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
        Search the knowledge base.

        Args:
            query: Query text
            top_k: Number of results to return
            collection_ids: Optional, list of collection IDs (uses current session's active collections if not specified)
            use_hybrid: Whether to use hybrid retrieval

        Returns:
            {
                "success": bool,
                "results": List[SearchResult],
                "searched_collections": List[str]
            }
        """
        try:
            # Determine collections to search
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
                    # Search all collections
                    target_collections = list(self._metadata["collections"].keys())

            if not target_collections:
                return {"success": True, "results": [], "searched_collections": []}

            # Execute retrieval for each collection (includes reranking)
            all_results = []
            for collection_id in target_collections:
                try:
                    results = await self._search_in_collection(
                        query=query,
                        collection_id=collection_id,
                        top_k=top_k,  # Use final top_k directly (postprocessor already handled)
                        use_hybrid=use_hybrid,
                    )
                    all_results.extend(results)
                except Exception as e:
                    logger.warning(
                        f"Failed to search in collection1 {collection_id}: {e}"
                    )

            # Sort across collections and truncate (single collection already reranked via postprocessor)
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
    # Internal Methods - Document Processing and Indexing
    # ============================================================================

    async def _process_source_async(self, source_id: str):
        """
        Async background processing of source (load -> chunk -> index).
        Uses LlamaIndex async API.
        """
        try:
            source = self._get_source(source_id)
            logger.info(f"Processing source: {source_id} - {source.name}")

            # 1. Load documents asynchronously
            documents = await self._load_documents(source.type, source.path)
            logger.info(f"Loaded {len(documents)} documents from {source.name}")

            # 2. Extract basic metadata and chunk
            all_nodes = []
            file_types = set()
            for doc in documents:
                # Extract basic metadata (source, path, etc.)
                metadata = self._extract_basic_metadata(doc, source)
                doc.metadata.update(metadata)

                # Record file type
                if "doc_type" in metadata:
                    file_types.add(metadata["doc_type"])

                # Chunk
                nodes = self._vector_store.parse_nodes([doc])
                all_nodes.extend(nodes)

            logger.info(f"Created {len(all_nodes)} chunks for {source.name}")

            # 3. Apply LlamaIndex MetadataExtractors (batch processing)
            if self._metadata_extractors:
                logger.info(
                    f"Applying {len(self._metadata_extractors)} metadata extractors..."
                )
                for extractor in self._metadata_extractors:
                    try:
                        # Batch extract metadata
                        metadata_list = extractor.extract(all_nodes)
                        # Update node metadata
                        for node, metadata in zip(all_nodes, metadata_list):
                            node.metadata.update(metadata)
                    except Exception as e:
                        logger.warning(
                            f"Metadata extractor {extractor.__class__.__name__} failed: {e}"
                        )

                logger.info(f"Metadata extraction completed for {len(all_nodes)} nodes")

            # 4. Build index
            await self._build_index(source.collection_id, all_nodes)

            # 5. Update source status
            source.status = "active"
            source.doc_count = len(documents)
            source.file_types = list(file_types)
            self._metadata["sources"][source_id] = source.to_dict()

            # 6. Update collection document count
            collection = self._get_collection(source.collection_id)
            collection.total_docs += len(documents)
            collection.updated_at = datetime.now().timestamp()
            self._metadata["collections"][source.collection_id] = collection.to_dict()

            self._save_metadata()

            logger.info(f"Source processing completed: {source_id}")

        except Exception as e:
            logger.error(f"Failed to process source {source_id}: {e}")
            # Update to error status
            source = self._get_source(source_id)
            source.status = "error"
            source.error = str(e)
            self._metadata["sources"][source_id] = source.to_dict()
            self._save_metadata()

        finally:
            # Clean up task reference
            if source_id in self._source_tasks:
                del self._source_tasks[source_id]

    async def _load_documents(self, source_type: str, path: str):
        """
        Load documents asynchronously (using SimpleDirectoryReader's aload_data).
        """
        from llama_index.core import SimpleDirectoryReader

        if source_type == "file":
            # Single file - SimpleDirectoryReader automatically uses pypdf/python-docx
            reader = SimpleDirectoryReader(input_files=[path])
            documents = await reader.aload_data()
        elif source_type == "folder":
            # Folder - automatically scan recursively and use built-in readers
            # Remove required_exts restriction, let SimpleDirectoryReader auto-detect all supported formats
            reader = SimpleDirectoryReader(
                input_dir=path,
                recursive=True,
            )
            documents = await reader.aload_data()
        elif source_type == "url":
            # URL (not implemented yet)
            raise NotImplementedError("URL source not implemented yet")
        else:
            raise ValueError(f"Unknown source type: {source_type}")

        return documents

    def _extract_basic_metadata(self, document, source: SourceInfo) -> dict:
        """
        Extract basic document metadata (source info + document original metadata).

        Note: Advanced metadata (title, keywords, summary) handled by LlamaIndex MetadataExtractors.
        """
        metadata = {
            "source_id": source.id,
            "source_name": source.name,
            "source_path": source.path,
            "collection_id": source.collection_id,
            "added_at": source.added_at,
        }

        # Extract original metadata from document
        if hasattr(document, "metadata") and document.metadata:
            # Document title
            if "title" in document.metadata:
                metadata["doc_title"] = document.metadata["title"]

            # File type
            if "file_type" in document.metadata:
                metadata["doc_type"] = document.metadata["file_type"]
            elif "file_path" in document.metadata:
                metadata["doc_type"] = Path(document.metadata["file_path"]).suffix[1:]

            # Other fields (extract based on config)
            extract_fields = self.knowledge_config["metadata"].get("extract_fields", [])
            for field in extract_fields:
                if field in document.metadata and field not in metadata:
                    metadata[field] = document.metadata[field]

        return metadata

    async def _build_index(self, collection_id: str, nodes: List):
        """Build Qdrant native hybrid index (Dense + Sparse Vectors) - runs in thread pool."""
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
        """Search in a single collection (using Qdrant native hybrid retrieval)."""
        results = []

        try:
            # Use VectorStoreBackend for retrieval
            nodes = await self._vector_store.search(
                collection_id=collection_id,
                query=query,
                top_k=top_k,
                use_hybrid=use_hybrid,
            )

            # Convert to SearchResult
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
