import datetime

def get_local_time() -> str:
    now = datetime.datetime.now()
    formatted = now.strftime("%Y-%m-%d %H:%M:%S")
    return formatted
