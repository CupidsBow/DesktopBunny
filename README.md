# Desktop Bunny

![Bunny](assets/header.png)

模仿 Rabi-Ribi 风格画的兔兔

## Feature

- 桌面上只有 Alice 一只兔兔，在底部任务栏随机游走
- 检测屏幕上可以支撑的平台并跳上去（可在托盘菜单开关）
- 点击静止状态的 Alice 使其跳跃
- 点击下蹲状态的 Alice 可以使其变成兔娘（需要 50 以上饱食度）
- 饱食度（0 - 100）每 216 秒减少一点，拖动文件至 Alice 可喂食恢复饱食度（会销毁文件，不进入回收站）；她也可以自己决定吃点东西
- 单实例守护：同一时间只运行一个 Bunny 进程，手动重复启动或 Alice 借沙盒命令再次启动自己都会直接退出
- 屏幕分辨率 / 工作区尺寸变化时自动退出，重启后适配新尺寸
- 内置 QQ 机器人：通过 QQ 私聊 / 群 @ 与 Alice 对话

## Quick Start

### 安装 Python 3.13.13 & 依赖

```bash
pip install -r requirements.txt
```

### 运行 / 打包

直接运行：

```bash
python main.py
```

打包为单文件 exe：

```bash
pyinstaller --name Bunny --onefile --windowed --icon=assets/icon.png --add-data "assets\*;assets" --add-data "components\*.py;components" --add-data ".env;." main.py
```

## QQ 机器人

桌宠启动时会作为 QQ 官方机器人上线：连接官方 WebSocket 网关，只发起点对点出站连接，因此**不需要公网 IP 或内网穿透**。

- 私聊发消息、或群里 @ 机器人，消息会进入 Alice 的认知循环：她会读取当前情境与记忆，自己决定回复、做事、休息还是主动找人
- 对话会写入 L1 工作记忆，并保存到 `state/l1.json`，重启后自动恢复；L1 超过上限时，后台用 LLM 把较早的对话压缩成摘要并归档为 L2 情景记忆，不会简单丢弃
- Alice 可以调用工具（时间、网页、文件、沙盒、电脑观测等），也能自己发起和推进活动

### QQ 后台要求

在 [q.qq.com](https://q.qq.com) 创建机器人后，需要：

1. 机器人处于上线 / 审核通过状态（未上线则把 `.env` 的 `QQ_IS_SANDBOX` 设为 `true`）
2. 订阅「单聊消息」「群聊@消息」事件
3. 用自己的 QQ 私聊机器人，或把机器人拉进群并 @ 它

## 设计文档

Alice 的大脑设计见 [manager/brain.md](manager/brain.md)。代码目录、每个 `.py` 文件的职责/输入/输出与跨文件数据流见该文档 §6；模块细节见 `manager/design/01-07`。

`manager/shared/types.py` 与 `manager/shared/config.py` 是全部跨模块数据契约与可调参数；`manager/brain.py` 是组合根，`manager/` 下的感知、身体、记忆、认知、活动、能力、执行器、机制各层均按设计文档实现。

## 配置

- 存档保存在 `%LOCALAPPDATA%\Bunny\bunny_save.dat`
- 根目录 `.env` 保存密钥类配置（已加入 .gitignore，模板见 `.env.sample`）：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` | DeepSeek API Key |
| `LLM_CHAT_URL` | 对话接口地址（默认 `https://api.deepseek.com/chat/completions`） |
| `LLM_MODEL` | 模型名（如 `deepseek-v4-flash`） |
| `QQ_APP_ID` | QQ 机器人 AppID |
| `QQ_APP_SECRET` | QQ 机器人 AppSecret |
| `QQ_IS_SANDBOX` | `true` 用沙箱环境，`false` 用正式环境 |
| `QQ_INTENTS` | 订阅事件位掩码，默认 `33554432`（= 群@消息 + 单聊消息） |
| `QQ_WORKER_COUNT` | 并发处理消息的 worker 数，默认 `8` |

打包时 `.env` 会随 exe 一起打入。
