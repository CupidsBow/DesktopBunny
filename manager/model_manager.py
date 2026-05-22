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
from tools.tool_executor import ToolExecutor
from constants import constants
import re


class ModelManager:
    """Ollama 模型管理器，负责截图分析、对话、长期记忆"""

    def __init__(self, tool_executor):
        self.silicon_chat_model = constants.CHAT_MODEL
        self.embedding_model = constants.EMBEDDING_MODEL
        self.silicon_chat_url = constants.SILICONFLOW_CHAT_URL
        self.ollama_embedding_url = constants.LOCAL_OLLAMA_EMBEDDING_URL
        self.tool_executor = tool_executor
        self.react_agent = ReActAgent(
            self,
            self.tool_executor,
            5
        )

        self.init_memory_db()

        # 对话历史（只存文本，token 长度近似计算）
        self.chat_history = []
        self.embedding_chat_history = []
        print(f"当前记忆 {self.list_all_memories()}")

    # ---------- 1. 对话聊天 ----------
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
        prompt = f"""将用户的行为提炼为极简客观陈述，用于语义向量检索：
- 剔除口语、调侃、语气助词
- 只保留：用户+行为+核心对象
- 不要疑问、不要修饰、越精简越好
- 控制在8-20字，纯平铺陈述

用户：{user_input}
标准检索句："""

        try:
            print(f"prompt {prompt}")
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
                            "content": prompt
                        }
                    ],
                    "enable_thinking": False
                }
            )
            print(f"res.json() {res.json()["choices"][0]["message"]["content"]}")
            return res.json()["choices"][0]["message"]["content"]
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

        fact_answer = self.react_agent.run(user_input)
        if not fact_answer:
            fact_answer = "兔脑过载..."

        # --------------------- 【记忆检索：核心】 ---------------------
        # retrieval_query = self._prepare_retrieval_query(user_input)
        # # 再用标准query去搜记忆
        # relevant_memories = self.retrieve_memory(retrieval_query)
        # long_memory = ""
        # if len(relevant_memories) > 0:
        #     long_memory = "\n".join([
        #         f"• {m}" for m in relevant_memories
        #     ])
        # ------------------------------------------------------------

        # 构建上下文：倒序加入历史，直到接近长度限制
        bunny_prompt = {
            "role": "system",
            "content": constants.BUNNY_PROMPT
        }
        # 把记忆拼进 system prompt 检索记忆移到 agent 工具中
        # if len(long_memory) > 0:
        #     bunny_prompt["content"] += f"\n\n你可以参考的你与用户的长期记忆：\n{long_memory}"

        # ====================== 【最小改动：插入 fact_answer】 ======================
        messages = [bunny_prompt] + self.chat_history + [
            {"role": "user", "content": f"请根据这个信息自然口语化回复我：{fact_answer}"}
        ]
        # ==========================================================================

        print(f"messages {messages}")
        res = requests.post(
            url=self.silicon_chat_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {constants.SILICONFLOW_API_KEY}"
            },
            json={
                "model": self.silicon_chat_model,
                "messages": messages,
                "enable_thinking": False
            }
        )
        print(f"res.json() {res.json()["choices"][0]["message"]["content"]}")
        reply = ""
        if res.json()["choices"]:
            reply = res.json()["choices"][0]["message"]["content"].strip()
        
        if len(reply) <= 0:
            return None

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

        if len(self.chat_history) > 20:
            # 归档 0 到 -20 的所有记录
            self.archive_chat_range(0, -10)
            
            # 只保留最后 20 条
            self.chat_history = self.chat_history[-10:]

        self._play_notification_sound()
        return reply

    # ---------- 2. 长期记忆（向量化） ----------
    def init_memory_db(self):
        """初始化 SQLite（用户/助手两张表）+ FAISS 索引（两个文件）"""
        self.db_path = os.path.join(constants.DEFAULT_SAVE_DIR, "memory.db")

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # 用户记忆表
        c.execute('''CREATE TABLE IF NOT EXISTS user_memories
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        # 助手记忆表
        c.execute('''CREATE TABLE IF NOT EXISTS assistant_memories
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()

        # FAISS 索引文件
        self.user_index_path = os.path.join(constants.DEFAULT_SAVE_DIR, "memory_user.index")
        self.assistant_index_path = os.path.join(constants.DEFAULT_SAVE_DIR, "memory_assistant.index")
        self.embedding_dim = 1024

        # 加载或创建用户索引
        if os.path.exists(self.user_index_path):
            self.user_index = faiss.read_index(self.user_index_path)
        else:
            base_index = faiss.IndexFlatL2(self.embedding_dim)
            self.user_index = faiss.IndexIDMap(base_index)

        # 加载或创建助手索引
        if os.path.exists(self.assistant_index_path):
            self.assistant_index = faiss.read_index(self.assistant_index_path)
        else:
            base_index = faiss.IndexFlatL2(self.embedding_dim)
            self.assistant_index = faiss.IndexIDMap(base_index)

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
        总结对话，输出带角色标签的记忆列表
        返回： [ {"role": "user", "summary": "xxx"}, {"role": "assistant", "summary": "yyy"}, ... ]
        """
        chat_text = "\n".join([
            f"{m['role']}: {m['content']}" for m in chat_history
        ])

        prompt = """请把以下对话内容，提炼成多条独立、简短、客观的事实。
    每条事实必须明确指出是关于“用户”还是“助手”的。
    格式：每行一条，开头用 [user] 或 [assistant] 标记角色，然后写一句话事实（不超过20字），不要序号。

    只提取用户和助手的：习惯、偏好、禁忌、特点。
    不同主题分开成多条，最多输出5条。

    对话内容：
    {chat_text}

    请输出（每行一条记忆）：
    """.format(chat_text=chat_text)

        try:
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
                            "content": prompt
                        }
                    ],
                    "enable_thinking": False
                }
            )
            response_text = res.json()["choices"][0]["message"]["content"]

            # 解析成结构化列表
            memory_list = []
            for line in response_text.split("\n"):
                line = line.strip()
                if line.startswith("[user]"):
                    role = "user"
                    summary = line[6:].strip()
                elif line.startswith("[assistant]"):
                    role = "assistant"
                    summary = line[11:].strip()
                else:
                    continue  # 跳过不符合格式的行

                if len(summary) > 4:
                    memory_list.append({"role": role, "summary": summary})

            return memory_list
        except Exception as e:
            print(f"总结记忆失败: {e}")
            return []

    def save_memory(self, summary: str, role: str) -> int:
        """
        保存一条记忆到对应角色的表和 FAISS 索引
        role: 'user' 或 'assistant'
        返回：自增 ID
        """
        if role not in ("user", "assistant"):
            raise ValueError("role 必须是 'user' 或 'assistant'")

        table = f"{role}_memories"
        index = self.user_index if role == "user" else self.assistant_index
        index_path = self.user_index_path if role == "user" else self.assistant_index_path

        # 1. 插入 SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(f"INSERT INTO {table} (summary) VALUES (?)", (summary,))
        memory_id = c.lastrowid
        conn.commit()
        conn.close()

        # 2. 生成向量并存入 FAISS
        emb = self.get_embedding(summary)
        emb_np = np.array([emb], dtype=np.float32)
        index.add_with_ids(emb_np, np.array([memory_id], dtype=np.int64))
        faiss.write_index(index, index_path)

        print(f"保存 {role} 记忆: {summary.strip()}")
        return memory_id

    def retrieve_memory(self, query: str, top_k=5, role=None) -> list:
        """
        检索最相关的长期记忆，可按角色过滤。
        role: None 表示两者都检索，'user' 或 'assistant' 单独检索。
        返回：摘要文本列表（已合并并去重）
        """
        def search_index(index, conn, table, query_emb, k):
            """在单个索引上搜索，返回摘要列表"""
            if index.ntotal == 0:
                return []
            distances, ids = index.search(np.array([query_emb], dtype=np.float32), k)
            memories = []
            for mem_id, dist in zip(ids[0], distances[0]):
                if mem_id <= 0:
                    continue
                c = conn.cursor()
                c.execute(f"SELECT summary FROM {table} WHERE id=?", (int(mem_id),))
                res = c.fetchone()
                if res:
                    memories.append(res[0])
            return memories

        query_emb = self.get_embedding(query)
        conn = sqlite3.connect(self.db_path)

        results = []
        if role in (None, "user"):
            results.extend(search_index(self.user_index, conn, "user_memories", query_emb, top_k))
        if role in (None, "assistant"):
            results.extend(search_index(self.assistant_index, conn, "assistant_memories", query_emb, top_k))

        conn.close()
        # 去重（相同文本可能被重复保存）
        return list(dict.fromkeys(results))

    def auto_save_memory(self):
        """自动总结对话 → 按角色分别保存多条记忆"""
        if len(self.embedding_chat_history) >= 6:
            chat_history_to_be_saved = self.embedding_chat_history
            self.embedding_chat_history = []

            memory_list = self.summarize_chat(chat_history_to_be_saved)
            for mem in memory_list:
                if mem["summary"].strip():
                    self.save_memory(mem["summary"].strip(), mem["role"])
            print(f"当前用户记忆: {self.list_all_memories(role='user')}")
            print(f"当前助手记忆: {self.list_all_memories(role='assistant')}")
            return memory_list
        return None

    def quit_save_memory(self):
        """退出时保存剩余记忆"""
        if len(self.embedding_chat_history) > 0:
            memory_list = self.summarize_chat(self.embedding_chat_history)
            for mem in memory_list:
                if mem["summary"].strip():
                    self.save_memory(mem["summary"].strip(), mem["role"])
            self.embedding_chat_history = []
            return memory_list
        return None

    def list_all_memories(self, role=None) -> dict:
        """
        列出长期记忆，可按角色过滤。
        返回：{"user": [...], "assistant": [...]} 或单个角色的列表
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            def fetch_memories(table):
                c = conn.cursor()
                c.execute(f"SELECT id, summary, created_at FROM {table} ORDER BY created_at DESC")
                rows = c.fetchall()
                return [{"id": row["id"], "summary": row["summary"], "created_at": row["created_at"]} for row in rows]

            if role == "user":
                result = fetch_memories("user_memories")
            elif role == "assistant":
                result = fetch_memories("assistant_memories")
            else:
                result = {
                    "user": fetch_memories("user_memories"),
                    "assistant": fetch_memories("assistant_memories")
                }
            conn.close()
            return result
        except Exception as e:
            print(f"读取记忆失败: {e}")
            return {} if role is None else []
    
    # ---------- 3. 模型交互部分 重构 ----------
    def send_request_to_chat_model(self, user_input: str) -> str:
        """
        与聊天模型对话，给出历史对话文件
        Returns: 模型回复
        """
        # 拼接完整提示词
        messages = [{
            "role": "user",
            "content": user_input
        }]
        print(f"messages {messages}")
        res = requests.post(
            url=self.silicon_chat_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {constants.SILICONFLOW_API_KEY}"
            },
            json={
                "model": self.silicon_chat_model,
                "messages": messages,
                "enable_thinking": False
            }
        )
        print(f"res.json() {res.json()["choices"][0]["message"]["content"]}")
        reply = ""
        if res.json()["choices"]:
            reply = res.json()["choices"][0]["message"]["content"].strip()
        
        if len(reply) <= 0:
            return None

        return reply


