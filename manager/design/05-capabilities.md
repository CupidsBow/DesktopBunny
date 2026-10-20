# 05 · 能力、工具与权限

> 能力是 Alice 的"手脚"。LLM 决定调用什么；机制负责执行、限权、超时、取消与审计。
> 所有工具统一注册在 `ToolExecutor`，但权限与异步执行由外层的 capability 层负责。

---

## 1. 分层

```
Decision (tool.call)
        │
        ▼
┌───────────────────────┐
│ capability 层          │  权限检查 · 参数校验 · 异步派发 · 超时 · 审计
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ ToolExecutor           │  工具注册表 · schema · 纯函数执行
└───────────┬───────────┘
            ▼
    web / fs / pc / memory / body / time
```

- **ToolExecutor** 保持"注册 + 分发"的简单职责。
- **capability 层**包裹在它外面，统一处理权限、异步、超时、取消、审计。
- 工具函数本身保持纯粹，不各自实现权限逻辑。

---

## 2. 能力注册

每个能力声明：

```python
@dataclass
class Capability:
    name: str                  # web.fetch
    description: str
    schema: dict               # JSON Schema
    handler: Callable
    permission: str            # fs.read / network.read / body / ...
    async_mode: bool           # 是否需要后台执行
    timeout_seconds: float
    audit: bool = True
```

注册示例：

```python
capabilities.register(
    name="web.fetch",
    description="读取一个网页的正文",
    schema={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    handler=fetch_page,
    permission="network.read",
    async_mode=True,
    timeout_seconds=30,
)
```

---

## 3. 能力清单

| 能力 | 作用 | 权限 | 异步 |
| --- | --- | --- | --- |
| `web.search` | 搜索网页 | `network.read` | 是 |
| `web.fetch` | 读取网页正文 | `network.read` | 是 |
| `fs.list` | 列出目录 | `fs.read` | 否/是 |
| `fs.read` | 读取文本文件 | `fs.read` | 是 |
| `fs.search` | 在允许目录内检索 | `fs.read` | 是 |
| `fs.write` | 写入 Alice 自己的数据目录 | `fs.write` | 否 |
| `pc.observe` | 进程、前台应用、资源占用 | `pc.observe` | 否 |
| `memory.recall` | 召回相关记忆 | `memory.read` | 否 |
| `memory.read` | 读取语义记忆文件 | `memory.read` | 否 |
| `memory.write` | 更新语义记忆 | `memory.write` | 否 |
| `time.now` | 当前时间 | 无 | 否 |

> `chat.send` 与 `body.do` 不作为 LLM 能力暴露：前者由 [06-effector](06-effector.md) 根据 `chat.reply` / `chat.reach_out` 调用，后者由 Decision 的 `body` 字段与 `body.do` 动作直接执行。

### 3.1 参数示例

```json
{"capability": "web.search", "args": {"query": "小模型 综述", "limit": 5}}
{"capability": "fs.read", "args": {"path": "C:/Users/.../project/README.md"}}
{"capability": "pc.observe", "args": {"what": ["foreground", "cpu"]}}
{"capability": "memory.recall", "args": {"query": "上次推荐的文章"}}
```

---

## 4. 执行模型

### 4.1 同步与异步

- **快能力**（时间、身体、记忆读、`chat.send`）在当前帧内完成。
- **慢能力**（网页、文件扫描、检索）由 capability 层派发到后台线程，帧立即结束。
- 后台任务完成/失败后，以事件回到收件箱：

```json
{
  "kind": "capability_done",
  "payload": {
    "call_id": "call_7",
    "capability": "web.fetch",
    "result_summary": "……摘要……",
    "result_ref": "artifact://web/7"
  }
}
```

- 大结果不直接塞进情境，只给摘要 + 引用；LLM 可以再次读取。

### 4.2 超时与取消

- 每个能力有 `timeout_seconds`；超时返回失败事件。
- 朋友明确说"停"时，机制可以取消在途任务。
- 取消是机制行为，不改变 LLM 的决策权。

### 4.3 重试

