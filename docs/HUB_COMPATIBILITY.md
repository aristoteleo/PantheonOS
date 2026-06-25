# Pantheon-Hub Compatibility Analysis for Dynamic Endpoints

## 📋 Executive Summary

✅ **pantheon-hub 无需任何修改**

新的动态端点功能 (`/api/<name>/...`) 完全在 Agent 侧实现，通过现有的 LiveView 数据服务器基础设施运行。Hub 已经支持所需的所有功能。

---

## 🔍 详细分析

### 1. 路由架构

#### Agent 侧 (LiveViewDataServer)
```
浏览器请求：
  https://ta-xxx.w.modal.host/api/track_name/chr1?threshold=0.5
             ↓
  Modal 加密隧道 (encrypted_ports=[8770])
             ↓
  Agent: LiveViewDataServer (0.0.0.0:8770)
             ↓
  aiohttp 路由分发：
    /api/{name}           → _serve_endpoint()
    /api/{name}/{tail:.*} → _serve_endpoint()
             ↓
  动态端点注册表查找 handler
             ↓
  执行用户的端点代码
```

**关键点**:
- `/api/<name>/` 路由仅在 **Agent 的 aiohttp 服务器**中注册
- Hub **永远不会收到这些请求**
- 请求通过 Modal 加密隧道直接到达 Agent

#### Hub 侧路由（FastAPI）
Hub 的 FastAPI 路由与 Agent 的 aiohttp 路由**完全独立**：

```
Hub FastAPI 路由 (pantheon-hub/pantheon_hub/api/):
  /api/chatrooms    → chatrooms.py
  /api/auth         → auth.py
  /api/users        → users.py
  /api/health       → health.py
  /api/store        → store.py
  /api/feedback     → feedback.py
  /api/quota        → quota.py
  /api/admin        → admin.py
  /api/shares       → shares.py

Agent aiohttp 路由 (新增):
  /api/{name}           → 动态端点
  /api/{name}/{tail:.*} → 动态端点
```

**结论**: ✅ 无路由冲突，因为它们在不同的服务器上。

---

### 2. Modal 加密隧道

#### 现有配置 (pantheon-hub/pantheon_hub/modal/function_manager.py)

```python
# Line 34
LIVE_VIEW_DATA_PORT = 8770

# Line 244-245 (create_pod)
env_vars["LIVE_VIEW_DATA_PORT"] = str(LIVE_VIEW_DATA_PORT)
env_vars["LIVE_VIEW_DATA_TOKEN"] = lv_data_token

# Line 266
"encrypted_ports": [LIVE_VIEW_DATA_PORT],
```

**已实现的功能**:
- ✅ 端口 8770 通过 Modal 加密隧道暴露
- ✅ 环境变量 `LIVE_VIEW_DATA_PORT` 和 `LIVE_VIEW_DATA_TOKEN` 注入
- ✅ Agent 在服务器模式下绑定 `0.0.0.0:8770`
- ✅ Token 认证已实现

#### 动态端点如何使用

```python
# Agent: data_server.py (已存在的代码)
# 启动时注册通配路由
app.router.add_route("*", "/api/{name}", self._serve_endpoint)
app.router.add_route("*", "/api/{name}/{tail:.*}", self._serve_endpoint)

# 运行时分发
async def _serve_endpoint(self, request):
    if not self._valid_api_token(request):  # 使用现有 token 验证
        return web.Response(status=403)
    name = request.match_info.get("name")
    handler = self._endpoints.get(name)
    return await handler(request)
```

**结论**: ✅ 动态端点复用现有的端口、隧道和认证机制。

---

### 3. CORS 配置

#### 现有 CORS 中间件 (PantheonOS/pantheon/toolsets/live_view/data_server.py)

```python
# Line 66-67 (已更新)
resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS"
```

**PR #120 已添加**:
- ✅ POST 方法支持
- ✅ OPTIONS 预检支持
- ✅ 通配 Origin (`*`)
- ✅ 通配 Headers (`*`)

**动态端点需求**:
- GET: 支持 ✅
- POST: 支持 ✅  
- OPTIONS: 支持 ✅

**结论**: ✅ CORS 配置已满足动态端点需求。

---

### 4. 路径验证与安全

#### 服务器模式路径结构

**静态文件** (已存在):
```
https://ta-xxx.w.modal.host/d/<token>/<prefix>/<file_path>
                           ↑
                           Token 在路径中
```

**动态端点** (新增):
```
本地模式:
  http://127.0.0.1:8770/api/<name>/?query=params

服务器模式:
  https://ta-xxx.w.modal.host/api/<name>/?token=<token>&query=params
                           ↑
                           Token 在查询参数或 Authorization header
```

#### Token 验证 (data_server.py:229-239)

```python
def _valid_api_token(self, request):
    if not self._server_mode:
        return True
    # Query param
    supplied = request.query.get("token")
    # Authorization header
    auth = request.headers.get("Authorization", "")
    if not supplied and auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    return hmac.compare_digest(str(supplied), self._token)
```

