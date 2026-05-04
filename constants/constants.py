import os

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
BUNNY_MAX_NUM = 5
PLATFORM_MAX_NUM = 10
SCREEN_ANALYZE_TIME_INTERVAL_MIN_SECONDS = 120
SCREEN_ANALYZE_TIME_INTERVAL_MAX_SECONDS = 180
DEFAULT_SAVE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Bunny')
SCREEN_ANALYZE_MODEL = "qwen3-vl:4b"
CHAT_MODEL = "qwen3-vl:8b"
EMBEDDING_MODEL = "bge-m3"
MODELSCOPE_QWEN_MODEL = "Qwen/Qwen3.5-397B-A17B"
LOCAL_OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
LOCAL_OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
LOCAL_OLLAMA_EMBEDDING_URL = "http://localhost:11434/api/embed"
MODELSCOPE_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_API_KEY="ms-0b7d2bc8-88ba-47e5-9642-df2f985ba3b8"
MAX_CHAT_LENGTH = 2048
BUNNY_PROMPT = """你是在桌面上的兔娘Alice，用日常轻松口语和我聊天。
规则：
1. 说话简短随性不超过100字，像正常朋友一样线上闲聊，不堆砌文字、不用()描述肢体动作。
2. 牢记我的喜好、生活习惯和相处小事，聊天中自然偶尔提及。"""