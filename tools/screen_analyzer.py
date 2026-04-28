import base64
import io
import cv2
import numpy as np
from PIL import Image
import requests
from datetime import datetime, timezone, timedelta


class ScreenAnalyzer:
    """通过 Ollama API 调用本地 VLM 分析屏幕"""

    def __init__(self, model="qwen3-vl:4b"):
        self.model = model
        self.api_url = "http://localhost:11434/api/generate"

    def _build_prompt(self) -> str:
        """根据当前北京时间构建带时间感知的提示词"""
        beijing_hour = datetime.now(timezone(timedelta(hours=8))).hour
        
        if beijing_hour < 6:
            time_feeling = "深夜"
        elif beijing_hour < 9:
            time_feeling = "早上"
        elif beijing_hour < 12:
            time_feeling = "上午"
        elif beijing_hour < 14:
            time_feeling = "午后"
        elif beijing_hour < 18:
            time_feeling = "下午"
        elif beijing_hour < 20:
            time_feeling = "傍晚"
        elif beijing_hour < 23:
            time_feeling = "晚上"
        else:
            time_feeling = "深夜"

        return f"""你是图片里的兔子桌宠。现在是{time_feeling}。
看这张屏幕截图，用一句简短幽默的话吐槽，或者对屏幕里的关键点发出疑问。
要求：
- 语气可爱、傲娇、像宠物
- 25字以内
- 不要描述画面，直接说评论
- 直接输出评论文案，不要加任何前缀"""

    def capture_and_encode(self, detector):
        """截图并转为 base64"""
        img = detector.capture_screen()
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        pil_img.thumbnail((640, 640))

        buffer = io.BytesIO()
        pil_img.save(buffer, format="JPEG", quality=40)
        return base64.b64encode(buffer.getvalue()).decode()

    def analyze(self, detector) -> str:
        """分析屏幕，返回吐槽文案"""
        try:
            image_b64 = self.capture_and_encode(detector)

            payload = {
                "model": self.model,
                "prompt": self._build_prompt(),  # ✅ 动态生成带时间的 prompt
                "images": [image_b64],
                "stream": False,
                "options": {
                    "temperature": 0.8,
                },
            }

            response = requests.post(self.api_url, json=payload, timeout=60)
            result = response.json()["response"].strip()

            # 清理可能的前缀
            for prefix in ["吐槽：", "评论：", "兔兔：", "兔兔说：", "回复："]:
                if result.startswith(prefix):
                    result = result[len(prefix):].strip()

            return result

        except Exception as e:
            print(f"分析失败: {e}")
            return ""

    def test(self, image_path: str = None):
        """
        测试方法：用指定图片或实时截图测试分析
        
        Args:
            image_path: 图片路径，不传则实时截图
        """
        if image_path:
            print(f"测试图片: {image_path}")
            img = cv2.imread(image_path)
            if img is None:
                print("图片读取失败！")
                return
            
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            pil_img.thumbnail((640, 640))
            
            buffer = io.BytesIO()
            pil_img.save(buffer, format="JPEG", quality=40)
            image_b64 = base64.b64encode(buffer.getvalue()).decode()
            
        else:
            print("实时截图测试...")
            from tools.platform_detector import PlatformDetector
            detector = PlatformDetector()
            image_b64 = self.capture_and_encode(detector)
        
        prompt = self._build_prompt()
        print(f"Prompt: {prompt[:50]}...")
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {
                "temperature": 0.8,
            },
        }
        
        print("正在分析...")
        response = requests.post(self.api_url, json=payload, timeout=60)
        result = response.json()
        
        print(f"\n{'='*50}")
        print(f"模型: {result['model']}")
        print(f"时间: {result['created_at']}")
        print(f"{'='*50}")
        print(f"回复: {result['response']}")
        print(f"{'='*50}")
        
        if result.get('total_duration'):
            print(f"总耗时: {result['total_duration'] / 1e9:.2f}s")
        if result.get('eval_duration'):
            print(f"推理耗时: {result['eval_duration'] / 1e9:.2f}s")
        
        return result['response']


if __name__ == "__main__":
    analyzer = ScreenAnalyzer(model="qwen3-vl:4b")
    analyzer.test("test.png")