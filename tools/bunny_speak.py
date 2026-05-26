import subprocess

def bunny_speak(text):
    # 直接调用系统的 edge-playback 播放语音
    command = f'edge-playback --text "{text}" --voice zh-CN-XiaoyiNeural'
    subprocess.run(command, shell=True)

# zh-CN-XiaoxiaoNeural
# zh-CN-XiaoyiNeural
# zh-HK-HiuGaaiNeural
# zh-HK-HiuMaanNeural
