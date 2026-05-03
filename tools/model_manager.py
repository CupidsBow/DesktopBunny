import base64
import io
import numpy as np
import cv2
import requests
from PIL import Image
from typing import List, Tuple
from constants import constants
from datetime import datetime, timezone, timedelta


class ModelManager:
    """Ollama 模型管理器，负责截图分析、对话、长期记忆"""

    def __init__(self, platform_detector):
        """
        Args:
            detector: PlatformDetector 实例，用于截取屏幕
            screen_analyze_model: 屏幕分析模型
            chat_model: 对话模型
            embedding_model: 向量化模型
            ollama_generate_url: Ollama generate API 地址
            ollama_chat_url: Ollama chat API 地址
            max_token_length: 对话最大上下文长度 (token 数)
        """
        self.platform_detector = platform_detector
        self.screen_analyze_model = constants.SCREEN_ANALYZE_MODEL
        self.chat_model = constants.CHAT_MODEL
        self.embedding_model = constants.EMBEDDING_MODEL
        self.ollama_generate_url = constants.LOCAL_OLLAMA_GENERATE_URL
        self.ollama_chat_url = constants.LOCAL_OLLAMA_CHAT_URL
        self.max_token_length = constants.MAX_CHAT_LENGTH

        # 对话历史（只存文本，token 长度近似计算）
        self.chat_history = []

    # ---------- 1. 屏幕分析 ----------
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

        return f"""你是图片里的兔子桌宠，名字是{bunny.name}。现在是{bunny_feeling}。
结合这张屏幕截图，用一句简短幽默的话吐槽，或者对屏幕里的关键点发出疑问。
要求：
- 语气可爱、像宠物
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

            payload = {
                "model": self.screen_analyze_model,
                "prompt": self._build_screen_analyze_prompt(bunny),
                "images": [image_b64],
                "stream": False,
                "options": {
                    "temperature": 0.8,
                },
            }

            response = requests.post(self.ollama_generate_url, json=payload, timeout=60)
            result = response.json()["response"].strip()

            # 清理可能的前缀
            for prefix in ["吐槽：", "评论：", "兔兔：", "兔兔说：", "回复："]:
                if result.startswith(prefix):
                    result = result[len(prefix):].strip()

            return result

        except Exception as e:
            print(f"分析失败: {e}")
            return None

    # ---------- 2. 对话聊天 ----------
    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数（中文一个字约 1.5 token，英文约 0.75 token/字母）"""
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        others = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + others * 0.4)

    def chat(self, user_input: str) -> str:
        """
        与聊天模型对话，自动管理上下文长度。
        Returns: 模型回复
        """
        self.chat_history.append({
            "role": "user",
            "content": user_input
        })

        # 构建上下文：倒序加入历史，直到接近长度限制
        bunny_prompt = {
            "role": "system",
            "content": constants.BUNNY_PROMPT
        }
        long_memory = ""
        bunny_prompt["content"] += long_memory
        total_tokens = self._estimate_tokens(bunny_prompt)
        selected_history = []

        for msg in reversed(self.chat_history):
            token_length = self._estimate_tokens(msg)
            if total_tokens + token_length <= self.max_token_length:
                total_tokens += token_length
                selected_history.insert(0, msg)
            else:
                break

        # 拼接完整提示词
        message = [bunny_prompt] + selected_history

        payload = {
            "model": self.chat_model,
            "messages": message,
            "stream": False
        }
        resp = requests.post(self.ollama_chat_url, json=payload, timeout=120)
        resp.raise_for_status()
        reply = resp.json()["message"]["content"]
        self.chat_history.append({
            "role": "assistant",
            "content": reply
        })
        return reply

    # ---------- 3. 长期记忆（向量化） ----------
    def _get_embedding(self, text: str) -> np.ndarray:
        """获取文本的向量表示"""
        payload = {
            "model": self.embedding_model,
            "prompt": text,
        }
        resp = requests.post(f"{self.ollama_generate_url}/api/embeddings", json=payload, timeout=30)
        resp.raise_for_status()
        embedding = resp.json()["embedding"]
        return np.array(embedding, dtype=np.float32)

    def add_memory(self, text: str):
        """将文本存入长期记忆（向量化并保存）"""
        emb = self._get_embedding(text)
        self.memory_texts.append(text)
        self.memory_embeddings.append(emb)

    def retrieve_memory(self, query: str, top_k: int = 3) -> List[str]:
        """根据查询文本检索最相关的记忆，返回原文列表（按相似度降序）"""
        if not self.memory_texts:
            return []

        query_emb = self._get_embedding(query)
        similarities = []
        for emb in self.memory_embeddings:
            # 余弦相似度
            dot = np.dot(query_emb, emb)
            norm = np.linalg.norm(query_emb) * np.linalg.norm(emb)
            sim = dot / norm if norm > 0 else 0.0
            similarities.append(sim)

        # 取 top_k 个最相似的索引
        top_indices = np.argsort(similarities)[::-1][:top_k]
        return [self.memory_texts[i] for i in top_indices]