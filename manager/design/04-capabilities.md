# 04 · 能力与工具

> 能力是 Alice 的"手脚"。LLM 决定调用什么；机制负责执行、超时、取消与审计。
> 所有工具统一注册在 `ToolExecutor`，异步执行由外层的 capability 层负责。

---

## 1. 分层

```
Decision (tool.call)
        │
        ▼
┌───────────────────────┐
│ capability 层          │  参数校验 · 异步派发 · 超时 · 审计
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ ToolExecutor           │  工具注册表 · schema · 纯函数执行
└───────────┬───────────┘
            ▼
    web / fs / pc / memory / body / time
```

- **ToolExecutor** 保持"注册 + 分发"的简单职责。
- **capability 层**包裹在它外面，统一处理异步、超时、取消、审计。
- 工具函数本身保持纯粹，不各自实现调度逻辑。

实现文件：

| 层 | 文件 |
| --- | --- |
| capability 注册 / schema | `manager/capabilities/registry.py` |
| 异步、超时、结果截断 | `manager/capabilities/executor.py` |
| 审计 | `manager/capabilities/audit.py` |
| artifact 存取 | `manager/capabilities/artifacts.py` |
| 记忆能力适配 | `manager/capabilities/memory_api.py` |
| 工具注册表 | `tools/tool_executor.py` |
| 工具纯函数 | `tools/web_tools.py` / `fs_tools.py` / `sandbox_tools.py` / `pc_tools.py` / `get_local_time.py` |

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
    async_mode=True,
    timeout_seconds=30,
)
```

---

## 3. 能力清单

| 能力 | 作用 | 异步 |
| --- | --- | --- |
| `web.search` | 搜索网页 | 是 |
| `web.fetch` | 读取网页正文 | 是 |
| `fs.list` | 列出目录 | 否/是 |
| `fs.read` | 读取文本文件 | 是 |
| `fs.search` | 检索文件 | 是 |
| `fs.write` | 写入文件 | 否 |
| `artifact.read` | 读取大结果的 artifact 引用 | 否 |
| `sandbox.run` | 运行代码与命令 | 是 |
| `pc.observe` | 进程、前台应用、资源占用 | 否 |
| `memory.recall` | 召回相关记忆 | 否 |
| `memory.read` | 读取语义记忆文件 | 否 |
| `memory.write` | 更新语义记忆 | 否 |
| `time.now` | 当前时间 | 否 |
| `eat` | 让桌宠吃东西（需注入 `eat_handler`） | 否 |

> `chat.send` 不作为 LLM 能力暴露：它由 [10-executor](10-executor.md) 根据 `chat.reply` / `chat.reach_out` 调用。

### 3.1 参数示例

```json
{"capability": "web.search", "args": {"query": "小模型 综述", "limit": 5}}
{"capability": "fs.read", "args": {"path": "C:/Users/.../project/README.md"}}
{"capability": "pc.observe", "args": {"what": ["foreground", "cpu"]}}
{"capability": "memory.recall", "args": {"query": "上次推荐的文章"}}
{"capability": "artifact.read", "args": {"ref": "artifact://web/7", "offset": 0, "limit": 4000}}
```

参数约定：

| 能力 | 参数 |
| --- | --- |
| `pc.observe` | `what`: `foreground` / `presence` / `processes` / `cpu` / `memory` / `network` 的子集 |
| `sandbox.run` | `command` 或 `code`（二选一）、`cwd`、`env`、`timeout` |
| `fs.search` | `path`、`pattern`（glob 或正则）、`limit` |
| `fs.write` | `path`、`content`、`append`（默认 `false`） |

---

## 4. 执行模型

### 4.1 同步与异步

- **快能力**（时间、记忆读、`artifact.read`、`fs.write`）在当前帧内完成。
- **慢能力**（网页、文件扫描、检索、`sandbox.run`）由 capability 层派发到后台线程，帧立即结束。
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

- 大结果不直接塞进情境，只给摘要 + 引用；LLM 用 `artifact.read(ref, offset, limit)` 再次读取。

### 4.2 超时与取消

- 每个能力有 `timeout_seconds`；超时返回失败事件。
- 取消发生在超时、关闭或用户急停时，不响应对话内容。
- 取消是机制行为，不改变 LLM 的决策权。

### 4.3 重试

- 网络类失败可以由机制做有限重试（例如 1 次）。
- 重试策略是约束，不是行为决策；结果如实返回。
- 不自动重试有副作用的写入操作。

---

## 5. 系统访问

不设权限层。Alice 可以使用整台电脑：读写文件、运行代码、访问网络、调用系统接口，都不做白名单、不做敏感路径拒绝、不做危险操作拦截。

| 范围 | 规则 |
| --- | --- |
| 文件 | 全盘读写，无白名单、无敏感路径排除 |
| 执行 | 可以运行任意代码；`sandbox.run` 只是可选工具，不是权限门槛 |
| 网络 | 搜索、抓取、下载、登录都可以，不设域名或方法限制 |
| 系统 | 不因"危险操作"而拒绝；调用失败按普通错误返回 |

- 唯一的边界是**记忆内容**：凭据、密码、他人隐私的原文不写进 L1/L2/L3（见 [03-memory §8.3](03-memory.md)）。这是写记忆的规则，不是访问限制。
- 所有调用照常进审计日志；错误只来自参数非法、超时或工具自身失败。

---

## 6. 失败处理

成功返回：

```json
{
  "ok": true,
  "result": "……小结果直接返回……",
  "result_ref": "artifact://web/7",
  "truncated": false
}
```

`result` 超过 `capability_max_result_bytes` 时写入 artifact，只保留摘要与 `result_ref`。

执行失败返回结构化错误：

```json
{
  "ok": false,
  "error": "invalid_args | timeout | capability_error",
  "reason": "……",
  "detail": {}
}
```

- 错误原因进入下一帧情境。
- LLM 自己决定换路、询问朋友、还是放弃。
- 机制不把失败解释成"不要做这件事"，只陈述事实。

---

## 7. 审计

每次能力调用记录：

| 字段 | 内容 |
| --- | --- |
| `time` | 调用时间 |
| `capability` | 能力名 |
| `args_summary` | 参数摘要（敏感值脱敏） |
| `result` | 成功/失败 |
| `duration_ms` | 耗时 |
| `caller` | 触发的帧/活动 id |

审计日志可被用户查看；Alice 也可以在对话里主动说明自己在做什么。

---

## 8. 扩展新工具

1. 在 `tools/` 写纯函数，输入输出可序列化（例如 `tools/web_tools.py`）。
2. 在 `manager/capabilities/registry.py` 注册：名称、描述、schema、是否异步、超时。
3. 如涉及记忆，加到 `manager/capabilities/memory_api.py`，不要在 `tools/` 里导入记忆模块。
4. 补测试：正常路径、超时、参数非法、大结果。
5. 若涉及新的系统资源，补审计字段。

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
| 超时 | 取消任务；返回超时事件 |
| 结果过大 | 截断并给引用 |
| 工具抛异常 | 捕获并返回结构化错误，不崩溃主进程 |

---

## 11. 可调参数

| 参数 | 含义 |
| --- | --- |
| `fs_max_read_bytes` | 单文件读取上限（超出返回引用） |
| `capability_timeout_default` | 默认超时 |
| `capability_max_result_bytes` | 单次结果上限 |
| `audit_path` | 审计日志路径 |