**安全特性**:
- ✅ 服务器模式强制 token 验证
- ✅ 恒定时间比较（防止时序攻击）
- ✅ 支持两种认证方式（query + header）
- ✅ 本地模式自动跳过（开发友好）

**结论**: ✅ 安全机制完整，无需 Hub 调整。

---

### 5. URL 生成

#### 现有逻辑 (data_server.py:214-227)

```python
def url_for_endpoint(self, name):
    if self._base_url is None:
        return None
    if self._server_mode:
        if not self._tunnel_base:
            logger.warning("server mode but no tunnel base delivered yet")
            return None
        return f"{self._tunnel_base}/api/{name}/?token={self._token}"
    return f"{self._base_url}/api/{name}/"
```

**工作流程**:
1. Hub 创建 sandbox 时设置 `encrypted_ports=[8770]`
2. Hub 读取隧道 URL: `sb.tunnels()[8770].host`
3. Hub 通过 `/api/chatroom` 返回 `live_view_base`
4. Frontend 调用 Agent 的 `set_data_endpoint(tunnel_base)`
5. Agent 的 `url_for_endpoint()` 使用隧道 URL

**Hub 已实现** (pantheon_hub/api/chatrooms.py):
```python
# Modal branch
live_view_base = await pod_manager.get_live_view_tunnel_url(pod_id)
return {..., "live_view_base": live_view_base}
```

**结论**: ✅ URL 生成机制已完整，动态端点自动使用正确的隧道 URL。

---

### 6. 请求流程对比

#### 静态文件 (已存在)
```
Browser → https://ta-xxx.w.modal.host/d/<token>/abc123/file.json
       → Modal Tunnel (8770)
       → Agent: _serve_token_gated()
       → 验证 token
       → FileResponse(root/file.json)
```

#### 动态端点 (新增)
```
Browser → https://ta-xxx.w.modal.host/api/track/chr1?token=xxx&threshold=0.5
       → Modal Tunnel (8770)
       → Agent: _serve_endpoint()
       → 验证 token (query param)
       → 查找 handler = _endpoints["track"]
       → handler(request)
       → web.json_response(computed_data)
```

**共同点**:
- ✅ 相同的端口 (8770)
- ✅ 相同的隧道
- ✅ 相同的 token
- ✅ 相同的 CORS 中间件

**结论**: ✅ 动态端点完全复用现有基础设施。

---

## 📊 Hub 组件检查清单

| 组件 | 状态 | 说明 |
|------|------|------|
| **Modal 隧道** | ✅ 已支持 | `encrypted_ports=[8770]` 已配置 |
| **环境变量注入** | ✅ 已支持 | `LIVE_VIEW_DATA_PORT` + `LIVE_VIEW_DATA_TOKEN` |
| **Tunnel URL 传递** | ✅ 已支持 | `get_live_view_tunnel_url()` + `/api/chatroom` |
| **Frontend 握手** | ✅ 已支持 | `set_data_endpoint()` 已实现 |
| **路由冲突** | ✅ 无冲突 | Hub 和 Agent 的 `/api/` 在不同服务器 |
| **CORS 配置** | ✅ 已支持 | POST/OPTIONS 已添加 |
| **Token 认证** | ✅ 已支持 | 服务器模式强制验证 |
| **K8s 后端** | ⚠️ 降级 | 无隧道，需要 B2 桥接（未实现，文档已说明）|

---

## 🔄 端点生命周期

### 注册流程
```
1. Agent 启动
   ↓
2. Agent 调用 serve_endpoint("track", "endpoint.py", config)
   ↓
3. 加载模块，调用 build(config)
   ↓
4. 注册 handler 到 _endpoints["track"]
   ↓
5. 返回 URL: https://ta-xxx.w.modal.host/api/track/?token=xxx
   ↓
6. Agent 在 LiveView 配置中使用此 URL
```

### 请求流程
```
1. Browser: fetch(url)
   ↓
2. Modal Tunnel → Agent:8770
   ↓
3. CORS 中间件（预检 / 主请求）
   ↓
4. _serve_endpoint(request)
   ↓
5. 验证 token
   ↓
6. 查找 handler
   ↓
7. 执行 handler(request)
   ↓
8. 返回 Response
```

### Hub 的角色
```
Hub 仅参与一次性设置：
1. 创建 Sandbox 时配置 encrypted_ports
2. 注入环境变量（端口 + token）
3. 返回隧道 URL 给 Frontend
4. Frontend 通知 Agent 隧道 URL

之后：
- Hub 不参与任何端点请求
- 所有流量直接 Browser ↔ Agent
```

---

## ⚠️ 边缘情况

### 1. K8s 后端（非 Modal）

**当前状态**: `BasePodManager.get_live_view_tunnel_url()` 返回 `None`

