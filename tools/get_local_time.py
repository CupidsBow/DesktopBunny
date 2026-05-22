import datetime

def get_local_time(value: str) -> str:
    now = datetime.datetime.now()
    formatted = now.strftime("%Y-%m-%d %H:%M:%S")
    return formatted
