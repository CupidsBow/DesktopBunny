import serial
import time
import logging

COM_PORT = "COM8"
BAUD = 9600
PARITY = serial.PARITY_NONE
DATABITS = 8
STOPBITS = 1
RX_TIMEOUT = 1  # 串口读取超时1秒
CMD_SEND_CODE = "E3{code}" # E3XX：发射编号XX红外码，示例发01号 E301
CMD_TEST = "E4"        # E4：模块自检，IR快闪3下，返回E4

logger = logging.getLogger(__name__)

class IRSerial:
    def __init__(self):
        self.ser = None
        self.open_serial()

    def open_serial(self):
        """打开COM8串口"""
        try:
            self.ser = serial.Serial(
                port=COM_PORT,
                baudrate=BAUD,
                bytesize=DATABITS,
                parity=PARITY,
                stopbits=STOPBITS,
                timeout=RX_TIMEOUT
            )
            if self.ser.is_open:
                logger.info(f"串口 {COM_PORT} 打开成功，波特率{BAUD}")
        except Exception as e:
            logger.error(f"串口打开失败:{e}\n检查COM口、接线、占用")
            exit(1)

    def send_hex_cmd(self, hex_str: str):
        """发送16进制指令，自动转字节，返回模块应答bytes"""
        if not self.ser or not self.ser.is_open:
            return b""
        # 16进制字符串转字节流
        send_bytes = bytes.fromhex(hex_str.strip())
        self.ser.write(send_bytes)
        logger.info(f"[发送HEX] {hex_str} → {send_bytes.hex(' ')}")
        time.sleep(0.2) # 等待模块应答
        recv_data = self.ser.read(self.ser.in_waiting)
        if len(recv_data) > 0:
            logger.info(f"[模块回复] {recv_data.hex(' ')}")
        else:
            logger.info("[模块回复] 无数据")
        return recv_data

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info("串口已关闭")

def switch_on_air_conditioner(value: str):
    ir = IRSerial()
    try:
        ir.send_hex_cmd(CMD_TEST)
        time.sleep(0.2)
        ir.send_hex_cmd(CMD_SEND_CODE.format(code="01")) # 发射01号红外码，打开空调
    except Exception as e:
        logger.error(f"发送红外信号失败: {e}")
        return False
    finally:
        ir.close()
    return True

def switch_off_air_conditioner(value: str):
    ir = IRSerial()
    try:
        ir.send_hex_cmd(CMD_TEST)
        time.sleep(0.2)
        ir.send_hex_cmd(CMD_SEND_CODE.format(code="02")) # 发射02号红外码，关闭空调
    except Exception as e:
        logger.error(f"发送红外信号失败: {e}")
        return False
    finally:
        ir.close()
    return True