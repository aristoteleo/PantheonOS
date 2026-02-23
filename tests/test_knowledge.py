"""
KnowledgeManager 黑盒测试

测试原则：
- 只测试公开接口（API）
- 不测试内部状态（私有属性）
- 验证输入/输出/功能行为
- 测试数据位于 tests/data/knowledge/

测试模式（通过环境变量 QDRANT_LOCATION 控制）：
- 未设置或本地路径：本地 Path 模式（默认，数据持久化）
- ":memory:"：内存模式（不持久化，最快）
- "http://..."：URL 模式（远程 Qdrant 服务器，完整异步支持）

示例：
  # 默认本地模式
  pytest tests/test_knowledge.py

  # 内存模式
  QDRANT_LOCATION=:memory: pytest tests/test_knowledge.py

  # URL 模式
  QDRANT_LOCATION=http://localhost:6333 pytest tests/test_knowledge.py
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

# Check if llama_index is available
try:
    import llama_index.core
    LLAMA_INDEX_AVAILABLE = True
except ImportError:
    LLAMA_INDEX_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not LLAMA_INDEX_AVAILABLE,
    reason="llama_index.core not installed"
)

# 加载环境变量
from dotenv import load_dotenv

load_dotenv()

from pantheon.toolsets.knowledge.knowledge_manager import KnowledgeToolSet

# 测试数据路径
TEST_DATA_DIR = Path(__file__).parent / "data" / "knowledge"
SAMPLE_FILE = TEST_DATA_DIR / "sample.md"
DOCS_DIR = TEST_DATA_DIR / "docs"


def get_test_config(tmp_dir: str) -> dict:
    """
    根据环境变量返回测试配置

    环境变量：
    - QDRANT_LOCATION: 覆盖 qdrant.location
      - ":memory:" : 纯内存模式
      - 本地路径 : 持久化到本地文件
      - URL : 连接到远程 Qdrant 服务器（如 "http://localhost:6333"）

    如果未设置环境变量，默认使用本地路径模式
    """
    # 基础配置（所有测试都需要独立的 storage_path）
    config = {"knowledge": {"storage_path": str(Path(tmp_dir) / "storage")}}

    # 环境变量会在 load_config() 中自动覆盖
    # 这里不需要手动处理，只需提供基础配置
    return config


async def test_knowledge_full_workflow():
    """完整工作流黑盒测试 - 测试所有公开接口"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        import yaml

        # 配置
        config_path = Path(tmp_dir) / "test_config.yaml"
        config = get_test_config(tmp_dir)

        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # 创建 KM 实例
        km = KnowledgeToolSet(config_path=str(config_path))
        await km.run_setup()

        # ========== 测试 1: Collection 管理 ==========
        # 列表（初始为空）
        result = await km.list_collections()
        assert result["success"] == True
        assert result["total"] == 0
        assert isinstance(result["collections"], list)

        # 创建集合
        result = await km.create_collection(
            name="Test Collection", description="测试集合"
        )
        assert result["success"] == True
        assert "collection" in result
        assert result["collection"]["name"] == "Test Collection"
        assert result["collection"]["description"] == "测试集合"
        collection_id = result["collection"]["id"]

        # 列表（应该有 1 个）
        result = await km.list_collections()
        assert result["total"] == 1
        assert result["collections"][0]["id"] == collection_id

        # ========== 测试 2: Source 管理 (文件) ==========
        if not SAMPLE_FILE.exists():
            pytest.skip(f"测试文件不存在: {SAMPLE_FILE}")

        # 添加文件源
        result = await km.add_sources(
            collection_id=collection_id,
            sources={
                "type": "file",
                "path": str(SAMPLE_FILE),
                "name": "Sample File",
            },
        )
        assert result["success"] == True
        assert "source_ids" in result
        source_id_1 = result["source_ids"][0]

        # 等待处理完成
        await asyncio.sleep(8)

        # 列表源
        result = await km.list_sources(collection_id=collection_id)
        assert result["success"] == True
        assert result["total"] >= 1

        # 验证源信息
        source = next(s for s in result["sources"] if s["id"] == source_id_1)
        assert source["name"] == "Sample File"
        assert source["type"] == "file"
        assert source["status"] in ["active", "processing"]

        # ========== 测试 3: Source 管理 (目录) ==========
        if DOCS_DIR.exists():
            result = await km.add_sources(
                collection_id=collection_id,
                sources={
                    "type": "folder",
                    "path": str(DOCS_DIR),
                    "name": "Docs Folder",
                },
            )
            assert result["success"] == True
            source_id_2 = result["source_ids"][0]

            # 等待处理
            await asyncio.sleep(8)

            # 验证源数量
            result = await km.list_sources(collection_id=collection_id)
            assert result["total"] == 2

        # ========== 测试 4: 基础检索 ==========
        result = await km.search_knowledge(
            query="LlamaIndex",
            collection_ids=[collection_id],
            top_k=3,
            use_hybrid=False,
        )
        assert result["success"] == True
        assert "results" in result
        assert isinstance(result["results"], list)
        assert "searched_collections" in result
        assert collection_id in result["searched_collections"]

        # 验证结果格式
        if len(result["results"]) > 0:
            first_result = result["results"][0]
            assert "text" in first_result
            assert "score" in first_result
            assert isinstance(first_result["score"], (int, float))

        # ========== 测试 5: 混合检索 ==========
        result = await km.search_knowledge(
            query="semantic chunking",
            collection_ids=[collection_id],
            top_k=3,
            use_hybrid=True,
        )
        assert result["success"] == True
        assert "results" in result

        # ========== 测试 6: Reranking ==========
        result = await km.search_knowledge(
            query="document indexing",
            collection_ids=[collection_id],
            top_k=5,
            use_hybrid=True,
        )
        assert result["success"] == True
        # Reranking 后结果数可能 <= top_k
        assert len(result["results"]) <= 5

        # ========== 测试 7: Chat 配置 ==========
        chat_id = "test_chat_001"

        # 获取配置
        result = await km.get_chat_knowledge(chat_id=chat_id)
        assert result["success"] == True
        assert result["config"]["chat_id"] == chat_id
        assert result["config"]["auto_search"] == False
        assert isinstance(result["config"]["active_collection_ids"], list)

        # 启用集合
        result = await km.enable_collection(
            chat_id=chat_id, collection_id=collection_id
        )
        assert result["success"] == True
        assert collection_id in result["config"]["active_collection_ids"]

        # 设置自动搜索
        result = await km.set_auto_search(chat_id=chat_id, enabled=True)
        assert result["success"] == True
        assert result["config"]["auto_search"] == True

        # 验证配置持久化
        result = await km.get_chat_knowledge(chat_id=chat_id)
        assert result["config"]["auto_search"] == True
        assert collection_id in result["config"]["active_collection_ids"]

        # ========== 测试 8: Chat 绑定检索 ==========
        result = await km.search_knowledge(query="test query", chat_id=chat_id, top_k=2)
        assert result["success"] == True
        assert collection_id in result["searched_collections"]
        # Chat 绑定应该只搜索激活的集合
        assert len(result["searched_collections"]) == 1

        # 禁用集合
        result = await km.disable_collection(
            chat_id=chat_id, collection_id=collection_id
        )
        assert result["success"] == True
        assert collection_id not in result["config"]["active_collection_ids"]

        # ========== 测试 9: 错误处理 ==========
        # 不存在的集合
        result = await km.search_knowledge(
            query="test", collection_ids=["col_nonexistent"], top_k=3
        )
        # 应该返回空结果或错误，但不应崩溃
        assert result["success"] == True or "error" in result

        # 不存在的 chat
        result = await km.get_chat_knowledge(chat_id="nonexistent_chat")
        # 应该返回默认配置
        assert result["success"] == True
        assert result["config"]["chat_id"] == "nonexistent_chat"

        # ========== 测试 10: 清理操作 ==========
        # 删除源
        result = await km.remove_source(
            collection_id=collection_id, source_id=source_id_1
        )
        assert result["success"] == True

        # 验证删除
        result = await km.list_sources(collection_id=collection_id)
        remaining_sources = [s for s in result["sources"] if s["id"] == source_id_1]
        assert len(remaining_sources) == 0

        # 删除集合
        result = await km.delete_collection(collection_id=collection_id)
        assert result["success"] == True

        # 验证集合已删除
        result = await km.list_collections()
        assert result["total"] == 0


