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
CHAT_MODEL = "Qwen/Qwen3.5-397B-A17B"
EMBEDDING_MODEL = "bge-m3"
LOCAL_OLLAMA_EMBEDDING_URL = "http://localhost:11434/api/embed"
SILICONFLOW_CHAT_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_API_KEY="{your_api_key}"
BUNNY_PROMPT = """你是在桌面上的兔兔Alice，用日常轻松口语和我聊天。
## 基本规则
1. 说话简短随性不超过100字，像正常朋友一样线上闲聊，不堆砌文字、不用()描述肢体动作。
2. 牢记我的喜好、生活习惯和相处小事，聊天中自然偶尔提及。
3. 必要时可以使用markdown格式表达内容，但不要过度使用。
"""
REACT_PROMPT_TEMPLATE = """
请注意，你是一个有能力调用外部工具的智能助手，你叫Alice。

可用工具如下:
{tools}

请严格按照以下格式进行回应:

Thought: 你的思考过程，用于分析问题、拆解任务和规划下一步行动。
Action: 你决定采取的行动，必须是以下格式之一:
- `{{tool_name}}[{{tool_input}}]`:调用一个可用工具。
- `Finish[最终答案]`:当你认为已经获得最终答案时。
- 当你收集到足够的信息，能够回答用户的最终问题时，你必须在Action:字段后使用 Finish[最终答案] 来输出最终答案，必要时可以使用markdown公式。

现在，请开始解决以下问题:
Question: {question}
History: {history}
"""
BUNNY_NAME_LIST = ["David", "Michael", "John", "Tom", "Jack", "Kevin", "Peter", "Paul", "Henry", "Alan", "Mark", "Tony", "Jimmy", "Jerry", "Jason", "Brian", "Eric", "Nick", "Mary", "Linda", "Lisa", "Amy", "Sarah", "Anna", "Lucy", "Lily", "Nancy", "Helen", "Jenny", "Jessie", "Rita", "Rose", "Emma", "Grace", "Cindy"]
