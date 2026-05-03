# Desktop Bunny

![Bunny](assets/header.png)

模仿 Rabi-Ribi 风格画的兔兔

## Feature

- 在底部任务栏随机游走
- 检测屏幕上可以支撑的平台并跳上去
- 点击静止状态的 Bunny 使其跳跃
- 读取屏幕内容并吐槽（需要连接 Ollama qwen3-vl:4b）
- 加了饱食度（0 - 100）每 216 秒减少一点，可以拖动文件至 Bunny 来喂食恢复 Bunny 的饱食度（会销毁文件，不进入回收站）
- 点击眯眼下蹲状态的 Bunny 可以使其变成兔娘 Alice（需要 50 以上饱食度）
- 支持与 Bunny 交互（需要连接 Ollama qwen3-vl:8b），保存长期记忆（需要连接 Ollama bge-m3）
- 目前调用模型是写死的

## Quick Start

### 安装 Ollama 模型

> ollama pull qwen3-vl:4b

> ollama pull qwen3-vl:8b

> ollama pull bge-m3

### 安装 Python 3.13.13

> pip install -r requirements.txt
>
> pyinstaller --name Bunny --onefile --windowed --icon=assets/icon.png --add-data "assets\*;assets" --add-data "components\*.py;components" main.py

运行`dist`目录下的`Bunny.exe`

## 连接 Ollama

目前使用`qwen3-vl:4b`与`qwen3-vl:8b`模型交互，`bge-m3`进行向量化存储记忆，需要在 Ollama 安装模型并开放接口，Bunny 会自动尝试连接 Ollama