- 网络类失败可以由机制做有限重试（例如 1 次）。
- 重试策略是约束，不是行为决策；结果如实返回。
- 不自动重试有副作用的写入操作。

---

## 5. 权限模型

### 5.1 文件系统

| 规则 | 说明 |
| --- | --- |
| 读白名单 | `FS_ALLOWED_ROOTS` 内允许读 |
| 写范围 | 默认只写 Alice 自己的数据目录 |
| 敏感拒绝 | 密钥、密码库、浏览器配置、SSH、token 文件等 |
| 路径规范化 | 解析 `..`、符号链接后再校验，防越界 |
| 大小限制 | 单文件读取上限，超限拒绝 |

### 5.2 网络

| 规则 | 说明 |
| --- | --- |
| 只读 | 允许搜索与抓取，不允许登录、发帖、购买 |
| 域名策略 | 默认允许公开网页；可配置黑名单 |
| 凭据 | 不读取、不携带用户的 Cookie/Token |
| 下载 | 不自动执行下载内容 |

### 5.3 执行

- 默认不运行任意代码。
- 如启用 `sandbox.run`：隔离目录、无网络或受限网络、超时、资源限制、审计。
- 不自我复制、不提权、不修改系统配置。

### 5.4 隐私

- 不读取剪贴板、键盘输入、密码框。
- 不把敏感原文写入记忆。
- 审计日志只记录参数摘要，不记录完整敏感内容。

---

## 6. 拒绝处理

权限拒绝返回结构化错误：

```json
{
  "ok": false,
  "error": "permission_denied",
  "reason": "path is outside allowed roots",
  "path": "..."
}
```

- 拒绝原因进入下一帧情境。
- LLM 自己决定换路、询问朋友、还是放弃。
- 机制不把拒绝解释成"不要做这件事"，只陈述事实。

---

## 7. 审计

每次能力调用记录：

| 字段 | 内容 |
| --- | --- |
| `time` | 调用时间 |
| `capability` | 能力名 |
| `args_summary` | 参数摘要（敏感值脱敏） |
| `permission` | 使用的权限 |
| `result` | 成功/失败/拒绝 |
| `duration_ms` | 耗时 |
| `caller` | 触发的帧/活动 id |

审计日志可被用户查看；Alice 也可以在对话里主动说明自己在做什么。

---

## 8. 扩展新工具

1. 在 `tools/` 写纯函数，输入输出可序列化。
2. 在 capability 层注册：名称、描述、schema、权限、是否异步、超时。
3. 加入 `ToolExecutor` 的分发表。
4. 补测试：正常路径、权限拒绝、超时、参数非法、大结果。
5. 若涉及新的敏感范围，先补权限规则与审计字段。

工具描述会进入情境；描述要短、准确、说明副作用。

---

## 9. 能力与记忆

- 能力结果默认不写记忆；LLM 通过 `remember` 或 `memory.write` 决定保留什么。
- 大结果以 artifact 引用保存，避免上下文膨胀。
- 活动结束时可把 artifact 摘要沉淀进 L2。

---

## 10. 失败与降级

| 情况 | 处理 |
| --- | --- |
| 网络不可用 | 返回失败；LLM 换路或稍后重试 |
| 文件不存在 | 返回明确错误 |
| 权限拒绝 | 返回原因；不重试 |
| 超时 | 取消任务；返回超时事件 |
| 结果过大 | 截断并给引用 |
| 工具抛异常 | 捕获并返回结构化错误，不崩溃主进程 |

---

## 11. 可调参数

| 参数 | 含义 |
| --- | --- |
| `FS_ALLOWED_ROOTS` | 允许读取的根目录 |
| `FS_DENIED_PATTERNS` | 敏感路径拒绝规则 |
| `NETWORK_POLICY` | 只读 / 域名策略 / 禁止登录 |
| `CAPABILITY_TIMEOUT_DEFAULT` | 默认超时 |
| `CAPABILITY_MAX_RESULT_BYTES` | 单次结果上限 |
| `SANDBOX_ENABLED` | 是否启用受限执行 |
| `AUDIT_LOG_PATH` | 审计日志路径 |
