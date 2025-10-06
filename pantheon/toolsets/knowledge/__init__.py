"""
Knowledge Base ToolSet

知识库管理工具集，提供文档索引、检索和管理功能。
"""

from .config import get_storage_path, load_config
from .knowledge_manager import KnowledgeToolSet
from .models import ChatKnowledgeConfig, CollectionInfo, SearchResult, SourceInfo

__all__ = [
    "KnowledgeToolSet",
    "SourceInfo",
    "CollectionInfo",
    "ChatKnowledgeConfig",
    "SearchResult",
    "load_config",
    "get_storage_path",
]
