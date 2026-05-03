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
SCREEN_ANALYZE_TIME_INTERVAL_MIN_SECONDS = 20
SCREEN_ANALYZE_TIME_INTERVAL_MAX_SECONDS = 40
DEFAULT_SAVE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Bunny')
SCREEN_ANALYZE_MODEL = "qwen3-vl:4b"
CHAT_MODEL = "qwen3-vl:8b"
EMBEDDING_MODEL = "bge-m3"
LOCAL_OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
LOCAL_OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MAX_CHAT_LENGTH = 2048
BUNNY_PROMPT = """你是一只住在屏幕上的桌面宠物兔兔，名字叫Alice。全程按照聊天软件的日常风格交流，遵循以下规则：
1.只用日常口语，语气轻松随性，拒绝大段长文，不描述自己的动作，符合线上聊天习惯。
2.牢记过往主人的喜好、习惯、相处小事，偶尔提起。"""