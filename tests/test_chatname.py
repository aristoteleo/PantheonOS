#!/usr/bin/env python3
"""
Chat Name Generator Tests - Real Agent
Tests using real agent with .env configuration

## 运行方法
```bash
# 确保项目根目录有 .env 文件包含 OPENAI_API_KEY
python -m pytest tests/test_chatname.py -v -s
```
"""

import os
import sys
import uuid
from pathlib import Path

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env')
except ImportError:
    # 如果没有python-dotenv，尝试手动加载.env
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if '=' in line and not line.strip().startswith('#'):
                    key, value = line.strip().split('=', 1)
                    os.environ[key] = value

from pantheon.internal.memory import Memory
from pantheon.chatroom.special_agents import ChatNameGenerator, SuggestionGenerator


@pytest.mark.asyncio
async def test_generate_name_first_conversation():
    """测试首次对话后生成名称 - 使用真实agent"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not found in environment")

    generator = ChatNameGenerator()
    memory = Memory("Test Chat")
    memory.id = str(uuid.uuid4())

    # 添加真实的首次对话
    memory.add_messages([
        {"role": "user", "content": "I need help debugging a Python script that processes CSV files and generates reports"},
        {"role": "assistant", "content": "I'd be happy to help you debug your Python CSV processing script!"}
    ])

    # 使用真实的agent生成名称
    result = await generator.generate_or_update_name(memory)

    # 验证结果
    assert isinstance(result, str)
    assert len(result) > 3
    assert len(result) < 100
    assert memory.extra_data["name_generated"] is True
    assert memory.extra_data["last_name_generation_message_count"] == 2

    # 检查生成的名称是否相关
    result_lower = result.lower()
    relevant_keywords = ["python", "csv", "debug", "script", "report", "process", "file"]
    assert any(keyword in result_lower for keyword in relevant_keywords), f"Generated name '{result}' should contain relevant keywords"

    print(f"✅ Generated name: '{result}'")


@pytest.mark.asyncio
async def test_update_name_after_threshold():
    """测试达到阈值后更新名称 - 使用真实agent"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not found in environment")

    generator = ChatNameGenerator()
    memory = Memory("Old Chat Name")
    memory.id = str(uuid.uuid4())

    # 模拟之前已生成过名称
    memory.extra_data["name_generated"] = True
    memory.extra_data["last_name_generation_message_count"] = 2

    # 先添加初始的2条消息（模拟之前的对话）
    initial_messages = [
        {"role": "user", "content": "I need help with Python script"},
        {"role": "assistant", "content": "Sure, I can help!"}
    ]
    memory.add_messages(initial_messages)

    # 再添加6条新消息达到更新阈值（总共8条消息）
    new_messages = [
        {"role": "user", "content": "Now I want to add machine learning features to predict data trends"},
        {"role": "assistant", "content": "Great! We can use scikit-learn to add predictive analytics"},
        {"role": "user", "content": "Which ML algorithms would work best for time series forecasting?"},
        {"role": "assistant", "content": "For time series, consider ARIMA, LSTM, or Prophet models"},
        {"role": "user", "content": "Can you help me implement a simple LSTM model?"},
        {"role": "assistant", "content": "Absolutely! Let's start with TensorFlow and Keras for the LSTM"}
    ]
    memory.add_messages(new_messages)

    # 使用真实的agent更新名称
    result = await generator.generate_or_update_name(memory)

    # 验证结果
    assert isinstance(result, str)
    assert len(result) > 3
    assert len(result) < 100
    assert memory.extra_data["last_name_generation_message_count"] == 8

    # 检查更新的名称是否反映新的主题
    result_lower = result.lower()
    ml_keywords = ["machine", "learning", "ml", "lstm", "model", "predict", "forecast", "time", "series"]
    assert any(keyword in result_lower for keyword in ml_keywords), f"Updated name '{result}' should reflect ML topic"

    print(f"✅ Updated name: '{result}'")


@pytest.mark.asyncio
async def test_no_generation_insufficient_messages():
    """测试消息不足时不生成名称"""
    generator = ChatNameGenerator()
    memory = Memory("Original Name")
    memory.id = str(uuid.uuid4())

    # 只添加一条消息
    memory.add_messages([
        {"role": "user", "content": "Hello"}
    ])

    # 不应该生成名称
    result = await generator.generate_or_update_name(memory)

    assert result == "Original Name"
    assert "name_generated" not in memory.extra_data

    print("✅ No generation with insufficient messages")


@pytest.mark.asyncio
async def test_chat_name_generator_uses_preferred_model(monkeypatch):
    generator = ChatNameGenerator()
    memory = Memory("Original Name")
    memory.id = str(uuid.uuid4())
    memory.add_messages([
        {"role": "user", "content": "Please help analyze this single-cell dataset"},
        {"role": "assistant", "content": "Sure, let's inspect the dataset."},
    ])

    async def fake_run(self, prompt, *args, **kwargs):
        return type("Resp", (), {"content": "🧬Single-cell Analysis"})()

    monkeypatch.setattr("pantheon.chatroom.special_agents.Agent.run", fake_run)

    result = await generator.generate_or_update_name(
        memory,
        preferred_model="gemini/gemini-3-flash-preview",
    )

    assert result == "🧬Single-cell Analysis"
    assert generator._name_agent is not None
    assert generator._name_agent.models == ["gemini/gemini-3-flash-preview"]


@pytest.mark.asyncio
async def test_suggestion_generator_uses_preferred_model(monkeypatch):
    generator = SuggestionGenerator()

    async def fake_run(self, prompt, *args, **kwargs):
        return type("Resp", (), {"content": "What data should we inspect next?\nDo you want marker genes?\nShould we compare clusters?"})()

    monkeypatch.setattr("pantheon.chatroom.special_agents.Agent.run", fake_run)

    suggestions = await generator.generate_suggestions(
        [
            {"role": "user", "content": "Analyze this PBMC dataset"},
            {"role": "assistant", "content": "I found several immune cell clusters."},
        ],
        preferred_model="gemini/gemini-3-flash-preview",
    )

    assert len(suggestions) == 3
    assert generator._suggestion_agent is not None
    assert generator._suggestion_agent.models == ["gemini/gemini-3-flash-preview"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
