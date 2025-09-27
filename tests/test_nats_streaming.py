#!/usr/bin/env python3
"""
NATS流式传输完整测试
测试NATS backend的streaming功能与IntegratedNotebook的集成
"""

import asyncio
import time
import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

# 设置环境变量确保使用NATS后端
os.environ["PANTHEON_REMOTE_BACKEND"] = "nats"
os.environ["NATS_SERVERS"] = "nats://localhost:4222"

from pantheon.remote import (
    RemoteBackendFactory,
    RemoteConfig,
    StreamMessage,
    StreamType,
)
from pantheon.toolsets.integrated_notebook import IntegratedNotebookToolSet


async def test_nats_streaming_complete():
    """完整的NATS流式传输测试"""
    print("🧪 开始NATS流式传输完整测试")
    print("=" * 50)

    # 1. 初始化NATS后端
    print("📡 初始化NATS后端...")
    config = RemoteConfig.from_config()
    backend = RemoteBackendFactory.create_backend(config)
    print(f"✅ 后端类型: {type(backend).__name__}")
    print(f"🌐 服务器: {backend.servers}")

    # 2. 创建IntegratedNotebook工具集
    print("\n📓 创建IntegratedNotebook工具集...")
    notebook = IntegratedNotebookToolSet(
        name="test-notebook", remote_backend=backend, workdir="."
    )
    await notebook.run_setup()
    print("✅ IntegratedNotebook工具集已就绪")

    # 3. 创建notebook会话
    print("\n🚀 创建notebook会话...")
    # 创建一个测试notebook和会话
    import uuid

    notebook_path = f"test_streaming_{uuid.uuid4().hex[:8]}.ipynb"

    try:
        session_result = await notebook.create_notebook_session(notebook_path)
        if not session_result["success"]:
            print(f"❌ 创建会话失败: {session_result['error']}")
            return

        session_id = session_result["session_id"]
        print(f"✅ 会话创建成功: {session_id[:12]}...")
    except Exception as e:
        print(f"❌ 创建会话异常: {e}")
        return

    # 4. 设置流式监听
    print("\n🌊 设置流式监听...")
    stream_id = f"notebook_iopub_{session_id}"
    stream_channel = await backend.get_or_create_stream(stream_id, StreamType.NOTEBOOK)

    # 消息收集器
    received_messages = []
    start_time = time.time()

    async def stream_callback(message: StreamMessage):
        """流式消息回调"""
        elapsed = time.time() - start_time
        received_messages.append((elapsed, message))

        # 解析Jupyter消息
        msg_type = message.data.get("msg_type", "unknown")

        # 不跳过解析错误的消息，需要调试
        if msg_type == "parse_error":
            print(f"🚨 PARSE_ERROR [{elapsed:.2f}s]: {message.data}")
            return

        content = message.data.get("content", {})

        if msg_type == "stream":
            text = content.get("text", "").strip()
            if text:
                print(f'🌊 STREAM [{elapsed:.2f}s]: "{text}"')
        elif msg_type == "execute_result":
            result = content.get("data", {}).get("text/plain", "")
            print(f"📊 RESULT [{elapsed:.2f}s]: {result}")
        elif msg_type == "error":
            error = content.get("evalue", "")
            print(f"❌ ERROR [{elapsed:.2f}s]: {error}")
        elif msg_type == "status":
            state = content.get("execution_state", "")
            print(f"🔄 STATUS [{elapsed:.2f}s]: {state}")
        else:
            print(f"🔍 OTHER [{elapsed:.2f}s]: {msg_type}")

    # 订阅流消息
    subscription_id = await stream_channel.subscribe(stream_callback)
    print(f"📡 已订阅流: {stream_id} -> {subscription_id}")

    # 5. 执行测试代码 - 带有流式输出
    print("\n🔥 执行流式测试代码...")
    test_code = """
import time

print("🚀 开始NATS流式测试...")

for i in range(3):
    time.sleep(0.5)
    progress = f"📊 Progress: {i+1}/3"
    print(progress)

time.sleep(0.3)
print("✅ 测试完成!")

# 最终结果
result = 2**8
result
"""

    # 重置计时器
    start_time = time.time()

    # 异步执行代码（这会触发流式输出）
    exec_result = await notebook.add_and_execute_cell(
        session_id=session_id,
        code=test_code,
        sync_return=True,  # 同步返回，获取完整结果
    )

    exec_status = "✅ 成功" if exec_result["success"] else "❌ 失败"
    print(f"\n⏱️  执行完成: {exec_status} {exec_result}")

    # 6. 等待所有流式消息
    print("\n⏳ 等待流式消息处理...")
    await asyncio.sleep(3)

    # 7. 分析结果
    print("\n📊 流式消息统计:")
    print(f"   总消息数: {len(received_messages)}")

    # 按类型统计
    msg_types = {}
    stream_count = 0
    for _, msg in received_messages:
        msg_type = msg.data.get("msg_type", "unknown")
        msg_types[msg_type] = msg_types.get(msg_type, 0) + 1
        if msg_type == "stream":
            stream_count += 1

    print(f"   消息类型: {msg_types}")
    print(f"   流式输出: {stream_count} 条")

    # 8. 验证同步执行结果
    print("\n🔍 同步执行结果验证:")
    outputs = []
    if exec_result["success"]:
        outputs = exec_result.get("outputs", [])
        print(f"   输出数量: {len(outputs)}")

        for i, output in enumerate(outputs):
            output_type = output.get("output_type", "unknown")
            if output_type == "stream":
                text = output.get("text", "").strip()
                print(f'   输出{i + 1}: {output_type} -> "{text}"')
            elif output_type == "execute_result":
                result = output.get("data", {}).get("text/plain", "")
                print(f"   输出{i + 1}: {output_type} -> {result}")

    # 9. 清理
    print("\n🧹 清理资源...")
    await stream_channel.unsubscribe(subscription_id)
    await notebook.shutdown_notebook_session(session_id)

    print("✅ NATS流式传输测试完成!")
    print(f"🎯 测试结果: 流式消息={len(received_messages)}, 同步输出={len(outputs)}")

    return len(received_messages) > 0 and exec_result["success"]


async def main():
    """主测试函数"""
    try:
        success = await test_nats_streaming_complete()
        if success:
            print("\n🎉 所有测试通过!")
        else:
            print("\n❌ 测试失败!")
    except Exception as e:
        print(f"\n💥 测试异常: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