class ReActAgent:
    def __init__(self, model_manager: ModelManager, tool_executor: ToolExecutor, max_steps: int = 5):
        self.model_manager = model_manager
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.history = []

    def run(self, question: str):
        """
        运行ReAct智能体来回答一个问题。
        """
        self.history = [] # 每次运行时重置历史记录
        current_step = 0

        while current_step < self.max_steps:
            current_step += 1
            print(f"--- 第 {current_step} 步 ---")

            # 1. 格式化提示词
            tools_desc = self.tool_executor.getAvailableTools()
            history_str = "\n".join(self.history)
            prompt = constants.REACT_PROMPT_TEMPLATE.format(
                tools=tools_desc,
                question=question,
                history=history_str
            )

            # 2. 调用LLM进行思考
            response_text = self.model_manager.send_request_to_chat_model(prompt)
            
            if not response_text:
                print("错误:LLM未能返回有效响应。")
                break

            # (这段逻辑在 run 方法的 while 循环内)
            # 3. 解析LLM的输出
            thought, action = self._parse_output(response_text)
            
            if thought:
                print(f"思考: {thought}")

            if not action:
                print("警告:未能解析出有效的Action，流程终止。")
                break

            # 4. 执行Action
            if action.startswith("Finish"):
                # 如果是Finish指令，提取最终答案并结束
                final_answer = re.match(r"Finish\[(.*)\]", action).group(1)
                print(f"最终答案: {final_answer}")
                return final_answer
            
            tool_name, tool_input = self._parse_action(action)
            if not tool_name and not tool_input:
                # ... 处理无效Action格式 ...
                continue

            print(f"行动: {tool_name}[{tool_input}]")
            
            tool_function = self.tool_executor.getTool(tool_name)
            if not tool_function:
                observation = f"错误:未找到名为 '{tool_name}' 的工具。"
            else:
                observation = tool_function(tool_input) # 调用真实工具
            # (这段逻辑紧随工具调用之后，在 while 循环的末尾)
            print(f"观察: {observation}")
            
            # 将本轮的Action和Observation添加到历史记录中
            self.history.append(f"Action: {action}")
            self.history.append(f"Observation: {observation}")

        # 循环结束
        print("已达到最大步数，流程终止。")
        return None

    # (这些方法是 ReActAgent 类的一部分)
    def _parse_output(self, text: str):
        """解析LLM的输出，提取Thought和Action。
        """
        # Thought: 匹配到 Action: 或文本末尾
        thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", text, re.DOTALL)
        # Action: 匹配到文本末尾
        action_match = re.search(r"Action:\s*(.*?)$", text, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        return thought, action

    def _parse_action(self, action_text: str):
        """解析Action字符串，提取工具名称和输入。
        """
        match = re.match(r"(\w+)\[(.*)\]", action_text, re.DOTALL)
        if match:
            return match.group(1), match.group(2)
        return None, None