async def test_knowledge_collection_crud():
    """测试 Collection CRUD 接口"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        import yaml

        config_path = Path(tmp_dir) / "test_config.yaml"
        config = get_test_config(tmp_dir)

        with open(config_path, "w") as f:
            yaml.dump(config, f)

        km = KnowledgeToolSet(config_path=str(config_path))
        await km.run_setup()

        try:
            # 创建
            result = await km.create_collection(
                name="Test Collection", description="测试"
            )
            assert result["success"] == True
            collection_id = result["collection"]["id"]

            # 列表
            result = await km.list_collections()
            assert result["total"] == 1
            assert result["collections"][0]["name"] == "Test Collection"

            # 删除
            result = await km.delete_collection(collection_id=collection_id)
            assert result["success"] == True

            # 验证删除
            result = await km.list_collections()
            assert result["total"] == 0
        finally:
            # 显式关闭客户端以释放文件锁
            if hasattr(km, "_qdrant_client") and km._qdrant_client:
                km._qdrant_client.close()
            if hasattr(km, "_qdrant_aclient") and km._qdrant_aclient:
                await km._qdrant_aclient.close()


async def test_knowledge_chat_configuration():
    """测试 Chat 配置接口"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        import yaml

        config_path = Path(tmp_dir) / "test_config.yaml"
        config = get_test_config(tmp_dir)

        with open(config_path, "w") as f:
            yaml.dump(config, f)

        km = KnowledgeToolSet(config_path=str(config_path))
        await km.run_setup()

        try:
            # 创建集合
            result = await km.create_collection(name="Test")
            collection_id = result["collection"]["id"]

            chat_id = "test_chat"

            # 获取初始配置
            result = await km.get_chat_knowledge(chat_id=chat_id)
            assert result["success"] == True
            assert result["config"]["auto_search"] == False

            # 启用集合
            result = await km.enable_collection(
                chat_id=chat_id, collection_id=collection_id
            )
            assert result["success"] == True
            assert collection_id in result["config"]["active_collection_ids"]

            # 设置自动搜索
            result = await km.set_auto_search(chat_id=chat_id, enabled=True)
            assert result["success"] == True
            assert result["config"]["auto_search"] == True

            # 禁用集合
            result = await km.disable_collection(
                chat_id=chat_id, collection_id=collection_id
            )
            assert result["success"] == True
            assert collection_id not in result["config"]["active_collection_ids"]

            # 清理：删除创建的集合
            result = await km.delete_collection(collection_id=collection_id)
            assert result["success"] == True
        finally:
            # 显式关闭客户端以释放文件锁
            if hasattr(km, "_qdrant_client") and km._qdrant_client:
                km._qdrant_client.close()
            if hasattr(km, "_qdrant_aclient") and km._qdrant_aclient:
                await km._qdrant_aclient.close()