**影响**:
- K8s 部署没有 Modal 隧道
- Agent 的 `url_for_endpoint()` 会记录警告
- 动态端点在 K8s 上**不可用**

**文档说明** (docs/2026-06-10-live-view-server-mode.md:36-37):
> B2 (hub HTTP↔NATS bridge): fallback for non-Modal (K8s-pod) deployments.
> **Not yet implemented.**

**结论**: ⚠️ 已知限制，不是 bug。K8s 支持需要实现 B2 桥接（未来工作）。

### 2. Hub 重启后的隧道恢复

**实现** (pantheon_hub/modal/function_manager.py:get_live_view_tunnel_url):
```python
# 从 sandbox handle 实时读取隧道，而非缓存
tunnels = await sb.tunnels.aio()
tunnel = tunnels.get(LIVE_VIEW_DATA_PORT)
return f"https://{tunnel.host}" if tunnel else None
```

**好处**:
- ✅ Hub 重启后自动恢复
- ✅ 支持 adopted sandboxes
- ✅ 隧道与 sandbox 生命周期绑定

**结论**: ✅ 已处理。

### 3. 旧 Sandbox（没有 encrypted_ports）

**场景**: 在部署新版 Hub 前创建的 sandbox

**行为**:
- `get_live_view_tunnel_url()` 返回 `None`
- Agent 的 `url_for_endpoint()` 返回 `None`
- `serve_endpoint()` 失败并返回错误

**缓解**:
- 用户重新连接会创建新 sandbox（有隧道）
- 或者显式重启 chatroom

**结论**: ✅ 优雅降级，不会崩溃。

---

## 🧪 测试建议

虽然 Hub 无需修改，但建议进行以下端到端测试：

### 测试 1: 基本端到端流程
```python
# 1. 创建 Modal sandbox（现有流程）
# 2. Agent 注册端点
result = await serve_endpoint("test", "test_endpoint.py")
assert result["success"]
assert "ta-" in result["url"]  # Modal tunnel
assert "token=" in result["url"]

# 3. Browser 请求
response = await fetch(result["url"])
assert response.status == 200
```

### 测试 2: CORS 预检
```python
# OPTIONS 请求应该成功
response = await fetch(endpoint_url, {method: "OPTIONS"})
assert response.status == 200
assert "POST" in response.headers["Access-Control-Allow-Methods"]
```

### 测试 3: Token 认证
```python
# 无 token 应该被拒绝
response = await fetch(url_without_token)
assert response.status == 403

# 正确 token 应该成功
response = await fetch(url_with_token)
assert response.status == 200
```

### 测试 4: POST with JSON
```python
response = await fetch(endpoint_url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({region: "chr1", threshold: 0.5})
})
assert response.status == 200
```

---

## 📝 需要的文档更新

### Hub 文档 (pantheon-hub/docs/)

**无需更新**，因为：
1. `docs/2026-06-10-live-view-server-mode.md` 已完整描述架构
2. 动态端点使用相同的隧道机制
3. Hub 的角色（设置隧道）保持不变

**可选增强**:
在 `docs/2026-06-10-live-view-server-mode.md` 添加一节：

```markdown
## Dynamic Endpoints (2026-06-25)

The LiveView data server now supports dynamic Python endpoints alongside
static files. Both use the same tunnel (port 8770) and token. The Hub's
role is identical: expose encrypted_ports, inject env vars, and return
the tunnel URL. All endpoint routing happens in the Agent.

Example URL: https://ta-xxx.w.modal.host/api/track_name/?token=xxx

See PantheonOS/docs/DESIGN.md for dynamic endpoint architecture.
```

---

## ✅ 最终结论

### 需要修改？
**❌ 否，pantheon-hub 无需任何代码修改。**

### 原因
1. ✅ Modal 隧道机制已实现并工作正常
2. ✅ Token 认证已配置
3. ✅ CORS 已支持 POST/OPTIONS
4. ✅ 路由在 Agent 侧，Hub 不参与
5. ✅ 所有基础设施完全复用

### 建议操作
1. ✅ 部署新的 Agent 镜像（包含动态端点功能）
2. ✅ 运行端到端测试验证
3. 📝 可选：在 Hub 文档中添加一节说明动态端点（信息性，非必需）

### 风险评估
**🟢 低风险**
- 动态端点是 Agent 侧的纯增量功能
- 复用所有现有基础设施
- 不影响静态文件服务
- 优雅降级（K8s 后端返回错误而非崩溃）

---

## 📚 参考文档

- **Hub 侧**: `pantheon-hub/docs/2026-06-10-live-view-server-mode.md`
- **Agent 侧**: `PantheonOS/docs/DESIGN.md`
- **代码**: 
  - Hub: `pantheon_hub/modal/function_manager.py`
  - Agent: `pantheon/toolsets/live_view/data_server.py`
  - Agent: `pantheon/toolsets/live_view/toolset.py`
