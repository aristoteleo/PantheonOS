#!/usr/bin/env python3
"""
NATS Streaming Tests for IntegratedNotebook
Standard pytest tests for NATS backend streaming functionality

## 运行方法

### 环境要求
启动 NATS 服务器，推荐使用项目配置：
```bash
# 进入 pantheon-agents 目录
cd pantheon-agents

# 使用项目配置启动 NATS（推荐）
nats-server -c nats-ws.conf

# 或使用 Docker 启动基础版本
# docker run -p 4222:4222 nats:latest
```

项目的 nats-ws.conf 配置提供：
- 标准 NATS 端口 4222（用于测试）
- WebSocket 端口 8080（用于前端）
- JetStream 支持
- HTTP 监控端口 8222

### 运行测试
```bash
# 运行所有测试
python -m pytest test_nats_streaming.py -v

# 运行单个测试
python -m pytest test_nats_streaming.py::test_nats_streaming_basic_execution -v

# 抑制清理警告
python -m pytest test_nats_streaming.py -v 2>/dev/null | grep -E "(PASSED|FAILED|===)"
```

## 关于异步清理错误说明

运行测试时可能会看到类似错误：
```
RuntimeError: Event loop is closed
Task was destroyed but it is pending!
```

**这些错误是无害的**，原因：
1. pytest 在测试结束时关闭事件循环，但 NATS 客户端的后台任务仍在运行
2. 这是测试环境的正常现象，不影响测试结果或功能正确性
3. 在生产环境中通过正确的应用生命周期管理可以避免

只要看到 "X passed" 就表明所有功能都工作正常。

## 测试覆盖
- test_nats_streaming_basic_execution: 基本流式执行
- test_nats_streaming_progressive_output: 渐进式输出
- test_nats_streaming_error_handling: 错误处理
- test_nats_streaming_multiple_cells: 多单元格执行
- test_nats_backend_properties: 后端属性
- test_notebook_session_lifecycle: 会话生命周期
"""

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from typing import List, Tuple

import pytest
import pytest_asyncio

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 设置环境变量确保使用NATS后端
os.environ["PANTHEON_REMOTE_BACKEND"] = "nats"
os.environ["NATS_SERVERS"] = "nats://localhost:4222"

from pantheon.remote import (
    RemoteBackendFactory,
    RemoteConfig,
    StreamMessage,
    StreamType,
)
from pantheon.toolsets.notebook import IntegratedNotebookToolSet

# Check if NATS server is available
def _check_nats_available():
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', 4222))
        sock.close()
        return result == 0
    except:
        return False

NATS_AVAILABLE = _check_nats_available()

pytestmark = pytest.mark.skipif(
    not NATS_AVAILABLE,
    reason="NATS server not running on localhost:4222"
)


@pytest_asyncio.fixture
async def nats_backend():
    """NATS backend fixture"""
    config = RemoteConfig.from_config()
    backend = RemoteBackendFactory.create_backend(config)

    try:
        yield backend
    finally:
        # Proper cleanup
        try:
            if hasattr(backend, 'cleanup') and callable(getattr(backend, 'cleanup')):
                await backend.cleanup()
            elif hasattr(backend, 'close') and callable(getattr(backend, 'close')):
                await backend.close()
            elif hasattr(backend, 'disconnect') and callable(getattr(backend, 'disconnect')):
                await backend.disconnect()
        except Exception:
            pass  # Ignore cleanup errors

        # Give time for cleanup
        await asyncio.sleep(0.1)


@pytest_asyncio.fixture
async def notebook_toolset(nats_backend):
    """IntegratedNotebook toolset fixture"""
    notebook = IntegratedNotebookToolSet(
        name="test-notebook", remote_backend=nats_backend, workdir="."
    )
    await notebook.run_setup()

    try:
        yield notebook
    finally:
        # Graceful cleanup
        try:
            await notebook.cleanup()
        except Exception:
            pass  # Ignore cleanup errors

        # Give time for cleanup
        await asyncio.sleep(0.1)


