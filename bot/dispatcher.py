import logging

from bot import bridge
from manager.chat_manager import ChatError, ChatManager

logger = logging.getLogger(__name__)

_help = (
    "你好呀，我是Alice，一起来聊天呀\n"
    "可用命令：\n"

    "/echo <text>  让屏幕上的兔兔Alice说一句话\n"
    "/feed <name>  喂兔兔（+20 饱食度）\n"
    "/status       查看所有兔兔的状态\n"
    "/help         显示本帮助"
)

_chat = ChatManager()


def handle(text: str) -> tuple:
    """处理一条 QQ 消息文本，返回 (回复文案, msg_type)。

    msg_type 0 = 文本，2 = Markdown（模型输出用 Markdown 渲染）。
    """
    text = text.strip()
    if not text:
        return "", 0

    lower = text.lower()

    if lower == "/help":
        return _help, 0

    if lower.startswith("/echo "):
        content = text[6:].strip()
        if not content:
            return "echo 后面要跟内容，例如：echo 你好", 0
        bridge.enqueue_command({"type": "echo", "text": content})
        return f"已让兔兔说：{content}", 0

    if lower.startswith("/feed "):
        name = text[6:].strip()
        if not name:
            return "feed 后面要跟名字，例如：feed Alice", 0
        bridge.enqueue_command({"type": "feed", "name": name})
        return f"喂了 {name} 一根胡萝卜~", 0

    if lower == "/status":
        bunnies = bridge.get_bunnies_snapshot()
        if not bunnies:
            return "现在还没有兔兔哦", 0
        lines = ["兔兔状态："]
        for bunny in bunnies:
            lines.append(f"- {bunny['name']}: 饱食度 {int(bunny['satiety'])}")
        return "\n".join(lines), 0

    try:
        return _chat.chat(text, raise_on_error=True), 2
    except ChatError as e:
        logger.error(f"调用模型失败: {e}")
        return f"（调用模型失败）{e}", 0
