"""
Knowledge Base 数据模型定义
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime


@dataclass
class SourceInfo:
    """源信息 (文件/文件夹/URL)"""
    id: str
    collection_id: str
    name: str
    type: str  # "file" | "folder" | "url"
    path: str
    status: str  # "processing" | "active" | "error"
    doc_count: int
    file_types: List[str]
    added_at: float
    error: Optional[str] = None
    progress: Optional[dict] = None  # {current: int, total: int}

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceInfo":
        """从字典创建"""
        return cls(**data)


@dataclass
class CollectionInfo:
    """集合信息"""
    id: str
    name: str
    description: str
    status: str  # "active" | "error"
    source_ids: List[str]
    total_docs: int
    embedding_model: str
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CollectionInfo":
        """从字典创建"""
        return cls(**data)


@dataclass
class ChatKnowledgeConfig:
    """Chat 知识库配置"""
    chat_id: str
    active_collection_ids: List[str] = field(default_factory=list)
    auto_search: bool = False

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ChatKnowledgeConfig":
        """从字典创建"""
        return cls(**data)


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    text: str
    metadata: dict
    score: float
    collection_id: str

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResult":
        """从字典创建"""
        return cls(**data)
