import threading
import hashlib
import re
import html
import tempfile
import tkinter.scrolledtext as scrolledtext
import tkinter as tk
from constants import constants
import ctypes
from PIL import Image, ImageTk
import sys
import os
import logging

try:
    from tkhtmlview import HTMLScrolledText
except ImportError:
    HTMLScrolledText = None

try:
    import markdown2
    def _markdown_to_html(text):
        return markdown2.markdown(text, extras=["fenced-code-blocks", "tables", "strike", "cuddled-lists"])
except ImportError:
    def _markdown_to_html(text):
        return "<pre>{}</pre>".format(html.escape(text))

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except Exception:
    plt = None
    HAS_MATPLOTLIB = False


class ChatWindow:
    def __init__(self, master, world):
        self.master = master
        self.world = world
        self.is_waiting_reply = False
        self.logger = logging.getLogger(__name__)

        # ========== 新增：让任务栏图标生效（Windows 专属） ==========
        myappid = "mycompany.myproduct.bunny"  # 随便写个唯一字符串
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

        # 窗口设置
        master.title("交互")
        master.geometry("1100x950")
        master.resizable(True, True)
        master.minsize(500, 600)
        master.configure(bg="#ffffff")

        # ========== 替换：同时设置窗口+任务栏图标 ==========
        # 假设 constants.BUNNY_ICON 是 .ico 或 .png 文件路径
        try:
            icon_ico_path = constants.BUNNY_ICON_ICO
            if getattr(sys, "frozen", False):
                icon_ico_path = os.path.join(sys._MEIPASS, constants.BUNNY_ICON_ICO)
            img = Image.open(icon_ico_path)
            icon = ImageTk.PhotoImage(img)
            master.iconphoto(True, icon)  # True = 同时影响任务栏
            self.master.icon = icon  # 防止被垃圾回收
        except Exception as e:
            self.logger.error(f"图标加载失败: {e}")

        # 确保 ModelManager 存在
        if not hasattr(self.world, 'model_manager'):
            from manager.model_manager import ModelManager
            self.world.model_manager = ModelManager(self.world.tool_executor)
        self.model_manager = self.world.model_manager

        # ====================== 关键修改：使用 grid 布局，固定输入框高度 ======================
        # 主网格布局：行0 = 聊天区 | 行1 = 输入框区（固定高度）
        main_container = tk.Frame(master, bg="#ffffff")
        main_container.pack(fill=tk.BOTH, expand=True)

        # 行配置：聊天区(0)可伸缩，输入框区(1)固定高度、不收缩、不拉伸
        main_container.rowconfigure(0, weight=1)    # 聊天区：自动伸缩
        main_container.rowconfigure(1, weight=0)    # 输入框：固定，不伸缩
        main_container.columnconfigure(0, weight=1)

        # -------------------- 顶部标题栏 --------------------
        title_frame = tk.Frame(main_container, bg="#f5f5f5", height=60)
        title_frame.grid(row=0, column=0, sticky="nwe")
        title_frame.grid_propagate(False)
        
        title_label = tk.Label(
            title_frame, text="Bunny", font=("Microsoft YaHei UI", 16, "bold"),
            bg="#f5f5f5", fg="#000000"
        )
        title_label.pack(side=tk.LEFT, padx=20)

        # -------------------- 聊天记录区域 --------------------
        self.temp_dir = tempfile.mkdtemp(prefix='chat_html_')
        if HTMLScrolledText is not None:
            self.chat_display = HTMLScrolledText(
                main_container, html="", wrap=tk.WORD, state='disabled', font=("Microsoft YaHei UI", 12),
                padx=15, pady=15, relief=tk.FLAT, background="#f5f5f5", highlightthickness=0
            )
            self.chat_display.grid(row=0, column=0, sticky="nsew", pady=(48,0))
            self.use_html_display = True
            self.chat_html = []
        else:
            self.chat_display = scrolledtext.ScrolledText(
                main_container, wrap=tk.WORD, state='disabled', font=("Microsoft YaHei UI", 12),
                padx=15, pady=15, relief=tk.FLAT, bg="#f5f5f5"
            )
            self.chat_display.grid(row=0, column=0, sticky="nsew", pady=(48,0))
            self.use_html_display = False
            self.chat_html = []

        # -------------------- 底部输入区域（固定高度，绝不缩小） --------------------
        INPUT_FRAME_HEIGHT = 140  # 输入框总高度，固定值
        bottom_frame = tk.Frame(main_container, bg="#ffffff", height=INPUT_FRAME_HEIGHT)
        bottom_frame.grid(row=1, column=0, sticky="ew")  # 只允许左右拉伸，高度固定
        bottom_frame.grid_propagate(False)  # 关键：强制保持高度，不被子元素压缩

        # 输入框工具栏
        tool_frame = tk.Frame(bottom_frame, bg="#ffffff", height=30)
        tool_frame.pack(fill=tk.X, padx=15)
        tool_frame.pack_propagate(False)

        # 多行输入框
        self.entry = tk.Text(
            bottom_frame, font=("Microsoft YaHei UI", 12), relief=tk.FLAT,
            bg="#ffffff", height=4
        )
        self.entry.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 8))
        self.entry.bind("<Return>", self.send_message)
        self.entry.bind("<Shift-Return>", self.new_line)

        # 发送按钮
        btn_frame = tk.Frame(bottom_frame, bg="#ffffff")
        btn_frame.pack(fill=tk.X, padx=15, pady=(0, 10))
        
        # 按钮样式（正常/禁用/按下）
        self.NORMAL_BG = "#07c160"
        self.NORMAL_FG = "white"
        
        self.DISABLE_BG = "#e3e3e3"
        self.DISABLE_FG = "#e6f3ec"  # 禁用时文字也变浅灰
        
        self.PRESS_BG = "#06994d"    # 更深的绿色，绝对不会白
        self.PRESS_FG = "white"
        
        self.send_btn = tk.Button(
            btn_frame, text="发送", command=self.send_message,
            font=("Microsoft YaHei UI", 10), width=10,
            relief=tk.FLAT,     # 纯扁平
            bd=0,               # 无边框
            highlightthickness=0,  # 无高亮边框
            bg=self.DISABLE_BG,
            fg=self.DISABLE_FG,
            state=tk.DISABLED,
            # 关键修复：强制覆盖默认的按下白色
            activebackground=self.PRESS_BG,
            activeforeground=self.PRESS_FG
        )
        self.send_btn.pack(side=tk.RIGHT)

        # 绑定输入框变化 → 实时更新按钮状态
        self.entry.bind("<KeyRelease>", self.update_send_btn_status)
        
        # 简化：直接用系统自带按压效果，更稳定
        # 去掉了自定义的 press/release 绑定，避免冲突变白

        # 初始加载
        self.refresh_chat_display()

    # ====================== 发送按钮状态控制 ======================
    def update_send_btn_status(self, event=None):
        """输入框内容变化时，自动启用/禁用发送按钮"""
        content = self.entry.get("1.0", tk.END).strip()
        if content and not self.is_waiting_reply:
            self.send_btn.config(
                state=tk.NORMAL,
                bg=self.NORMAL_BG,
                fg=self.NORMAL_FG,
                activebackground=self.PRESS_BG,
                activeforeground=self.PRESS_FG,
                text="发送"
            )
        else:
            if self.is_waiting_reply:
                self.send_btn.config(
                    state=tk.DISABLED,
                    bg=self.DISABLE_BG,
                    fg=self.DISABLE_FG,
                    activebackground=self.DISABLE_BG,
                    activeforeground=self.DISABLE_FG,
                    text="兔兔思考中..."
                )
            else:
                self.send_btn.config(
                    state=tk.DISABLED,
                    bg=self.DISABLE_BG,
                    fg=self.DISABLE_FG,
                    activebackground=self.DISABLE_BG,
                    activeforeground=self.DISABLE_FG,
                    text="发送"
                )

    def _render_formula_image(self, expr):
        """Render a LaTeX formula to a PNG image and return the path."""
        if not HAS_MATPLOTLIB:
            return f"<code>{html.escape(expr)}</code>"

        expr_key = hashlib.md5(expr.encode("utf-8")).hexdigest()
        filename = f"formula_{expr_key}.png"
        output_path = os.path.join(self.temp_dir, filename)
        if not os.path.exists(output_path):
            fig = plt.figure(figsize=(0.01, 0.01))
            fig.text(0, 0, f"${expr}$", fontsize=16)
            plt.axis("off")
            fig.patch.set_alpha(0)
            plt.savefig(output_path, dpi=200, bbox_inches="tight", pad_inches=0.05, transparent=True)
            plt.close(fig)

        return output_path.replace("\\", "/")

    def _convert_markdown_and_math(self, text):
        """Convert markdown text and embedded LaTeX formulas to HTML."""
        formulas = []

        def replace_block(match):
            expr = match.group(1).strip()
            formulas.append(expr)
            return f"@@FORMULA{len(formulas) - 1}@@"

        def replace_inline(match):
            expr = match.group(1).strip()
            formulas.append(expr)
            return f"@@FORMULA{len(formulas) - 1}@@"

        text = re.sub(r"\$\$(.+?)\$\$", replace_block, text, flags=re.S)
        text = re.sub(r"\$(.+?)\$", replace_inline, text)
        html_text = _markdown_to_html(text)

        for index, expr in enumerate(formulas):
            img_path = self._render_formula_image(expr)
            if img_path.endswith(".png"):
                replacement = f"<img src=\"{img_path}\" style=\"vertical-align:middle; max-height:1.4em;\"/>"
            else:
                replacement = img_path
            html_text = html_text.replace(f"@@FORMULA{index}@@", replacement)

        return html_text

    def _format_message_html(self, sender, text):
        body_html = self._convert_markdown_and_math(text)
        return (
            "<div style=\"margin-bottom:14px;\">"
            f"<p style=\"margin:0 0 8px 0;font-weight:bold;color:#1A3E72;\">{html.escape(sender)}</p>"
            f"<div>{body_html}</div>"
            "</div>"
        )

    def _wrap_chat_html(self):
        return (
            "<div style=\"background:#f5f5f5; padding:12px; margin:0; font-family: 'Microsoft YaHei UI', sans-serif;\">"
            + "".join(self.chat_html)
            + "</div>"
        )

    # ====================== 原有功能不变 ======================
    def new_line(self, event):
        """Shift+回车换行"""
        self.entry.insert(tk.END, "\n")
        return "break"

    def send_message(self, event=None):
        """发送消息（支持回车发送）"""
        if self.send_btn.cget('state') == tk.DISABLED:
            return "break"
        
        if event:
            if not self.entry.get("1.0", tk.END).strip():
                return "break"
        
        user_text = self.entry.get("1.0", tk.END).strip()
        if not user_text:
            return
        
        self.entry.delete("1.0", tk.END)
        self.update_send_btn_status()
        self._append_message("我", user_text)
        threading.Thread(target=self._get_bot_reply, args=(user_text,), daemon=True).start()
        return "break"

    def _get_bot_reply(self, user_text):
        self.is_waiting_reply = True
        self.update_send_btn_status()
        try:
            reply = self.model_manager.chat(user_text)
            self.master.after(0, self._append_message, "Alice", reply)
        except Exception as e:
            reply = f"（聊天出错：{e}）"
        self.is_waiting_reply = False
        self.update_send_btn_status()

    def _append_message(self, sender, text):
        """消息展示优化：支持 HTML/Markdown 与数学公式渲染"""
        self.chat_display.config(state='normal')
        if self.use_html_display:
            if self.chat_display.get("1.0", tk.END).strip():
                self.chat_html.append("<hr style='border:none;border-top:1px solid #e0e0e0;margin:10px 0;'>")
            self.chat_html.append(self._format_message_html(sender, text))
            self.chat_display.set_html(self._wrap_chat_html())
        else:
            if self.chat_display.get("1.0", tk.END).strip():
                self.chat_display.insert(tk.END, "\n")
            self.chat_display.insert(tk.END, f"{sender}：{text}\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)

    def refresh_chat_display(self):
        self.chat_display.config(state='normal')
        if self.use_html_display:
            self.chat_html = []
            for msg in self.model_manager.chat_history:
                sender = "我" if msg["role"] == "user" else "Alice"
                if self.chat_html:
                    self.chat_html.append("<hr style='border:none;border-top:1px solid #e0e0e0;margin:10px 0;'>")
                self.chat_html.append(self._format_message_html(sender, msg["content"]))
            self.chat_display.set_html(self._wrap_chat_html())
        else:
            self.chat_display.delete("1.0", tk.END)
            for msg in self.model_manager.chat_history:
                sender = "我" if msg["role"] == "user" else "Alice"
                self.chat_display.insert(tk.END, f"{sender}：{msg['content']}\n")
        self.chat_display.config(state='disabled')
