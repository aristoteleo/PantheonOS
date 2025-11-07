# LocalProvider - 本地 ToolSet 提供者

## 概述

`LocalProvider` 是 Pantheon Agents 框架中新增的 ToolProvider 实现,用于在内存中直接调用 ToolSet 实例,无需通过远程连接。

## 特点

- **零延迟**: 直接在内存中调用工具,无网络开销
- **简单易用**: 只需传入 ToolSet 实例即可
- **会话隔离**: 支持 context_variables 和会话管理
- **参数过滤**: 自动过滤不需要的参数
- **缓存优化**: 工具列表和描述会被缓存

## 与其他 Provider 的对比

| Provider | 使用场景 | 连接方式 | 延迟 |
|----------|---------|---------|------|
| **LocalProvider** | 本地工具集,单进程场景 | 内存直调 | 最低 |
| **ToolSetProvider** | 远程工具集,分布式场景 | 通过 ToolsetProxy (NATS) | 中等 |
| **MCPProvider** | MCP 协议服务器 | HTTP/SSE | 中等 |

## 使用方法

### 方法一: 自动包装 (推荐)

**最简单的方式** - 直接传入 ToolSet 实例,Agent 会自动将其包装为 LocalProvider:

```python
from pantheon.agent import Agent
from pantheon.toolset import ToolSet, tool


# 1. 定义 ToolSet
class MyToolSet(ToolSet):
    def __init__(self):
        super().__init__(name="my_tools")

    @tool
    async def my_tool(self, arg: str) -> str:
        """工具描述"""
        return f"Result: {arg}"


# 2. 创建 ToolSet 实例
toolset = MyToolSet()

# 3. 直接添加到 Agent (会自动包装为 LocalProvider)
agent = Agent(
    name="my_agent",
    instructions="You are a helpful assistant.",
    model="gpt-4o-mini"
)

await agent.toolset(toolset)  # 自动包装! ✨

# 4. 使用
result = await agent.run("Use my_tool with arg='test'")
```

### 方法二: 显式创建 LocalProvider

如果需要更多控制,可以手动创建 LocalProvider:

```python
from pantheon.agent import Agent
from pantheon.providers import LocalProvider
from pantheon.toolset import ToolSet, tool


# 1. 定义 ToolSet
class MyToolSet(ToolSet):
    def __init__(self):
        super().__init__(name="my_tools")

    @tool
    async def my_tool(self, arg: str) -> str:
        """工具描述"""
        return f"Result: {arg}"


# 2. 创建 ToolSet 实例
toolset = MyToolSet()

# 3. 显式创建 LocalProvider
provider = LocalProvider(toolset)
await provider.initialize()  # 可选的预初始化

# 4. 添加到 Agent
agent = Agent(
    name="my_agent",
    instructions="You are a helpful assistant.",
    model="gpt-4o-mini"
)

await agent.toolset(provider)

# 5. 使用
result = await agent.run("Use my_tool with arg='test'")
```

### 完整示例

参考 [examples/local_provider_example.py](../examples/local_provider_example.py) 查看完整示例。

## API 文档

### LocalProvider

```python
class LocalProvider(ToolProvider):
    def __init__(self, toolset: ToolSet)
```

**参数**:
- `toolset`: ToolSet 实例,直接在内存中调用

**方法**:
- `async initialize()`: 初始化 provider,缓存工具描述
- `async list_tools() -> list[ToolInfo]`: 列举可用工具
- `async call_tool(name: str, args: dict) -> Any`: 调用工具
- `async shutdown()`: 清理资源(对于本地 provider 无操作)

**属性**:
- `toolset_name`: 返回工具集名称

## 实现细节

### 自动包装机制 (Agent.toolset)

当传入 ToolSet 实例到 `agent.toolset()` 时:

1. 检测到是 ToolSet 类型
2. 自动创建 `LocalProvider(toolset)`
3. 调用 `provider.initialize()` 初始化
4. 将 provider 加入 `agent.providers` 字典
5. 工具通过 provider 动态路由调用

```python
# agent.py 的实现逻辑
if isinstance(toolset, ToolSet):
    provider = LocalProvider(toolset)
    await provider.initialize()
    self.providers[provider.toolset_name] = provider
```

### 初始化流程 (LocalProvider.initialize)

1. 接收 ToolSet 实例
2. 调用 `toolset.run_setup()` 确保初始化完成
3. 调用 `toolset.list_tools()` 获取工具列表
4. 缓存工具描述用于参数过滤

### 工具调用流程

1. 从缓存中获取工具的参数定义
2. 过滤掉不需要的参数(只保留工具声明的参数和特殊参数)
3. 通过 `getattr(toolset, tool_name)` 获取工具方法
4. 直接调用工具方法 (内存调用)
5. 返回结果

### 参数过滤

LocalProvider 会过滤参数,只传递工具期望的参数:

```python
# 工具声明需要: message
# Agent 可能传递: message, extra_param, context_variables

# LocalProvider 会过滤为: message, context_variables
# (context_variables 在 _SKIP_PARAMS 中,会保留)
```

## 何时使用 LocalProvider

**适用场景**:
- ✅ 单机部署,不需要分布式
- ✅ 需要最低延迟
- ✅ 工具逻辑简单,不需要隔离进程
- ✅ 开发和测试环境

**不适用场景**:
- ❌ 需要多个 Agent 共享同一个工具集实例
- ❌ 需要分布式部署
- ❌ 工具需要运行在独立进程/机器上
- ❌ 需要工具的远程管理和监控

对于这些场景,请使用 `ToolSetProvider` + `ToolsetProxy`。

## 测试

运行 LocalProvider 测试:

```bash
pytest tests/test_local_provider.py -v
```

## 相关文件

- 实现: [pantheon/providers.py](../pantheon/providers.py)
- 测试: [tests/test_local_provider.py](../tests/test_local_provider.py)
- 示例: [examples/local_provider_example.py](../examples/local_provider_example.py)
