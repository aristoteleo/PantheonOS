"""
简化的RAG数据模型

使用dataclass简化模型定义，避免过度工程化
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class DatabaseInfo:
    """数据库信息 - 简化版本"""
    name: str
    path: str
    description: str = ""
    status: str = "active"
    doc_count: int = 0
    size_mb: float = 0.0
    created_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    source_type: str = "local"
    build_config: Dict[str, Any] = field(default_factory=lambda: {"sources": []})
    import_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    build_status: Optional[Dict[str, Any]] = None  # 构建状态信息
    last_operation: Optional[Dict[str, Any]] = None  # 最后操作信息

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.last_updated is None:
            self.last_updated = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，处理datetime序列化"""
        data = asdict(self)

        # 处理datetime序列化
        if self.created_at:
            data["created_at"] = self.created_at.isoformat()
        if self.last_updated:
            data["last_updated"] = self.last_updated.isoformat()

        # 处理import_info中的datetime
        if self.import_info and "last_import" in self.import_info:
            data["import_info"] = self.import_info.copy()
            if isinstance(self.import_info["last_import"], datetime):
                data["import_info"]["last_import"] = self.import_info["last_import"].isoformat()

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatabaseInfo":
        """从字典创建实例"""
        # 处理时间字段
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.now()
        elif created_at is None:
            created_at = datetime.now()

        last_updated = data.get("last_updated")
        if isinstance(last_updated, str):
            try:
                last_updated = datetime.fromisoformat(last_updated)
            except ValueError:
                last_updated = datetime.now()
        elif last_updated is None:
            last_updated = datetime.now()

        # 处理构建配置
        build_config = data.get("build_config", {})
        if "sources" not in build_config:
            build_config["sources"] = data.get("sources", [])

        # 处理import_info中的datetime
        import_info = data.get("import_info")
        if import_info and "last_import" in import_info:
            last_import = import_info["last_import"]
            if isinstance(last_import, str):
                try:
                    import_info = import_info.copy()
                    import_info["last_import"] = datetime.fromisoformat(last_import)
                except ValueError:
                    pass  # 保持原值

        return cls(
            name=data["name"],
            path=data["path"],
            description=data.get("description", ""),
            status=data.get("status", "active"),
            doc_count=data.get("doc_count", 0),
            size_mb=data.get("size_mb", 0.0),
            created_at=created_at,
            last_updated=last_updated,
            source_type=data.get("source_type", "local"),
            build_config=build_config,
            import_info=import_info,
            error=data.get("error"),
            build_status=data.get("build_status"),
            last_operation=data.get("last_operation")
        )


# TaskInfo class removed - functionality integrated into DatabaseInfo.build_status and .last_operation


@dataclass
class DatabaseMetadata:
    """数据库元数据容器 - 简化版本"""
    databases: Dict[str, DatabaseInfo] = field(default_factory=dict)

    def add_database(self, db_info: DatabaseInfo) -> None:
        """添加或更新数据库"""
        self.databases[db_info.name] = db_info

    def get_database(self, name: str) -> Optional[DatabaseInfo]:
        """获取数据库信息"""
        return self.databases.get(name)

    def remove_database(self, name: str) -> bool:
        """删除数据库"""
        if name in self.databases:
            del self.databases[name]
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "databases": {
                name: db.to_dict() for name, db in self.databases.items()
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatabaseMetadata":
        """从字典创建元数据"""
        instance = cls()
        databases_data = data.get("databases", {})

        for name, db_data in databases_data.items():
            try:
                db_info = DatabaseInfo.from_dict(db_data)
                instance.add_database(db_info)
            except Exception as e:
                # 记录日志但继续处理其他数据库
                print(f"Warning: Failed to load database '{name}': {e}")

        return instance


# 向后兼容的类型别名
DatabaseDict = Dict[str, Any]