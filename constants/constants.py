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
# 工作区尺寸变化轮询间隔：分辨率/任务栏变化后按正常流程退出重启
SCREEN_CHANGE_POLL_INTERVAL_SECONDS = 2
# --- 读屏幕吐槽 ---
SCREEN_ANALYZE_MODEL = "zai-org/GLM-4.5V"
# 关掉思考链。实测 GLM-4.5V 从 4.5 秒降到 1.0 秒，而且开着 thinking 时
# 偶尔会把 <|begin_of_box|> 这类模板控制符漏进正文，画在屏幕上很难看。
# 注意这个参数是模型相关的：换成 Qwen3-VL-30B-A3B、Qwen3-VL-32B-Thinking
# 等不支持它的模型会直接报 20015，那时把这里改成 None 即可。
SCREEN_ANALYZE_ENABLE_THINKING = False
SCREEN_ANALYZE_POLL_INTERVAL_SECONDS = 1     # 前台窗口轮询间隔
SCREEN_ANALYZE_COOLDOWN_SECONDS = 60         # 两次吐槽最短间隔，防止来回切窗口刷屏
SCREEN_ANALYZE_DWELL_SECONDS = 600           # 同一窗口停留超过这么久也触发一次
SCREEN_ANALYZE_OTHER_WINDOW_NUM = 5          # 作为上下文带上几个其他窗口
SCREEN_ANALYZE_RECENT_QUIPS = 5              # 记住最近说过几句，避免重复
SCREEN_ANALYZE_IMAGE_MAX_SIZE = 640          # 截图长边上限（再高性价比很低，见 README）
SCREEN_ANALYZE_JPEG_QUALITY = 40
SCREEN_ANALYZE_TIMEOUT_SECONDS = 15          # 实测 GLM-4.5V 约 1 秒；注意超时会阻塞下面的轮询循环
DEFAULT_SAVE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Bunny')
SILICONFLOW_CHAT_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_API_KEY="sk-wzeeyxwvuylblahkproiburdhapeaoqrwyloqjnwbpximkee"
BUNNY_NAME_LIST = ["David", "Michael", "John", "Tom", "Jack", "Kevin", "Peter", "Paul", "Henry", "Alan", "Mark", "Tony", "Jimmy", "Jerry", "Jason", "Brian", "Eric", "Nick", "Mary", "Linda", "Lisa", "Amy", "Sarah", "Anna", "Lucy", "Lily", "Nancy", "Helen", "Jenny", "Jessie", "Rita", "Rose", "Emma", "Grace", "Cindy"]