async def test_knowledge_concurrent_operations():
    """测试并发操作的接口行为"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        import yaml

        config_path = Path(tmp_dir) / "test_config.yaml"
        config = get_test_config(tmp_dir)

        with open(config_path, "w") as f:
            yaml.dump(config, f)

        km = KnowledgeToolSet(config_path=str(config_path))
        await km.run_setup()

        try:
            # 并发创建集合
            tasks = [km.create_collection(name=f"Collection {i}") for i in range(3)]

            results = await asyncio.gather(*tasks)

            # 验证所有操作成功
            collection_ids = []
            for result in results:
                assert result["success"] == True
                assert "collection" in result
                collection_ids.append(result["collection"]["id"])

            # 验证列表
            result = await km.list_collections()
            assert result["total"] == 3

            # 清理：删除所有创建的集合
            for collection_id in collection_ids:
                result = await km.delete_collection(collection_id=collection_id)
                assert result["success"] == True
        finally:
            # 显式关闭客户端以释放文件锁
            if hasattr(km, "_qdrant_client") and km._qdrant_client:
                km._qdrant_client.close()
            if hasattr(km, "_qdrant_aclient") and km._qdrant_aclient:
                await km._qdrant_aclient.close()


async def test_knowledge_api_response_format():
    """测试 API 响应格式的一致性"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        import yaml

        config_path = Path(tmp_dir) / "test_config.yaml"
        config = get_test_config(tmp_dir)

        with open(config_path, "w") as f:
            yaml.dump(config, f)

        km = KnowledgeToolSet(config_path=str(config_path))
        await km.run_setup()

        try:
            # 所有 API 都应该返回包含 "success" 字段的 dict
            result = await km.list_collections()
            assert isinstance(result, dict)
            assert "success" in result

            result = await km.create_collection(name="Test")
            assert isinstance(result, dict)
            assert "success" in result
            collection_id = result["collection"]["id"]

            result = await km.get_chat_knowledge(chat_id="test")
            assert isinstance(result, dict)
            assert "success" in result

            # 清理：删除创建的集合
            result = await km.delete_collection(collection_id=collection_id)
            assert result["success"] == True
        finally:
            # 显式关闭客户端以释放文件锁
            if hasattr(km, "_qdrant_client") and km._qdrant_client:
                km._qdrant_client.close()
            if hasattr(km, "_qdrant_aclient") and km._qdrant_aclient:
                await km._qdrant_aclient.close()


# 如果直接运行此文件
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
