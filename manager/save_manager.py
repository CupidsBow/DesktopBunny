import json
import base64
import os
from constants import constants

class SaveManager:
    """本地存档管理器，存档固定保存在 C 盘用户应用数据目录"""

    def __init__(self, filename: str = "bunny_save.dat", xor_key: int = 0x5A):
        """
        Args:
            filename: 存档文件名
            xor_key:  XOR 混淆密钥 (0-255)
        """
        # 确保文件夹存在
        os.makedirs(constants.DEFAULT_SAVE_DIR, exist_ok=True)
        self.save_path = os.path.join(constants.DEFAULT_SAVE_DIR, filename)
        self._xor_key = xor_key

    def save(self, data: dict) -> None:
        """保存数据到本地"""
        json_str = json.dumps(data, ensure_ascii=False)
        obfuscated = bytes([b ^ self._xor_key for b in json_str.encode('utf-8')])
        encoded = base64.b64encode(obfuscated)
        with open(self.save_path, 'wb') as f:
            f.write(encoded)

    def load(self) -> dict:
        """加载本地存档，如果文件不存在或损坏则返回空字典"""
        if not os.path.exists(self.save_path):
            return {}

        try:
            with open(self.save_path, 'rb') as f:
                encoded = f.read()
            obfuscated = base64.b64decode(encoded)
            json_str = bytes([b ^ self._xor_key for b in obfuscated]).decode('utf-8')
            return json.loads(json_str)
        except Exception:
            return {}

    def delete(self) -> None:
        """删除存档文件"""
        if os.path.exists(self.save_path):
            os.remove(self.save_path)