@pytest_asyncio.fixture
async def notebook_session(notebook_toolset):
    """Notebook session fixture"""
    notebook_path = f"test_streaming_{uuid.uuid4().hex[:8]}.ipynb"

    # Use kernel_toolset for session management
    session_result = await notebook_toolset.kernel_toolset.create_session()
    assert session_result["success"], f"Failed to create session: {session_result.get('error')}"

    session_id = session_result["session_id"]

    yield session_id, notebook_path

    # Cleanup
    await notebook_toolset.kernel_toolset.shutdown_session(session_id)
    # Remove test notebook file
    try:
        Path(notebook_path).unlink(missing_ok=True)
    except Exception:
        pass


class StreamMessageCollector:
    """Helper class to collect and analyze stream messages"""

    def __init__(self):
        self.messages: List[Tuple[float, StreamMessage]] = []
        self.start_time = time.time()

    def callback(self, message: StreamMessage):
        """Stream message callback"""
        elapsed = time.time() - self.start_time
        self.messages.append((elapsed, message))

    def reset_timer(self):
        """Reset the timer for new execution"""
        self.start_time = time.time()

    def get_messages_by_type(self, msg_type: str) -> List[Tuple[float, StreamMessage]]:
        """Get messages filtered by type"""
        return [
            (elapsed, msg) for elapsed, msg in self.messages
            if msg.data.get("msg_type") == msg_type
        ]

    def get_stream_outputs(self) -> List[str]:
        """Extract stream output texts"""
        stream_messages = self.get_messages_by_type("stream")
        return [
            msg.data.get("content", {}).get("text", "").strip()
            for _, msg in stream_messages
            if msg.data.get("content", {}).get("text", "").strip()
        ]

    def get_execution_results(self) -> List[str]:
        """Extract execution result outputs"""
        result_messages = self.get_messages_by_type("execute_result")
        return [
            msg.data.get("content", {}).get("data", {}).get("text/plain", "")
            for _, msg in result_messages
        ]

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def message_types(self) -> dict:
        """Get count of each message type"""
        types = {}
        for _, msg in self.messages:
            msg_type = msg.data.get("msg_type", "unknown")
            types[msg_type] = types.get(msg_type, 0) + 1
        return types


