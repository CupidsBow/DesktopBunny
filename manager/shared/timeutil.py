"""时间、ISO 转换、衰减与钳制。

大脑内部统一使用带时区的本地时间；落盘用 ISO8601 字符串，闹钟用浮点时间戳。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Sequence


def now_local() -> datetime:
    """当前本地时间（带时区）。"""
    return datetime.now().astimezone()


def to_iso(value: datetime) -> str:
    """转成秒级 ISO8601；保留时区。"""
    return value.isoformat(timespec="seconds")


def parse_iso(value: object) -> datetime | None:
    """解析 ISO8601；naive 时间按本地时区处理。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def to_timestamp(value: datetime) -> float:
    return value.timestamp()


def from_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value).astimezone()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def decay_factor(age_seconds: float, half_life: float) -> float:
    """指数衰减：半衰期后为 0.5。"""
    if half_life <= 0:
        return 1.0
    return 0.5 ** (max(0.0, age_seconds) / half_life)


def circadian_value(hour: float, curve: Sequence[tuple[float, float]]) -> float:
    """按控制点线性插值昼夜基线；``hour`` 为 0~24 的浮点数。"""
    if not curve:
        return 0.5
    hour = hour % 24.0
    points = sorted((float(h), float(v)) for h, v in curve)
    for (h1, v1), (h2, v2) in zip(points, points[1:]):
        if h1 <= hour <= h2:
            if h2 == h1:
                return v2
            ratio = (hour - h1) / (h2 - h1)
            return v1 + (v2 - v1) * ratio
    if hour < points[0][0]:
        # 24 点回绕到第一个控制点
        h1, v1 = points[-1]
        h2, v2 = points[0][0] + 24.0, points[0][1]
        ratio = (hour + 24.0 - h1) / (h2 - h1)
        return v1 + (v2 - v1) * ratio
    return points[-1][1]


def next_occurrence(hhmm: str, now: datetime) -> datetime:
    """返回 ``HH:MM`` 的下一次本地时刻；格式非法时退化为 24 小时后。"""
    try:
        hour_text, minute_text = str(hhmm).strip().split(":", 1)
        hour = int(hour_text) % 24
        minute = int(minute_text) % 60
    except (ValueError, AttributeError):
        return now + timedelta(days=1)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def parse_time_arg(spec: dict, now: datetime) -> datetime | None:
    """解析条件闹钟的 ``at_time`` 参数。"""
    raw = spec.get("time")
    if not raw:
        return None
    text = str(raw)
    if "T" in text or "-" in text:
        parsed = parse_iso(text)
        if parsed is not None:
            if parsed <= now and spec.get("repeat") == "once":
                return parsed
            if parsed <= now and "repeat" not in spec:
                return parsed
            return parsed
    return next_occurrence(text, now)


def is_night(value: datetime, start_hour: int = 23, end_hour: int = 7) -> bool:
    hour = value.hour
    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def age_seconds(then: datetime | None, now: datetime) -> float:
    if then is None:
        return 0.0
    return max(0.0, (now - then).total_seconds())


def join_lines(items: Iterable[object]) -> str:
    return "\n".join(str(item) for item in items if str(item).strip())
