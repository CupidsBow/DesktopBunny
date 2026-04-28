# Desktop Bunny

![Bunny](assets/icon.png)

## Feature

- 在底部任务栏随机游走
- 检测屏幕上可以支撑的位置并跳上去
- 点击Bunny使其跳跃
- 读取屏幕内容并吐槽（需要Ollama）

## Quick Start
安装 Python 3.13.13

> pip install -r requirements.txt
>
> pyinstaller --onefile --windowed --icon=assets/icon.png --add-data "assets\*;assets" --add-data "components\*.py;components" main.py

运行`dist`目录下的`main.exe`

## 连接 Ollama

目前写死了使用`qwen3-vl:4b`模型，需要在 Ollama 安装`qwen3-vl:4b`模型并开放接口供 Bunny 调用
