"""生命循环：低频推进身体与环境，检测显著变化，并排布唤醒闹钟。

``tick`` 推进状态、检测信号；``schedule`` 排下一次唤醒；``due`` 返回到期
唤醒理由（闹钟 / 信号 / 定期唤醒）。唤醒理由最终由 ``brain`` 转成事件并入
``event_queue``，本模块只产出理由、不写事件账本。

从旧 ``manager/mechanism`` 的状态时钟 + 信号 + 闹钟合并而来：
- 去掉 ``BodyMode`` 与 ``max_idle_seconds``（单一 ``periodic_wake_seconds`` 兜底）；
- ``WakeController`` 的角色改由 ``event_queue`` 承担，这里只做低频推进；
- ``delta`` 由调用方循环提供，实际推进时长以 ``now_fn`` 的时钟差为准，
  避免游戏循环帧率抖动污染身体演化。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Iterable

from manager.body.body import Body
from manager.environment.environment import Environment
from manager.lifecycle.alarms import AlarmStore
from manager.lifecycle.signals import SignalDetector
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import (
    Alarm,
    AlarmPredicate,
    BodyState,
    BunnySnapshot,
    EnvironmentState,
    SignalChange,
    WakeReason,
    WakeReasonKind,
)

logger = logging.getLogger(__name__)

#: 单次状态推进的 dt 上限，避免长时间停机后一次性演化过头。
_MAX_DT_SECONDS = 300.0


class Lifecycle:
    def __init__(
        self,
        config: BrainConfig,
        *,
        body: Body,
        environment: Environment,
        snapshot_getter: Callable[[], BunnySnapshot | dict | None] | None = None,
        busy_seconds_getter: Callable[[], float] | None = None,
        mesh: Any = None,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.config = config
        self.body = body
        self.environment = environment
        self.now_fn = now_fn
        self.mesh = mesh
        self._mesh_messages: list = []
        # 默认无快照；组合根（brain）注入 bot/bridge 的 BunnySnapshot 获取器。
        self._snapshot_getter = snapshot_getter or (lambda: None)
        # 默认不计忙碌；组合根（brain）注入活动/在途能力汇总的忙碌时长。
        self._busy_seconds_getter = busy_seconds_getter or (lambda: 0.0)
        self._signals = SignalDetector(config)
        self._alarms = AlarmStore(config.paths.alarms_path, config, now_fn=now_fn)
        self._alarms.load()

        self._last_tick: datetime | None = None
        self._last_periodic_at: datetime = now_fn()
        self._pending_interactions = 0
        self._pending_signals: list[WakeReason] = []

        # 初始状态：给 due()/信号一个基线，且第一次 tick 不发信号。
        snapshot = self._snapshot_getter()
        self._environment = environment.tick(snapshot)
        self.body.tick(
            self._environment,
            interactions=0,
            dt=0.0,
            bunny_snapshot=snapshot,
            busy_seconds=0.0,
        )
        self._body = body.snapshot()
        self._prev_body: BodyState | None = None
        self._prev_environment: EnvironmentState | None = None

    # ------------------------------------------------------------------
    # 状态时钟
    # ------------------------------------------------------------------
    def tick(self, delta: float = 0.0) -> list[SignalChange]:
        """推进一次身体与环境，返回本次检测到的显著变化。

        未到 ``state_tick_interval`` 时直接返回空列表，不推进状态。
        """
        now = self.now_fn()
        if self._last_tick is not None and (
            now - self._last_tick).total_seconds() < self.config.state_tick_interval:
            return []

        dt = 0.0 if self._last_tick is None else (
            now - self._last_tick).total_seconds()
        dt = max(0.0, min(dt, _MAX_DT_SECONDS))

        snapshot = self._snapshot_getter()
        prev_body = self._prev_body
        prev_environment = self._prev_environment
        environment = self.environment.tick(snapshot)
        interactions, self._pending_interactions = self._pending_interactions, 0
        busy_seconds = max(0.0, float(self._busy_seconds_getter()))
        body = self.body.tick(
            environment,
            interactions=interactions,
            dt=dt,
            bunny_snapshot=snapshot,
            busy_seconds=busy_seconds,
        )

        self._body = self.body.snapshot()
        self._environment = environment
        self._prev_body = self._body
        self._prev_environment = environment
        self._last_tick = now

        changes = self._signals.evaluate(
            prev_body, body, prev_environment, environment, now
        )
        # brain.tick 会把这些 SignalChange 转成 Event 写入 event_queue。
        for change in changes:
            self._pending_signals.append(
                WakeReason(
                    kind=WakeReasonKind.SIGNAL,
                    detail=change.detail or str(change.signal),
                )
            )
        self._drive_mesh()
        return changes

    # ------------------------------------------------------------------
    # 唤醒排布
    # ------------------------------------------------------------------
    def schedule(
        self,
        next_wake_in: float | None = None,
        wake_when: Iterable[AlarmPredicate | dict] | None = None,
    ) -> list[Alarm]:
        """排下一次唤醒：``next_wake_in`` 秒后，或某个条件满足时。"""
        self._alarms.clear_source("llm")
        now = self.now_fn()
        created: list[Alarm] = []
        if next_wake_in is not None:
            created.append(
                self._alarms.schedule_time(
                    float(next_wake_in),
                    source="llm:next_wake_in",
                    note=f"{next_wake_in}s",
                    now=now,
                )
            )
        for raw in wake_when or []:
            predicate = (
                raw if isinstance(raw, AlarmPredicate) else AlarmPredicate.from_dict(raw)
            )
            alarm = self._alarms.schedule_predicate(
                predicate, source="llm:wake_when", now=now
            )
            if alarm is not None:
                created.append(alarm)
        return created

    def due(self) -> list[WakeReason]:
        """返回到期唤醒理由：闹钟、信号，以及独立的定期唤醒。"""
        now = self.now_fn()
        reasons: list[WakeReason] = []

        # 独立的定期唤醒：机械生物的基础心跳，也是唯一的兜底唤醒，
        # 与闹钟 / 信号 / 事件无关，按自己的节奏触发。
        if (
            now - self._last_periodic_at
        ).total_seconds() >= self.config.periodic_wake_seconds:
            self._last_periodic_at = now
            reasons.append(
                WakeReason(kind=WakeReasonKind.PERIODIC, detail="定期唤醒")
            )

        for alarm in self._alarms.due(now, self._body, self._environment):
            reasons.append(
                WakeReason(
                    kind=WakeReasonKind.ALARM,
                    detail=f"alarm {alarm.id}"
                    + (f"（{alarm.note}）" if alarm.note else ""),
                )
            )
        reasons.extend(self._pending_signals)
        self._pending_signals = []
        return reasons

    # ------------------------------------------------------------------
    # brain 回写
    # ------------------------------------------------------------------
    def note_interactions(self, count: int = 1) -> None:
        """累积本轮社交互动次数，供下一次状态推进结算身体。"""
        # brain.submit_event 收到 MESSAGE 事件时调用，保证社交电量在互动时被消耗。
        self._pending_interactions += max(0, int(count))

    # ------------------------------------------------------------------
    # 本地兔子网格（2 秒状态时钟顺带推进）
    # ------------------------------------------------------------------
    def _drive_mesh(self) -> None:
        if self.mesh is None:
            return
        try:
            self.mesh.heartbeat()
        except Exception:
            logger.exception("mesh 心跳失败")
        try:
            self._mesh_messages.extend(self.mesh.poll_inbox())
        except Exception:
            logger.exception("mesh 收件箱轮询失败")

    def take_messages(self) -> list:
        """取走本时钟拍内收到的本地兔子消息，交给 brain 转成 MESSAGE 事件。"""
        messages, self._mesh_messages = self._mesh_messages, []
        return messages

    # ------------------------------------------------------------------
    # 只读状态
    # ------------------------------------------------------------------
    @property
    def body_state(self) -> BodyState:
        return self._body

    @property
    def environment_state(self) -> EnvironmentState:
        return self._environment


__all__ = ["Lifecycle"]
