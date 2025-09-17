import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..toolset import ToolSet, tool
from ..utils.log import logger
from ..rag.utils import get_metadata_manager


async def search_databases_impl(
    query: str,
    databases: Optional[List[str]] = None,
    top_k: int = 5,
    rag_home: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """共享的数据库搜索实现

    Args:
        query: 查询字符串
        databases: 要查询的数据库列表，None表示查询所有可用数据库
        top_k: 每个数据库返回的最大结果数
        rag_home: RAG存储根目录

    Returns:
        聚合的搜索结果列表
    """
    if rag_home is None:
        rag_home = Path.home() / ".pantheon-rag"

    # 使用公共元数据管理器
    metadata_manager = get_metadata_manager(rag_home)

    async def _get_database(db_name: str):
        """获取数据库实例"""
        return await metadata_manager.get_database(db_name)

    async def _search_single_database(
        db_name: str, query: str, top_k: int
    ) -> List[Dict[str, Any]]:
        """搜索单个数据库"""
        try:
            vector_db = await _get_database(db_name)
            if vector_db is None:
                return []

            # 执行查询
            raw_results = await vector_db.query(query, top_k)

            # 转换结果格式，添加数据库来源信息
            formatted_results = []
            for result in raw_results:
                # 简化处理：直接操作字典
                if isinstance(result, dict):
                    # 确保有metadata字段
                    if "metadata" not in result:
                        result["metadata"] = {}
                    # 添加数据库来源信息
                    result["metadata"]["database"] = db_name
                    formatted_results.append(result)

            return formatted_results

        except Exception as e:
            logger.error(f"Error searching database '{db_name}': {e}")
            return []

    logger.info(
        f"[cyan]🔍 Searching databases for: {query[:50]}{'...' if len(query) > 50 else ''}[/cyan]"
    )

    if databases is None:
        # 获取所有可用数据库
        databases = await metadata_manager.get_available_databases()
    else:
        logger.info(f"[dim]Searching specific databases: {', '.join(databases)}[/dim]")

    if not databases:
        logger.warning("[yellow]⚠️ No databases available for search[/yellow]")
        return []

    # 并行查询所有数据库
    search_tasks = []
    for db_name in databases:
        task = _search_single_database(db_name, query, top_k)
        search_tasks.append(task)

    # 等待所有查询完成
    results_lists = await asyncio.gather(*search_tasks, return_exceptions=True)

    # 聚合结果
    all_results = []
    for i, results in enumerate(results_lists):
        if isinstance(results, Exception):
            logger.error(f"Error searching database '{databases[i]}': {results}")
            continue

        if results and isinstance(results, list):
            all_results.extend(results)

    if not all_results:
        logger.info("[yellow]⚠️ No matching documents found in any database[/yellow]")
        return []

    # 按相似度分数排序
    all_results.sort(key=lambda x: x.get("metadata", {}).get("score", 0), reverse=True)

    # 限制结果数量
    final_results = all_results[:top_k]

    logger.info(
        f"[green]✅ Found {len(final_results)} relevant documents from {len(databases)} databases[/green]"
    )

    return final_results


class RAGToolSet(ToolSet):
    """统一RAG查询工具集

    支持跨数据库查询，从单数据库查询改为多数据库聚合查询
    """

    def __init__(
        self,
        name: str = "rag",
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)

        # RAG存储根目录
        self.rag_home = Path.home() / ".pantheon-rag"
        self.databases_dir = self.rag_home / "databases"
        self.metadata_file = self.rag_home / "metadata.json"

        # 元数据管理器（包含统一缓存）
        self._metadata_manager = get_metadata_manager(self.rag_home)

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

    @tool
    async def get_available_databases(self) -> List[str]:
        """获取所有可用数据库列表"""
        available_databases = await self._metadata_manager.get_available_databases()
        logger.info(
            f"[cyan]📊 Found {len(available_databases)} available databases: {', '.join(available_databases)}[/cyan]"
        )
        return available_databases


__all__ = ["RAGToolSet"]
