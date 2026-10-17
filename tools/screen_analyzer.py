import base64
import io
import cv2
import requests
from PIL import Image
from constants import constants
from datetime import datetime, timezone, timedelta
import logging


class ScreenAnalyzer:
    def __init__(self, platform_detector):
        """
        Args:
            platform_detector: PlatformDetector 实例，用于截取屏幕
        """
        self.platform_detector = platform_detector
        self.model = constants.SCREEN_ANALYZE_MODEL
        self.silicon_chat_url = constants.SILICONFLOW_CHAT_URL
        self.logger = logging.getLogger(__name__)

    # ---------- 上下文 ----------
    def _time_feeling(self) -> str:
        """按北京时间把当前时刻说成人话"""
        hour = datetime.now(timezone(timedelta(hours=8))).hour
        if hour < 6:
            return "深夜"
        if hour < 9:
            return "早上"
        if hour < 12:
            return "上午"
        if hour < 14:
            return "午后"
        if hour < 18:
            return "下午"
        if hour < 20:
            return "傍晚"
        if hour < 23:
            return "晚上"
        return "深夜"

    def _hunger_feeling(self, bunny) -> str:
        """饱食度 → 一句人话。bunny 为 None 时不带饱食度信息"""
        if bunny is None:
            return ""
        if bunny.satiety < 20:
            return "，肚子饿得咕咕叫"
        if bunny.satiety < 50:
            return "，有点饿了"
        return "，吃得饱饱的"

    def _dwell_feeling(self, dwell_seconds: float) -> str:
        if dwell_seconds < 60:
            return "刚切过来"
        return f"已经待了约 {int(dwell_seconds // 60)} 分钟"

    def _build_prompt(self, bunny, window_title, other_windows,
                      dwell_seconds, recent_quips) -> str:
        """把窗口标题等上下文拼成 prompt。

        窗口标题是这里最关键的信息源——它给出的具体名词（番剧名、项目名、
        歌名）是截图像素换不来的，所以截图只当辅助，分辨率压到 640 就够。
        """
        name = bunny.name if bunny is not None else "兔兔"
        current = window_title.strip() or "（读不到窗口标题）"
        others = "、".join(other_windows) if other_windows else "（没有其他窗口）"
        dwell = self._dwell_feeling(dwell_seconds)
        recents = ("\n".join(f"  - {q}" for q in recent_quips)
                   if recent_quips else "  - （还没说过话）")

        return f"""你是挂在主播屏幕边上的兔兔小伙伴{name}，陪主播直播。
你说话像老观众发弹幕：短、损、接得住梗。

【你现在知道的】
- 时间：{self._time_feeling()}{self._hunger_feeling(bunny)}
- 主播正在用的窗口：{current}
- 屏幕上还开着：{others}
- 主播在这个窗口：{dwell}
- 你最近刚说过：
{recents}

【说什么】
一句话，15-25字。可以吐槽、起哄、阴阳、拆台。

【必须做到】
1. 必须点名上面信息里真实存在的具体东西——窗口、软件、番剧名、歌名、项目名。
   信息不够具体时，就吐槽"主播同时在干好几件事"这件事本身。
2. 禁止出现：好认真、加油、辛苦、真棒、在忙呀、注意休息。
3. 别夸，别安慰，别解释。你是损友，不是助理。
4. 不要和你最近说过的话重复或近义。

【正例】
  - 代码写到一半去刷B站，良心不会痛吗？
  - 狐妖小红娘第15集了？进度条比你commit还长
  - VS Code开着bunny项目，B站开着番，手分得挺开啊

【反例·这样写会被丢掉】
  - 主播好认真呀，加油！
  - 我看到你打开了浏览器和代码编辑器。
  - 结合屏幕内容来看，你可能在……

只输出那一句话："""

    # ---------- 截图 ----------
    def _capture_and_encode(self, detector) -> str:
        """截图并转为 base64（压到配置的尺寸，够用就行）"""
        img = detector.capture_screen()
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        pil_img.thumbnail((
            constants.SCREEN_ANALYZE_IMAGE_MAX_SIZE,
            constants.SCREEN_ANALYZE_IMAGE_MAX_SIZE,
        ))

        buffer = io.BytesIO()
        pil_img.save(buffer, format="JPEG", quality=constants.SCREEN_ANALYZE_JPEG_QUALITY)
        return base64.b64encode(buffer.getvalue()).decode()

    # ---------- 主入口 ----------
    def analyze_screen(self, bunny, window_title="", other_windows=None,
                       dwell_seconds=0.0, recent_quips=None) -> str:
        """结合窗口标题与截图，返回一句吐槽；失败返回 None"""
        other_windows = other_windows or []
        recent_quips = recent_quips or []

        try:
            image_b64 = self._capture_and_encode(self.platform_detector)
            prompt = self._build_prompt(
                bunny, window_title, other_windows, dwell_seconds, recent_quips
            )

            body = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [{
                            'type': 'text',
                            'text': prompt,
                        }, {
                            'type': 'image_url',
                            'image_url': {
                                'url': f"data:image/jpeg;base64,{image_b64}",
                            },
                        }]
                    }
                ],
            }
            # 只有明确支持该参数的模型才带上，否则服务端会报 20015
            if constants.SCREEN_ANALYZE_ENABLE_THINKING is not None:
                body["enable_thinking"] = constants.SCREEN_ANALYZE_ENABLE_THINKING

            res = requests.post(
                url=self.silicon_chat_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {constants.SILICONFLOW_API_KEY}"
                },
                json=body,
                timeout=constants.SCREEN_ANALYZE_TIMEOUT_SECONDS
            )

            data = res.json()
            if "choices" not in data:
                self.logger.error(f"吐槽接口返回异常: {data}")
                return None

            choices = data["choices"]
            if not choices:
                return None

            reply = (choices[0]["message"]["content"] or "").strip()
            return reply or None

        except Exception as e:
            self.logger.error(f"屏幕内容分析失败: {type(e).__name__}: {e}")
            return None
