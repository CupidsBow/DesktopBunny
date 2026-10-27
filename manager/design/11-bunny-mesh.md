# 11 · 兔兔之间：本地兔子也是「朋友」

> 多只兔兔（不同 `BUNNY_NAME` 的多个进程）住在同一台 Windows 电脑里。
> 本模块把「本地兔子」接入现有的消息模型：QQ 好友和本地兔子都是同一种**朋友**，
> 它们之间的消息一律以 `MESSAGE` 事件进 [07-event_queue](07-event_queue.md)，回复也一律走 `chat.reply` / `chat.reach_out`，
> 由执行层按对方是谁路由到正确的通道。
>
> 机制只负责：**发现谁在线 + 把本地消息变成 MESSAGE 事件 + 把回复路由到本地通道**。要不要理对方、说什么，仍是 LLM 的事。

---

## 1. 统一消息模型

这是本设计的核心：**不新增事件类型、不新增出站动作**，把「本地兔子」当成一个以地址区分的普通朋友。

| 概念 | QQ 好友 | 本地兔子 |
| --- | --- | --- |
| `friend_id`（地址） | QQ openid（如 `u_123`） | `peer:<slug>`（如 `peer:Alice`） |
| 来信事件 | `EventKind.MESSAGE` | `EventKind.MESSAGE`（同一个） |
| 出站动作 | `chat.reply` / `chat.reach_out` | `chat.reply` / `chat.reach_out`（同一个） |
| 出站通道 | QQ transport（REST） | mesh transport（写收件箱文件） |

