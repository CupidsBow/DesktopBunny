import base64
import io
import cv2
import requests
from PIL import Image
from constants import constants
from datetime import datetime, timezone, timedelta


class ScreenAnalyzer:
    def __init__(self, platform_detector):
        """
        Args:
            platform_detector: PlatformDetector 实例，用于截取屏幕
        """
        self.platform_detector = platform_detector
        self.silicon_chat_model = constants.CHAT_MODEL
        self.silicon_chat_url = constants.SILICONFLOW_CHAT_URL

    def _build_screen_analyze_prompt(self, bunny=None) -> str:
        """根据当前北京时间构建带时间感知的提示词"""
        beijing_hour = datetime.now(timezone(timedelta(hours=8))).hour
        
        if beijing_hour < 6:
            bunny_feeling = "深夜"
        elif beijing_hour < 9:
            bunny_feeling = "早上"
        elif beijing_hour < 12:
            bunny_feeling = "上午"
        elif beijing_hour < 14:
            bunny_feeling = "午后"
        elif beijing_hour < 18:
            bunny_feeling = "下午"
        elif beijing_hour < 20:
            bunny_feeling = "傍晚"
        elif beijing_hour < 23:
            bunny_feeling = "晚上"
        else:
            bunny_feeling = "深夜"
        
        if bunny.satiety < 20:
            bunny_feeling += "，并且你肚子特别饿了"
        elif bunny.satiety < 50:
            bunny_feeling += "，并且你有点饿了"
        else:
            bunny_feeling += "，并且你吃得饱饱的"

        return f"""你是图片里的兔兔{bunny.name}。现在是{bunny_feeling}。
结合这张屏幕截图，用一句简短幽默的话吐槽，或者对屏幕里的关键点发出疑问。
要求：
- 30字以内
- 不要描述画面，直接说评论
- 直接输出评论文案，不要加任何前缀"""

    def _capture_and_encode(self, detector):
        """截图并转为 base64"""
        img = detector.capture_screen()
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        pil_img.thumbnail((640, 640))

        buffer = io.BytesIO()
        pil_img.save(buffer, format="JPEG", quality=40)
        return base64.b64encode(buffer.getvalue()).decode()

    def analyze_screen(self, bunny) -> str:
        """分析屏幕，返回吐槽文案"""
        try:
            image_b64 = self._capture_and_encode(self.platform_detector)
            
            res = requests.post(
                url=self.silicon_chat_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {constants.SILICONFLOW_API_KEY}"
                },
                json={
                    "model": self.silicon_chat_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{
                                'type': 'text',
                                'text': self._build_screen_analyze_prompt(bunny),
                            }, {
                                'type': 'image_url',
                                'image_url': {
                                    'url': f"data:image/jpeg;base64,{image_b64}",
                                },
                            }]
                        }
                    ],
                    "enable_thinking": False
                }
            )
            
            if res.json()["choices"]:
                reply = res.json()["choices"][0]["message"]["content"]
                return reply
            else:
                return None

        except Exception as e:
            print(f"分析失败: {e}")
            return None