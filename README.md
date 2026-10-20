# Desktop Bunny

![Bunny](assets/header.png)

模仿 Rabi-Ribi 风格画的兔兔

## Feature

- 在底部任务栏随机游走
- 检测屏幕上可以支撑的平台并跳上去（可在托盘菜单开关）
- 点击静止状态的 Bunny 使其跳跃
- 添加/移除兔兔（1-5只，托盘菜单）
- 点击下蹲状态的 Bunny 可以使其变成兔娘（需要 50 以上饱食度）
- 饱食度（0 - 100）每 216 秒减少一点，拖动文件至 Bunny 可喂食恢复饱食度（会销毁文件，不进入回收站）
- 屏幕分辨率 / 工作区尺寸变化时自动退出，重启后适配新尺寸
- 内置 QQ 机器人：通过 QQ 私聊 / 群 @ 与兔兔对话，或下发命令

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

- 私聊发消息、或群里 @ 机器人，会调用 DeepSeek 模型回复（当前为单次、无记忆对话）
- 模型自带工具调用，会按需自动调用 `get_local_time`（当前时间）、`calc`（数学计算）
- 命令（大小写不敏感）：
  - `/help` 显示帮助
  - `/echo <文本>` 让屏幕上的兔兔说一句话
  - `/feed <名字>` 喂食指定名字的兔兔（饱食度 +20）
  - `/status` 查看所有兔兔的状态

### QQ 后台要求

在 [q.qq.com](https://q.qq.com) 创建机器人后，需要：

1. 机器人处于上线 / 审核通过状态（未上线则把 `.env` 的 `QQ_IS_SANDBOX` 设为 `true`）
2. 订阅「单聊消息」「群聊@消息」事件
3. 用自己的 QQ 私聊机器人，或把机器人拉进群并 @ 它

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
