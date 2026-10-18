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

## Quick Start

### 安装 Python 3.13.13 & 依赖

```bash
pip install -r requirements.txt
```

### 打包

```bash
pyinstaller --name Bunny --onefile --windowed --icon=assets/icon.png --add-data "assets\*;assets" --add-data "components\*.py;components" --add-data ".env;." main.py
```

运行 dist 目录下的 Bunny.exe

## 配置

- 存档保存在 `%LOCALAPPDATA%\Bunny\bunny_save.dat`
- 云端模型配置放在根目录 `.env`（已加入 .gitignore，模板见 `.env.sample`），预留 `LLM_API_KEY` / `LLM_CHAT_URL` / `LLM_MODEL` 三项。打包时随 exe 一起打入，当前暂无功能使用，为后续接入模型预留
