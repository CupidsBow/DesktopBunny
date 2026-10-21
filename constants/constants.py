import os
import sys


def _load_env() -> dict:
    """读取 .env（开发时取项目根目录，frozen 时取打包内置的 sys._MEIPASS）。

    .env 不进 git，打包时通过 spec/--add-data 打进 exe，运行时从 _MEIPASS 读。
    """
    if getattr(sys, "frozen", False):
        env_path = os.path.join(sys._MEIPASS, ".env")
    else:
        # constants.py 位于 constants/ 下，项目根目录是上一级
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )

    values: dict = {}
    if not os.path.exists(env_path):
        return values
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("\"'")
    return values


_ENV = _load_env()


GLOBAL_FPS = 60
BUNNY_ICON = "assets/icon.png"
BUNNY_ICON_ICO = "assets/icon.ico"
BUNNY_IDLE_PNG = "assets/BunnyIdle.png"
BUNNY_JUMP_PNG = "assets/BunnyJump.png"
BUNNY_FLOATING_PNG = "assets/BunnyFloating.png"
BUNNY_FALLING_PNG = "assets/BunnyFalling.png"
BUNNY_SPECIAL_PNG = "assets/BunnySpecial.png"
BUNNY_GIRL_IDLE_PNG = "assets/BunnyGirlIdle.png"
BUNNY_GIRL_MOVE_PNG = "assets/BunnyGirlMove.png"
JUMP_WAV_PATH = "assets/se_jump.wav"
PLATFORM_HEIGHT = 100
PLATFORM_DETECT_TIME_INTERVAL_SECONDS = 2
PLATFORM_MAX_NUM = 10
# 桌面上只有 Alice 一只兔兔，不存在实例名与多兔路由
ALICE_NAME = "Alice"
# 工作区尺寸变化轮询间隔：分辨率/任务栏变化后按正常流程退出重启
SCREEN_CHANGE_POLL_INTERVAL_SECONDS = 2
# --- 云端模型配置（.env 覆盖，后续功能会复用） ---
LLM_MODEL = _ENV.get("LLM_MODEL", "deepseek-v4-pro")
LLM_CHAT_URL = _ENV.get("LLM_CHAT_URL", "https://api.deepseek.com/chat/completions")
LLM_API_KEY = _ENV.get("LLM_API_KEY", "sk-REPLACE-ME")
# --- QQ 机器人配置（.env 覆盖） ---
QQ_APP_ID = _ENV.get("QQ_APP_ID", "")
QQ_APP_SECRET = _ENV.get("QQ_APP_SECRET", "")
QQ_IS_SANDBOX = _ENV.get("QQ_IS_SANDBOX", "false").lower() == "true"
# 订阅事件位掩码：1<<25 = 群@消息 + 单聊消息（若鉴权被拒，按控制台「订阅事件」调整）
QQ_INTENTS = int(_ENV.get("QQ_INTENTS", str(1 << 25)))
# 并发处理 QQ 消息的 worker 线程数
QQ_WORKER_COUNT = int(_ENV.get("QQ_WORKER_COUNT", "8"))
DEFAULT_SAVE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Bunny')
