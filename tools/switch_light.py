import win32com.client
import time
import logging


ORDER_PREFIX = "天猫精灵"
TURN_ON_THE_LIGHT_ORDER = "帮我开灯"
TURN_OFF_THE_LIGHT_ORDER = "帮我关灯"

logger = logging.getLogger(__name__)

def turn_on_the_light(value = "on") -> str:
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    speaker.Speak(ORDER_PREFIX)
    time.sleep(1)
    speaker.Speak(TURN_ON_THE_LIGHT_ORDER)
    logger.info("调用 turn_on_the_light 开灯成功")
    return "开灯成功"

def turn_off_the_light(value = "off") -> str:
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    speaker.Speak(ORDER_PREFIX)
    time.sleep(1)
    speaker.Speak(TURN_OFF_THE_LIGHT_ORDER)
    logger.info("调用 turn_off_the_light 关灯成功")
    return "关灯成功"