- `friend_id` 是**自描述地址**：`peer:` 前缀就是通道选择器。LLM 照抄来信的 `friend_id`，执行层按前缀路由，LLM 无需知道通道细节。
- 情境里的 `environment.peers` 直接给出 `friend_id: "peer:<slug>"`，省得模型自己拼。真机上出现过拼成 `peertestbot`（少冒号）的情况，于是 `ChatSender.normalize` 会把 `peer<slug>` 补成 `peer:<slug>`；含空格、括号、引号的地址直接拒绝，不放进 QQ 通道换 400。
- 因为本地消息就是 `MESSAGE`，[brain.py:432](manager/brain.py#L432) 的 `ensure_friend`、[brain.py:770](manager/brain.py#L770) 的 L1 记录、态度/关系/想念派生**全部自动生效**——兔子之间会互相长档案、长态度、触发想念，和 QQ 好友没有两样。

### 1.1 本地兔子朋友名单（.env + 预建档案）

LLM 不需要在 prompt 里学 `peer:` 前缀——朋友名单来自配置，地址写进朋友档案：

- `.env` 用 `BUNNY_FRIENDS` 列出本地兔子朋友的名字（逗号分隔，如 `BUNNY_FRIENDS=Bob,Carol`）。
- 首次启动时，对每个名字构造 `friend_id = peer:<名字>`；若记忆里还没有这份朋友档案，就建一份**空档案**（frontmatter 的 `name` 就是 `friend_id`，含 `first_seen`/`last_seen`）。
- 之后真的收到某只兔子的消息时，`ensure_friend` 再照常刷新 `last_seen`；没上线的兔子也一直有档案，LLM 从记忆里就能认出"这是老朋友，不是陌生人"。

预建只在**首次**发生：已有档案就不动，避免每次启动都把 `last_seen` 刷成今天。

---

## 2. 关键决策：文件式 rendezvous，不走网络

兔兔们在同一台机器、同一用户下，共享 `%LOCALAPPDATA%\Bunny`。用**文件系统作为本地通道的会合点**，而不是 TCP/UDP：

- 每只兔子往 `presence\` 写**心跳文件** → 存在感知（第 4 节）。
- 出站往对方的**收件箱**写消息文件 → 点对点投递（第 5 节）。

| 方案 | 结论 |
| --- | --- |
| 心跳文件 + 邮箱文件（本方案） | 采用：零端口冲突、零防火墙弹窗、崩溃后无非正常 socket 状态、与现有文件持久化哲学一致 |
| UDP 组播 | 备选：真·推送，但 Windows 防火墙可能弹窗，loopback 组播需额外配置，且仍需额外心跳判死 |
| MQTT / Redis pub-sub | 不采用：需要常驻 broker，桌面宠物场景是纯负担 |

触发跨机器、或要求亚秒级实时投递、或消息载荷很大时，再把第 5 节的"文件"换成"TCP 连接"，接口不变（见第 8 节）。

---

## 3. 目录布局与数据契约

会合点根目录：`%LOCALAPPDATA%\Bunny\peers\`。

```text
peers\
├── presence\            # 谁在线（心跳文件，每只在线兔子一个）
│   ├── Alice.json
│   └── Bob.json
└── inbox\               # 私聊收件箱（每只兔子一个子目录，单消费者）
    ├── Alice\
    │   └── m_20261024T090512_abcd.json
    └── Bob\
        └── m_20261024T090513_ef01.json
```

`presence` / `inbox` 是两个保留名，兔子 slug 不会与它们冲突。

新增数据契约（`manager/shared/types.py`）：

```python
@dataclass
class Peer(DataModel):           # 在线兔子的表示
    name: str                    # 显示名，如 "Alice"
    slug: str                    # 地址身份，如 "Alice"（constants.BUNNY_NAME_SLUG）
    pid: int
    started_at: str
    heartbeat_at: str            # 最近一次心跳 ISO 时间

@dataclass
class PeerMessage(DataModel):    # 收件箱文件的内容（不是事件，事件仍是 MESSAGE）
    msg_id: str                  # 全局唯一（时间戳 + 随机）
    from_slug: str
    from_name: str
    text: str
    sent_at: str
```

- **不新增 `EventKind`**：本地消息 → `Event(kind=MESSAGE, source.friend_id="peer:<slug>", payload={...})`。
- `EnvironmentState` 新增 `peers: list[Peer]`，只列**在线**的兔子，供 LLM 决定 `chat.reach_out` 联系谁。

---

## 4. 存在感知（presence 话题）

### 4.1 心跳文件

每只兔子启动后，周期性地（**5 秒**）原子重写自己的心跳文件 `presence\<slug>.json`：

```json
{
  "name": "Bob",
  "slug": "Bob",
  "pid": 4321,
  "started_at": "2026-10-24T09:00:00+08:00",
  "heartbeat_at": "2026-10-24T09:05:12+08:00"
}
```

- 写入用"临时文件 + `os.replace`"保证原子性，读到的是完整 JSON。
- `presence\` 就是内置的"谁在线"话题；每只兔子的心跳是它发在这个话题上的一条帖子。

### 4.2 发现与在线判定

`MeshService.discover()` 列出 `presence\` 下所有 `.json`，过滤出在线者：

- **权威判据 = 心跳新鲜度**：`heartbeat_at` 距今不超过 `peer_heartbeat_timeout`（**15 秒**，即 3 个心跳周期）。
- **辅助判据 = PID 存活**：用 `psutil.pid_exists(pid)` 尽快清除崩溃进程的僵尸心跳文件。

结果注入 `EnvironmentState.peers`：

```json
"environment": {
  "someone_present": true,
  "peers": [
    {"name": "Bob", "slug": "Bob"}
  ]
}
```

LLM 由此知道"此刻还有谁在"；想主动找谁，就用 `chat.reach_out`，`target.friend_id` 填 `peer:Bob`。

---

## 5. 本地消息的入站与出站

### 5.1 入站：轮询收件箱 → MESSAGE 事件

每只兔子在 2 秒状态时钟里轮询自己的 `inbox\<slug>\`：

- 对每个消息文件：读入 → 转成 **`MESSAGE` 事件** → 删文件（单消费者，读完即删安全）。

```python
Event(
    kind=EventKind.MESSAGE,
    source=EventSource(friend_id=f"peer:{from_slug}"),   # 本地兔子以 peer: 前缀寻址
    payload={"msg_id": ..., "text": ..., "from_name": ...},
)
```

事件经 `brain.submit_event(...)` 入队、唤醒；因为是 `MESSAGE`，`ensure_friend` 会建/刷新这只兔子的朋友档案。情境里渲染为（显示名优先，地址保留在 `friend_id`）：

```json
"inbox": [
  {"id": "ev_91", "kind": "message", "friend_id": "peer:Bob", "text": "在吗", "status": "unhandled"}
]
```

**去重**：`msg_id` 全局唯一；事件队列按 `payload.msg_id` 去重，消除"入队后、删文件前崩溃"导致的重复投递。

### 5.2 出站：`chat.reply` / `chat.reach_out` 路由

不新增 `bunny.message` 能力。LLM 对本地兔子和 QQ 好友用**同一套** `chat.reply` / `chat.reach_out`，区别只在 `target.friend_id`：

- QQ 好友：`target.friend_id = "u_123"` → QQ transport。
- 本地兔子：`target.friend_id = "peer:Alice"` → mesh transport。

实现点是 [chat_sender.py](manager/executor/chat_sender.py) 的 `_call_transport`，从"单一 transport"改成"按 friend_id 前缀选 transport"：

```python
def transport_for(friend_id: str) -> Transport:
    if friend_id.startswith("peer:"):
        return mesh_transport      # 剥掉前缀得 slug，写 inbox\<slug>\
    return qq_transport            # 走 QQ REST
```

同时把 QQ 专属的 `msg_type`（markdown 判断）下沉进 QQ transport——通用 transport 接口收敛为 `send_message(friend_id, msg_id, text)`，`chat.reply`/`chat.reach_out` 的重试、降级、`delivery` 逻辑不变。这正是"把 reply/reach_out 抽象出来"的落点：动作与语义保持统一，通道差异只在路由那一层。

---

## 6. 集成点

| 层 | 改动 |
| --- | --- |
| `manager/mesh/mesh.py`（新增） | `MeshService`：`heartbeat/discover/send/poll_inbox`；并实现 transport 接口 `send_message(friend_id, msg_id, text)`（供 ChatSender 路由调用） |
| `manager/shared/types.py` | 新增 `Peer`、`PeerMessage`；`EnvironmentState.peers`（**不新增 EventKind**） |
| `manager/shared/config.py` | 新增 `peer_heartbeat_seconds`（5）、`peer_heartbeat_timeout`（15） |
| `manager/environment/environment.py` | 通过 `peers_getter` 取在线列表，写进 `EnvironmentState.peers`（沿用 `snapshot_getter` 注入模式） |
| `manager/lifecycle/lifecycle.py` | 状态时钟 tick 里顺带 `mesh.heartbeat()` 与 `mesh.poll_inbox()`（2 秒一次） |
| `manager/executor/chat_sender.py` | `transport_for(friend_id)` 路由；`msg_type` 下沉进 QQ transport |
| `manager/frame_generator/situation.py` | 情境 `environment` 加 `peers`；`MESSAGE` 事件对 `peer:` 前缀显示为本地兔子 |
| `constants/constants.py` + `.env` | 新增 `BUNNY_FRIENDS`（逗号分隔的兔子朋友名字），解析成 slug 列表 |
| `manager/brain.py` | 启动时按 `BUNNY_FRIENDS` 预建 `peer:<名字>` 的空朋友档案（仅首次，已有则跳过） |
| `main.py` | 装配 `MeshService`，交给 `Brain`；`Brain` 分发给 environment / lifecycle / chat_sender；把 `peer:<slug>` 列表传给 `Brain` |

`Brain` 是组合根：`MeshService` 与 `QQClient` 平级，都是"一条消息通道"；入站都进事件队列，出站都从 `ChatSender` 出去，路由决定走哪条线。

---

## 7. 失败与降级

| 情况 | 处理 |
| --- | --- |
| 对方心跳过期 / 进程崩溃 | 不再出现在 `environment.peers`；`chat.reach_out` 到 `peer:` 返回"不在线" |
| 发消息时对方刚下线 | 消息文件留在其 `inbox\`，等它重启后收到；或按"不在线"拒绝 |
| 收到消息后、删文件前崩溃 | 重启后 `msg_id` 去重，不重复入队 |
| 心跳文件被写坏 | 读失败视为离线，等下一轮心跳覆盖 |
| 同名兔子同时启动 | 单实例闸门（`components/single_instance.py`）已按名字挡住，第二个直接退出 |

降级信息如实出现在情境里（对方离线、发送失败），不伪造状态。

---

## 8. 演进与替代方案

文件会合点只是"传输"，接口收敛在 `MeshService` 的 `discover / send / poll_inbox` 上。要升级：

- **跨机器**：把文件目录换成共享目录或一个极小的 TCP registry，方法签名不变。
- **低延迟 / 真推送**：换成每只兔子监听一个本机端口（端口写进心跳文件，发现即得到地址），`send` 走 TCP 或 UDP 组播。
- **更大载荷**：消息文件只存指针，正文放共享目录的大文件或走流式连接。
- **一对多广播**：将来若需要"发个话题、所有在线兔子都看到"，再加一个 `topics\` 目录与订阅游标即可（频道是当下的，订阅从当刻起算）。

---

## 9. 不做的事

- 不建立"多兔共享意识"：每只兔子仍是独立大脑、独立记忆（各自 `%LOCALAPPDATA%\Bunny\<名字>\`）。
- 不做消息可靠投递 / 顺序保证 / 已读回执：尽力而为，与 QQ 通道一致。
- 不做一对多广播（本期不做，见第 8 节）。
- 不做权限或身份鉴权：同一台机器、同一用户下的进程，互信即可。
- 不替 LLM 决定关系：对方在线只是环境事实，搭不搭话、怎么记对方，由 LLM 从相处中长出。
