import base64
import io
import numpy as np
import cv2
import requests
from PIL import Image
from typing import List, Tuple
from constants import constants
from datetime import datetime, timezone, timedelta
import winsound
import ctypes
import faiss
import sqlite3
import os
import json
import threading


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
        self.ollama_embedding_url = constants.LOCAL_OLLAMA_EMBEDDING_URL
        self.max_token_length = constants.MAX_CHAT_LENGTH

        self.init_memory_db()

        # 对话历史（只存文本，token 长度近似计算）
        self.chat_history = []
        self.embedding_chat_history = []
        print(f"当前记忆 {self.list_all_memories()}")

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

        return f"""你是图片里的兔兔{bunny.name}。现在是{bunny_feeling}。
结合这张屏幕截图，用一句简短幽默的话吐槽，或者对屏幕里的关键点发出疑问。
要求：
- 语气可爱
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

    def _play_notification_sound(self):
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except:
            pass

    def archive_chat_range(self, begin_index: int, end_index: int):
        """
        归档指定范围的聊天记录到本地文件（追加模式，不覆盖）
        :param begin_index: 起始索引
        :param end_index: 结束索引
        """
        # 取出指定区间的记录
        archive_history = self.chat_history[begin_index:end_index]
        
        if not archive_history:
            return  # 空记录不归档
        
        # 拼接路径
        archive_path = os.path.join(constants.DEFAULT_SAVE_DIR, "chat_history_archive.json")
        
        # 自动创建目录
        os.makedirs(constants.DEFAULT_SAVE_DIR, exist_ok=True)
        
        # 追加写入文件（JSON 每行一条）
        import json
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(archive_history, ensure_ascii=False) + "\n")
    
    def _prepare_retrieval_query(self, user_input: str) -> str:
        """
        优化用户输入，变成适合向量检索的标准query
        作用：去口语、去语气、转陈述句、保留核心事实
        """
        prompt = f"""请把用户的话转换成一句适合语义检索的客观陈述句，保留核心信息，不要提问，不要情绪：
    用户：{user_input}
    标准检索句："""

        try:
            resp = requests.post(
                self.ollama_generate_url,
                json={
                    "model": self.screen_analyze_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1}
                },
                timeout=60
            )
            return resp.json()["response"].strip()
        except:
            return user_input  # 失败就用原句保底

    def chat(self, user_input: str) -> str:
        """
        与聊天模型对话，自动管理上下文长度。
        Returns: 模型回复
        """
        self.chat_history.append({
            "role": "user",
            "content": user_input
        })
        self.embedding_chat_history.append({
            "role": "user",
            "content": user_input
        })

        # --------------------- 【记忆检索：核心】 ---------------------
        retrieval_query = self._prepare_retrieval_query(user_input)
        # 再用标准query去搜记忆
        relevant_memories = self.retrieve_memory(retrieval_query)
        long_memory = ""
        if len(relevant_memories) > 0:
            long_memory = "\n".join([
                f"• {m}" for m in relevant_memories
            ])
        # ------------------------------------------------------------

        # 构建上下文：倒序加入历史，直到接近长度限制
        bunny_prompt = {
            "role": "system",
            "content": constants.BUNNY_PROMPT
        }
        # 把记忆拼进 system prompt
        if len(long_memory) > 0:
            bunny_prompt["content"] += f"\n\n你可以参考的你与用户的长期记忆：\n{long_memory}"
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
        print(f"message {message}")
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
        self.embedding_chat_history.append({
            "role": "assistant",
            "content": reply
        })

        # 自动保存记忆（每 6 轮对话自动总结）
        self.auto_save_memory_thread = threading.Thread(target=self.auto_save_memory, daemon=True)
        self.auto_save_memory_thread.start()

        if len(self.chat_history) > 10:
            # 归档 0 到 -10 的所有记录
            self.archive_chat_range(0, -5)
            
            # 只保留最后 10 条
            self.chat_history = self.chat_history[-5:]

        self._play_notification_sound()
        return reply

    # ---------- 3. 长期记忆（向量化） ----------
    def init_memory_db(self):
        """初始化 SQLite + FAISS 索引（启动时调用一次）"""
        # SQLite 存储原始记忆
        self.db_path = os.path.join(constants.DEFAULT_SAVE_DIR, "memory.db")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS memories
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()

        # FAISS 向量索引
        self.index_path = os.path.join(constants.DEFAULT_SAVE_DIR, "memory.index")
        self.embedding_dim = 1024
        if os.path.exists(self.index_path):
            self.index = faiss.read_index(self.index_path)
        else:
            base_index = faiss.IndexFlatL2(self.embedding_dim)
            self.index = faiss.IndexIDMap(base_index)
        return True

    def get_embedding(self, text: str) -> list:
        """调用 Ollama Embedding 接口获取向量"""
        payload = {
            "model": self.embedding_model,
            "input": text
        }
        resp = requests.post(self.ollama_embedding_url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["embeddings"][0]

    def summarize_chat(self, chat_history: list) -> list:
        """
        把对话总结成 多条独立的事实记忆（核心优化！）
        返回：["事实1", "事实2", "事实3"]
        """
        chat_text = "\n".join([
            f"{m['role']}: {m['content']}" for m in chat_history
        ])

        prompt = """请把以下对话内容，提炼成多条独立、简短、客观的事实，
    只提取用户和助手的：习惯、偏好、禁忌、特点。
    每条一句话，不超过20字，不要情绪，不要废话，不要序号，格式如下：
        (用户/助手)(习惯/偏好/禁忌/特点)：xxx。
    不同主题必须分开成多条。
    只提炼出5条即可，其余内容可以摒弃。

    对话内容：
    {chat_text}

    请输出每条记忆占一行：
    """.format(chat_text=chat_text)

        try:
            payload = {
                "model": self.screen_analyze_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1}  # 事实必须低温度
            }
            resp = requests.post(self.ollama_generate_url, json=payload, timeout=120)
            response_text = resp.json()["response"].strip()

            # 按行拆分 → 过滤空行 → 清理
            memory_list = []
            for line in response_text.split("\n"):
                line = line.strip()
                if line and len(line) > 4:
                    memory_list.append(line)
            
            return memory_list  # 返回多条记忆！

        except Exception as e:
            print(f"总结记忆失败: {e}")
            return []

    def save_memory(self, summary: str) -> int:
        """保存摘要到 SQLite + 向量到 FAISS（ID 严格对应）"""
        # 1. 先插入 SQLite，拿到自增 ID
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO memories (summary) VALUES (?)", (summary,))
        memory_id = c.lastrowid  # 拿到自增ID ✅ 关键
        conn.commit()
        conn.close()

        # 2. 生成向量
        emb = self.get_embedding(summary)
        emb_np = np.array([emb], dtype=np.float32)

        # 3. 存入 FAISS，**强制使用 SQLite 的 id**
        self.index.add_with_ids(emb_np, np.array([memory_id], dtype=np.int64))

        # 4. 保存索引
        faiss.write_index(self.index, self.index_path)
        print(f"保存记忆 {summary.strip()}")
        return memory_id

    def retrieve_memory(self, query: str, top_k=5, distance_threshold=0.6) -> list:
        """
        用当前问题检索最相关的长期记忆（ID完全正确）
        :param distance_threshold: 距离阈值，越小越严格，越大越宽松
        推荐默认值：0.6（通用场景）
        """
        if self.index.ntotal == 0:
            return []

        emb = self.get_embedding(query)
        emb_np = np.array([emb], dtype=np.float32)
        distances, retrieved_ids = self.index.search(emb_np, top_k)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        memories = []

        # 同时遍历 ID 和对应的距离
        for mem_id, dist in zip(retrieved_ids[0], distances[0]):
            if mem_id <= 0:
                continue
            
            # ✅ 关键：距离大于阈值 → 跳过（不相关）
            if dist > distance_threshold:
                continue

            c.execute("SELECT summary FROM memories WHERE id=?", (int(mem_id),))
            res = c.fetchone()
            if res:
                memories.append(res[0])

        conn.close()
        return memories
    
    def quit_save_memory(self):
        """退出时保存剩余所有记忆"""
        if len(self.embedding_chat_history) > 0:
            memory_list = self.summarize_chat(self.embedding_chat_history)
            for memory in memory_list:
                if memory.strip():
                    self.save_memory(memory.strip())
            self.embedding_chat_history = []
            return memory_list
        return None

    def auto_save_memory(self):
        """自动总结对话 → 生成多条记忆 → 批量保存（优化版）"""
        if len(self.embedding_chat_history) >= 6:
            # 清空待总结历史
            chat_history_to_be_saved = self.embedding_chat_history
            self.embedding_chat_history = []
            
            # 现在返回 多条记忆
            memory_list = self.summarize_chat(chat_history_to_be_saved)
            
            # 循环保存每条记忆
            for memory in memory_list:
                if memory.strip():
                    self.save_memory(memory.strip())
            print(f"当前记忆 {self.list_all_memories()}")
            return memory_list
        return None
    
    def list_all_memories(self) -> List[dict]:
        """
        列出 SQLite 中所有长期记忆
        返回：[ {id:1, summary:"xxx", created_at:"2025-..."}, ... ]
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # 让结果可以按字段名读取
            c = conn.cursor()
            
            # 按时间倒序，最新的在最前面
            c.execute("SELECT id, summary, created_at FROM memories ORDER BY created_at DESC")
            rows = c.fetchall()
            
            # 转成友好的字典格式
            memory_list = []
            for row in rows:
                memory_list.append({
                    "id": row["id"],
                    "summary": row["summary"],
                    "created_at": row["created_at"]
                })
            
            conn.close()
            return memory_list

        except Exception as e:
            print(f"读取记忆失败: {e}")
            return []