@pytest.mark.asyncio
async def test_nats_streaming_basic_execution(notebook_toolset, notebook_session):
    """Test basic NATS streaming functionality with code execution"""
    session_id, notebook_path = notebook_session

    # Setup stream listener
    stream_id = f"notebook_iopub_{session_id}"
    stream_channel = await notebook_toolset.remote_backend.get_or_create_stream(
        stream_id, StreamType.NOTEBOOK
    )

    # Setup message collector
    collector = StreamMessageCollector()
    subscription_id = await stream_channel.subscribe(collector.callback)

    try:
        # Execute simple test code
        test_code = """
print("Hello, NATS streaming!")
result = 2 + 3
print(f"Result: {result}")
result
"""

        collector.reset_timer()

        # Execute cell
        exec_result = await notebook_toolset.execute_cell(
            session_id=session_id,
            code=test_code,
            sync_return=True,
        )

        # Verify execution success
        assert exec_result["success"], f"Execution failed: {exec_result.get('error')}"
        assert exec_result["execution_count"] == 1
        assert len(exec_result["outputs"]) > 0

        # Wait for streaming messages
        await asyncio.sleep(2)

        # Verify streaming messages
        assert collector.message_count > 0, "No streaming messages received"

        # Check message types
        msg_types = collector.message_types
        assert "stream" in msg_types, "No stream messages received"
        assert "execute_result" in msg_types, "No execute_result messages received"

        # Verify stream outputs
        stream_outputs = collector.get_stream_outputs()
        assert "Hello, NATS streaming!" in " ".join(stream_outputs)
        assert "Result: 5" in " ".join(stream_outputs)

        # Verify execution results
        exec_results = collector.get_execution_results()
        assert "5" in exec_results

    finally:
        # Graceful cleanup of streaming
        try:
            await stream_channel.unsubscribe(subscription_id)
            # Give time for unsubscription to complete
            await asyncio.sleep(0.1)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_nats_streaming_progressive_output(notebook_toolset, notebook_session):
    """Test NATS streaming with progressive output (time-based)"""
    session_id, notebook_path = notebook_session

    # Setup stream listener
    stream_id = f"notebook_iopub_{session_id}"
    stream_channel = await notebook_toolset.remote_backend.get_or_create_stream(
        stream_id, StreamType.NOTEBOOK
    )

    collector = StreamMessageCollector()
    subscription_id = await stream_channel.subscribe(collector.callback)

    try:
        # Execute code with progressive output
        test_code = """
import time

print("Starting progressive test...")
for i in range(3):
    time.sleep(0.5)
    print(f"Step {i+1}/3 completed")

print("All steps completed!")
final_result = "SUCCESS"
final_result
"""

        collector.reset_timer()
        start_time = time.time()

        exec_result = await notebook_toolset.execute_cell(
            session_id=session_id,
            code=test_code,
            sync_return=True,
        )

        execution_time = time.time() - start_time

        # Verify execution
        assert exec_result["success"]
        assert execution_time >= 1.5  # Should take at least 1.5s due to sleep

        # Wait for all streaming messages
        await asyncio.sleep(2)

        # Verify progressive output
        stream_outputs = collector.get_stream_outputs()

        # Check all expected outputs are present
        expected_outputs = [
            "Starting progressive test...",
            "Step 1/3 completed",
            "Step 2/3 completed",
            "Step 3/3 completed",
            "All steps completed!"
        ]

        combined_output = " ".join(stream_outputs)
        for expected in expected_outputs:
            assert expected in combined_output, f"Missing expected output: {expected}"

        # Verify timing - messages should be spread over time
        stream_messages = collector.get_messages_by_type("stream")
        if len(stream_messages) >= 2:
            first_time = stream_messages[0][0]
            last_time = stream_messages[-1][0]
            assert last_time - first_time >= 1.0, "Messages not properly spread over time"

    finally:
        # Graceful cleanup of streaming
        try:
            await stream_channel.unsubscribe(subscription_id)
            # Give time for unsubscription to complete
            await asyncio.sleep(0.1)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_nats_streaming_error_handling(notebook_toolset, notebook_session):
    """Test NATS streaming with error conditions"""
    session_id, notebook_path = notebook_session

    # Setup stream listener
    stream_id = f"notebook_iopub_{session_id}"
    stream_channel = await notebook_toolset.remote_backend.get_or_create_stream(
        stream_id, StreamType.NOTEBOOK
    )

    collector = StreamMessageCollector()
    subscription_id = await stream_channel.subscribe(collector.callback)

    try:
        # Execute code that will cause an error
        test_code = """
print("Before error...")
undefined_variable  # This will cause a NameError
print("After error - should not appear")
"""

        collector.reset_timer()

        exec_result = await notebook_toolset.execute_cell(
            session_id=session_id,
            code=test_code,
            sync_return=True,
        )

        # Execution should complete but with error
        assert exec_result["success"] is False or len(exec_result.get("outputs", [])) > 0

        # Wait for streaming messages
        await asyncio.sleep(2)

        # Verify we got messages
        assert collector.message_count > 0

        # We should get stream output before the error
        stream_outputs = collector.get_stream_outputs()
        assert "Before error..." in " ".join(stream_outputs)

        # We should NOT get the output after the error
        assert "After error - should not appear" not in " ".join(stream_outputs)

        # Check if error message is captured (may be in different message types)
        error_found = False
        for _, msg in collector.messages:
            msg_type = msg.data.get("msg_type")
            if msg_type == "error":
                error_content = msg.data.get("content", {})
                if "NameError" in str(error_content) or "undefined_variable" in str(error_content):
                    error_found = True
                    break

        # Note: Error might also be in the execution result outputs
        if not error_found and exec_result.get("outputs"):
            for output in exec_result["outputs"]:
                if output.get("output_type") == "error":
                    if "NameError" in str(output) or "undefined_variable" in str(output):
                        error_found = True
                        break

        assert error_found, "Error information not found in stream or execution result"

    finally:
        # Graceful cleanup of streaming
        try:
            await stream_channel.unsubscribe(subscription_id)
            # Give time for unsubscription to complete
            await asyncio.sleep(0.1)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_nats_streaming_multiple_cells(notebook_toolset, notebook_session):
    """Test NATS streaming with multiple cell executions"""
    session_id, notebook_path = notebook_session

    # Setup stream listener
    stream_id = f"notebook_iopub_{session_id}"
    stream_channel = await notebook_toolset.remote_backend.get_or_create_stream(
        stream_id, StreamType.NOTEBOOK
    )

    collector = StreamMessageCollector()
    subscription_id = await stream_channel.subscribe(collector.callback)

    try:
        # Execute first cell
        code1 = """
x = 10
print(f"First cell: x = {x}")
x
"""

        collector.reset_timer()

        result1 = await notebook_toolset.execute_cell(
            session_id=session_id,
            code=code1,
            sync_return=True,
        )

        assert result1["success"]
        assert result1["execution_count"] == 1

        # Wait a bit
        await asyncio.sleep(1)

        # Execute second cell that uses variable from first cell
        code2 = """
y = x * 2
print(f"Second cell: y = x * 2 = {y}")
y
"""

        result2 = await notebook_toolset.execute_cell(
            session_id=session_id,
            code=code2,
            sync_return=True,
        )

        assert result2["success"]
        assert result2["execution_count"] == 2

        # Wait for all streaming messages
        await asyncio.sleep(2)

        # Verify we got messages for both executions
        assert collector.message_count > 0

        # Check stream outputs contain both cell outputs
        stream_outputs = collector.get_stream_outputs()
        combined_output = " ".join(stream_outputs)

        assert "First cell: x = 10" in combined_output
        assert "Second cell: y = x * 2 = 20" in combined_output

        # Verify execution results
        exec_results = collector.get_execution_results()
        assert "10" in exec_results
        assert "20" in exec_results

    finally:
        # Graceful cleanup of streaming
        try:
            await stream_channel.unsubscribe(subscription_id)
            # Give time for unsubscription to complete
            await asyncio.sleep(0.1)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_nats_backend_properties(nats_backend):
    """Test NATS backend basic properties"""
    assert nats_backend is not None
    assert hasattr(nats_backend, 'servers')
    assert 'nats://localhost:4222' in nats_backend.servers


@pytest.mark.asyncio
async def test_notebook_session_lifecycle(notebook_toolset):
    """Test complete notebook session lifecycle"""
    # Create session
    notebook_path = f"test_lifecycle_{uuid.uuid4().hex[:8]}.ipynb"

    session_result = await notebook_toolset.create_notebook_session(notebook_path)
    assert session_result["success"]

    session_id = session_result["session_id"]

    try:
        # Verify session exists
        status = await notebook_toolset.get_notebook_status(session_id)
        assert status["success"]
        assert status["session_id"] == session_id
        assert status["notebook_path"] == notebook_path

        # Execute a simple cell
        exec_result = await notebook_toolset.execute_cell(
            session_id=session_id,
            code="test_var = 'hello world'\ntest_var",
            sync_return=True,
        )
        assert exec_result["success"]

        # Check variables
        vars_result = await notebook_toolset.get_variables(session_id)
        assert vars_result["success"]

    finally:
        # Cleanup
        shutdown_result = await notebook_toolset.shutdown_notebook_session(session_id)
        assert shutdown_result["success"]

        # Remove test file
        try:
            Path(notebook_path).unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    # Allow running directly with python
    pytest.main([__file__, "-v"])