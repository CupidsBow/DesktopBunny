"""自我状态：从身体与记忆中派生心情、态度、欲望。

全部是只读派生值，不落盘，也不触发行为。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Mapping

from manager.shared.config import BrainConfig, RelationThresholds
from manager.shared.timeutil import decay_factor, parse_iso
from manager.shared.types import (
    Attitude,
    BodyState,
    Episode,
    MemoryView,
    RelationLevel,
    SelfState,
    Urge,
)

logger = logging.getLogger(__name__)


class SelfStateDeriver:
    """把身体量、最近情景与说话人汇总成一份输入视图。"""

    def __init__(self, config: BrainConfig) -> None:
        self.config = config
        self.thresholds: RelationThresholds = config.relation_thresholds

    def derive(
        self,
        body: BodyState,
        memory_view: MemoryView | None,
        episodes: Iterable[Episode],
        now: datetime,
        *,
        at_night: bool = False,
    ) -> SelfState:
        episodes = list(episodes or [])
        mood = self._mood(episodes, now)
        attitudes = self._attitudes(episodes, now)
        urges = self._urges(body, attitudes, episodes, now, at_night)
        return SelfState(
            mood=round(mood, 3),
            mood_label=self._mood_label(mood),
            urges=urges,
            attitudes=attitudes,
        )

    # ------------------------------------------------------------------
    # 心情
    # ------------------------------------------------------------------
    def _mood(self, episodes: list[Episode], now: datetime) -> float:
        total_weight = 0.0
        weighted = 0.0
        for episode in episodes:
            when = parse_iso(episode.metadata.time)
            if when is None:
                continue
            age = max(0.0, (now - when).total_seconds())
            if age > self.config.mood_window:
                continue
            weight = decay_factor(age, self.config.mood_half_life)
            weighted += episode.metadata.valence * weight
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return max(-1.0, min(1.0, weighted / total_weight))

    @staticmethod
    def _mood_label(value: float) -> str:
        if value >= 0.5:
            return "兴奋"
        if value >= 0.15:
            return "轻快"
        if value > -0.15:
            return "平静"
        if value > -0.5:
            return "低落"
        return "烦躁"

    # ------------------------------------------------------------------
    # 态度
    # ------------------------------------------------------------------
    def _attitudes(
        self, episodes: list[Episode], now: datetime
    ) -> dict[str, Attitude]:
        buckets: dict[str, list[tuple[float, float, float]]] = {}
        for episode in episodes:
            speaker = episode.metadata.speaker
            if not speaker:
                continue
            when = parse_iso(episode.metadata.time)
            if when is None:
                continue
            age = max(0.0, (now - when).total_seconds())
            weight = decay_factor(age, self.config.attitude_half_life)
            intensity = abs(episode.metadata.valence) * (
                0.5 + episode.metadata.arousal
            )
            buckets.setdefault(speaker, []).append(
                (
                    episode.metadata.valence,
                    intensity * weight,
                    weight,
                )
            )

        result: dict[str, Attitude] = {}
        for friend_id, items in buckets.items():
            total_weight = sum(item[1] for item in items)
            if total_weight <= 0:
                continue
            value = sum(item[0] * item[1] for item in items) / total_weight
            confidence = total_weight
            result[friend_id] = Attitude(
                friend_id=friend_id,
                value=round(max(-1.0, min(1.0, value)), 3),
                confidence=round(confidence, 3),
                relation=self._relation(round(value, 3), confidence),
            )
        return result

    def _relation(self, value: float, confidence: float) -> RelationLevel:
        t = self.thresholds
        if confidence < t.min_confidence:
            return RelationLevel.STRANGER
        if value < t.distant_below:
            return RelationLevel.DISTANT
        if value < t.familiar_from:
            return RelationLevel.ACQUAINTANCE
        if value < t.close_from:
            return RelationLevel.FAMILIAR
        if value < t.intimate_from:
            return RelationLevel.CLOSE
        return RelationLevel.INTIMATE

    # ------------------------------------------------------------------
    # 欲望
    # ------------------------------------------------------------------
    def _urges(
        self,
        body: BodyState,
        attitudes: Mapping[str, Attitude],
        episodes: list[Episode],
        now: datetime,
        at_night: bool,
    ) -> list[Urge]:
        urges: list[Urge] = []

        hunger = max(0.0, min(1.0, (80.0 - body.satiety) / 80.0))
        urges.append(
            Urge(
                name="饥饿",
                level=round(hunger, 3),
                reason=f"饱食度 {body.satiety:.0f}",
            )
        )

        lonely = max(0.0, min(1.0, 1.0 - body.social_battery))
        urges.append(
            Urge(
                name="孤独",
                level=round(lonely, 3),
                reason=f"社交电量 {body.social_battery:.2f}",
            )
        )

        sleepiness = (
            (1.0 - body.energy) * 0.6
            + (1.0 - body.circadian) * 0.4
            + (0.2 if at_night else 0.0)
        )
        urges.append(
            Urge(
                name="睡意",
                level=round(max(0.0, min(1.0, sleepiness)), 3),
                reason=(
                    f"精力 {body.energy:.2f}、昼夜 {body.circadian:.2f}"
                    + ("、夜深" if at_night else "")
                ),
            )
        )

        missing = self._missing_friend(attitudes, episodes, now)
        if missing is not None:
            urges.append(missing)

        share = self._share_urge(episodes, now)
        if share is not None:
            urges.append(share)

        hour = now.hour
        if 6 <= hour < 10:
            daily_level, daily_reason = 0.5, "早上"
        elif 21 <= hour < 24:
            daily_level, daily_reason = 0.4, "夜里"
        else:
            daily_level, daily_reason = 0.05, "日常时段"
        urges.append(Urge(name="日常", level=daily_level, reason=daily_reason))

        return urges

    def _missing_friend(
        self,
        attitudes: Mapping[str, Attitude],
        episodes: list[Episode],
        now: datetime,
    ) -> Urge | None:
        last_seen: dict[str, datetime] = {}
        for episode in episodes:
            speaker = episode.metadata.speaker
            when = parse_iso(episode.metadata.time)
            if not speaker or when is None:
                continue
            if speaker not in last_seen or when > last_seen[speaker]:
                last_seen[speaker] = when

        best: Urge | None = None
        for friend_id, attitude in attitudes.items():
            if attitude.relation in (RelationLevel.STRANGER, RelationLevel.DISTANT):
                continue
            seen = last_seen.get(friend_id)
            if seen is None:
                continue
            age = max(0.0, (now - seen).total_seconds())
            if age < 24 * 3600:
                continue
            level = max(0.0, min(1.0, age / (7 * 86400.0)))
            if best is None or level > best.level:
                best = Urge(
                    name="想念",
                    level=round(level, 3),
                    reason=f"已经 {age / 3600.0:.0f} 小时没见到 {friend_id}",
                    target=friend_id,
                )
        return best

    def _share_urge(
        self, episodes: list[Episode], now: datetime
    ) -> Urge | None:
        for episode in reversed(episodes):
            when = parse_iso(episode.metadata.time)
            if when is None:
                continue
            age = max(0.0, (now - when).total_seconds())
            if age > 2 * 3600:
                continue
            salience = abs(episode.metadata.valence) * (
                0.5 + episode.metadata.arousal
            )
            if salience >= 0.5:
                return Urge(
                    name="分享",
                    level=round(min(1.0, salience), 3),
                    reason="最近有值得说的事",
                )
        return None
