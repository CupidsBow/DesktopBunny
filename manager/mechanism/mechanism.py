"""运行时循环：状态时钟、唤醒、单帧串行与调度执行。"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Callable

from manager.activities.activities import ActivityService
from manager.body.body import Body
from manager.body.self_state import SelfStateDeriver
from manager.capabilities.executor import CapabilityExecutor
from manager.capabilities.registry import CapabilityRegistry
from manager.cognition.cognition import Cognition
from manager.cognition.situation import SituationBuilder
from manager.effector.effector import Effector
from manager.mechanism.alarms import AlarmStore, ResumeWatcher
from manager.mechanism.clocks import StateClock
from manager.mechanism.control import Control
from manager.mechanism.events import EventLedger
from manager.mechanism.persistence import PersistenceWorker
from manager.mechanism.signals import SignalDetector
from manager.mechanism.wake import WakeController
from manager.memory.consolidation import Consolidator
from manager.memory.memory import Memory
from manager.perception.perception import PerceptionService
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import (
    Action,
    Activity,
    ActivityStatus,
    ArrivedDuring,
    BodyState,
    BunnySnapshot,
    CapabilityCall,
    CapabilityErrorCode,
    CapabilityResult,
    ConversationType,
    Event,
    EventKind,
    Perception,
    SelfState,
    TurnState,
    WakeReason,
    WakeReasonKind,
)

logger = logging.getLogger(__name__)


class Mechanism:
    def __init__(
        self,
        config: BrainConfig,
        *,
        perception: PerceptionService,
        body: Body,
        self_deriver: SelfStateDeriver,
        memory: Memory,
        activities: ActivityService,
        cognition: Cognition,
        effector: Effector,
        capabilities: CapabilityExecutor,
        registry: CapabilityRegistry,
        events: EventLedger,
        alarms: AlarmStore,
        signals: SignalDetector,
        clock: StateClock,
        wake: WakeController,
        control: Control,
        consolidator: Consolidator | None = None,
        persistence: PersistenceWorker | None = None,
        snapshot_getter: Callable[[], dict[str, Any] | None] | None = None,
        qq_connected_getter: Callable[[], bool] | None = None,
        now_fn=now_local,
    ) -> None:
        self.config = config
        self.perception = perception
        self.body = body
        self.self_deriver = self_deriver
        self.memory = memory
        self.activities = activities
        self.cognition = cognition
        self.effector = effector
        self.capabilities = capabilities
        self.registry = registry
        self.events = events
        self.alarms = alarms
        self.signals = signals
        self.clock = clock
        self.wake = wake
        self.control = control
        self.consolidator = consolidator
        self.persistence = persistence
        self.snapshot_getter = snapshot_getter or (lambda: None)
        self.qq_connected_getter = qq_connected_getter or (lambda: False)
        self.now_fn = now_fn
        self.situation_builder = SituationBuilder(config, memory, now_fn=now_fn)
        self.resume_watcher = ResumeWatcher(config)

        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._consolidation_worker: threading.Thread | None = None
        self._perception = perception.tick(
            self.snapshot_getter(), qq_connected=False
        )
        self._body = body.snapshot()
        self._self_state = self.self_deriver.derive(
            self._body, None, [], self._perception.now,
            at_night=self._perception.at_night,
        )
        self._prev_body: BodyState | None = None
        self._prev_perception: Perception | None = None
        self._episodes_cache: list = []
        self._episodes_at: datetime | None = None
        self._turn = TurnState()
        self._last_frame_at = self.now_fn()
        self._last_idle_notify = 0.0
        self._last_checkpoint_id: str | None = None
        self._pending_interactions = 0
        self._frame_counter = 0
        self._last_state_tick: datetime | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.control.start()
        self.wake.reset()
        self.memory.start()
        if self.persistence is not None:
            self.persistence.start()
        self.events.load()
        self.alarms.load()
        restored = self.activities.open_activities()
        self._refresh_state(self.now_fn(), initial=True)

        detail = "启动"
        if restored:
            detail = "启动；恢复了上次未完成的活动（已转为暂停）"
        self._system_event("startup", detail)
        if self.config.paths.events_path:
            logger.info("事件账本：%s", self.config.paths.events_path)
        self.wake.notify(
            WakeReason(kind=WakeReasonKind.STARTUP, detail=detail)
        )
        for orphan in self.capabilities.take_orphans():
            call = CapabilityCall(
                capability=str(orphan.get("capability") or "unknown"),
                args={},
                call_id=orphan.get("call_id"),
            )
            result = CapabilityResult.failure(
                CapabilityErrorCode.UNKNOWN,
                "上次进程退出时该能力仍在执行，结果不可信",
                {"recovered": True, "caller": orphan.get("caller", "")},
            )
            self._emit_capability_result(call, result)

        self._worker = threading.Thread(
            target=self._worker_loop, name="cognition-worker", daemon=True
        )
        self._worker.start()
        if self.consolidator is not None:
            self._consolidation_worker = threading.Thread(
                target=self._consolidation_loop,
                name="consolidation-worker",
                daemon=True,
            )
            self._consolidation_worker.start()
        logger.info("机制已启动")

    def stop(self, reason: str = "") -> None:
        logger.info("机制停止：%s", reason)
        self._system_event("shutdown", reason or "正常关闭")
        self.control.stop(reason)
        self.wake.stop()
        if self._worker and self._worker.is_alive():
            self._worker.join(3.0)
        if self._consolidation_worker and self._consolidation_worker.is_alive():
            self._consolidation_worker.join(0.5)
        self.capabilities.close()
        self.memory.flush(3.0)
        self.memory.stop(3.0)
        if self.persistence is not None:
            self.persistence.flush(3.0)
            self.persistence.stop(3.0)

    # ------------------------------------------------------------------
    # 主线程：轻量状态时钟
    # ------------------------------------------------------------------
    def tick(self, delta: float = 0.0) -> None:
        if not self.control.running:
            return
        now = self.now_fn()
        snapshot = self.snapshot_getter()
        qq_connected = bool(self.qq_connected_getter())
        with self._lock:
            mode = self._body.mode
            due = self.clock.due(mode, now)
            interactions, self._pending_interactions = self._pending_interactions, 0
        if due:
            self._tick_state(now, snapshot, interactions, qq_connected)
        self._poll_timers(now)

    def _tick_state(
        self,
        now: datetime,
        snapshot: dict[str, Any] | None,
        interactions: int,
        qq_connected: bool,
    ) -> None:
        try:
            perception = self.perception.tick(snapshot, qq_connected)
            with self._lock:
                last = self._last_state_tick
                dt = (now - last).total_seconds() if last else 0.0
                body = self.body.tick(
                    perception,
                    interactions=interactions,
                    dt=min(max(dt, 0.0), 300.0),
                    bunny_snapshot=_as_bunny(snapshot),
                )
                episodes = self._episodes(now)
                self_state = self.self_deriver.derive(
                    body,
                    None,
                    episodes,
                    now,
                    at_night=perception.at_night,
                )
                prev_body, prev_perception = self._prev_body, self._prev_perception
                self._body = body
                self._perception = perception
                self._self_state = self_state
                self._prev_body = body
                self._prev_perception = perception
                self._last_state_tick = now
                self.clock.mark(now)
            changes = self.signals.evaluate(
                prev_body, body, prev_perception, perception, now
            )
            for change in changes:
                event = self.events.add(
                    EventKind.SIGNAL, payload=change.to_payload()
                )
                self.wake.notify(
                    WakeReason(
                        kind=WakeReasonKind.SIGNAL,
                        detail=change.detail or str(change.signal),
                        event_id=event.id,
                    )
                )
        except Exception:
            logger.exception("状态时钟异常")
            self._system_event("state_tick_error", traceback.format_exc()[:1000])

    def _episodes(self, now: datetime) -> list:
        if (
            self._episodes_at is not None
            and (now - self._episodes_at).total_seconds() < 60
        ):
            return self._episodes_cache
        self._episodes_cache = self.memory.recent_episodes(
            7 * 86400, limit=60, now=now
        )
        self._episodes_at = now
        return self._episodes_cache

    def _poll_timers(self, now: datetime) -> None:
        try:
            with self._lock:
                body = self._body
                perception = self._perception
                last_frame = self._last_frame_at
            fired = self.alarms.due(
                now,
                body,
                perception,
                checkpoint_id=self._last_checkpoint_id,
            )
            self._last_checkpoint_id = None
            for alarm in fired:
                payload = {
                    "alarm_id": alarm.id,
                    "type": alarm.kind.value,
                    "fired_at": to_iso(now),
                    "detail": alarm.note,
                }
                if alarm.predicate is not None:
                    payload["predicate"] = alarm.predicate.to_dict()
                event = self.events.add(EventKind.ALARM, payload=payload)
                self.wake.notify(
                    WakeReason(
                        kind=WakeReasonKind.ALARM,
                        detail=f"alarm {alarm.id}",
                        event_id=event.id,
                    )
                )

            for payload in self.resume_watcher.check(
                self.activities, body, perception, now
            ):
                event = self.events.add(EventKind.ALARM, payload=payload)
                self.wake.notify(
                    WakeReason(
                        kind=WakeReasonKind.ALARM,
                        detail=f"活动 {payload.get('activity_id')} 的恢复条件满足",
                        event_id=event.id,
                    )
                )

            checkpoint = self.activities.due_checkpoint(now)
            if checkpoint is not None:
                self._emit_checkpoint(checkpoint)

            max_idle = self.clock.max_idle(body.mode)
            elapsed = (now - last_frame).total_seconds()
            monotonic = time.monotonic()
            if (
                elapsed >= max_idle
                and monotonic - self._last_idle_notify >= 30
                and not self.wake.has_pending()
            ):
                self._last_idle_notify = monotonic
                self.wake.notify(
                    WakeReason(
                        kind=WakeReasonKind.MAX_IDLE,
                        detail=f"已经 {elapsed / 60:.0f} 分钟没有醒来",
                    )
                )
        except Exception:
            logger.exception("定时器检查异常")

    # ------------------------------------------------------------------
    # 外部事件
    # ------------------------------------------------------------------
    def submit_event(self, event: Event) -> Event:
        active = self.activities.active()
        if event.arrived_during is None and active is not None:
            event.arrived_during = ArrivedDuring(type="activity", id=active.id)
        event = self.events.add_event(event)
        self.wake.notify(
            WakeReason(
                kind=WakeReasonKind.EVENT,
                detail=_describe_event(event),
                event_id=event.id,
            )
        )
        if event.kind is EventKind.MESSAGE:
            with self._lock:
                self._pending_interactions += 1
        return event

    def inject_interaction(self, count: int = 1) -> None:
        with self._lock:
            self._pending_interactions += max(1, int(count))

    # ------------------------------------------------------------------
    # 认知 worker
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while self.control.running:
            try:
                if not self.wake.wait(0.5):
                    continue
                remaining = self.wake.settle_remaining()
                while remaining > 0 and self.control.running:
                    self.wake.wait(remaining)
                    remaining = self.wake.settle_remaining()
                reasons = self.wake.take()
                if not reasons or not self.control.running:
                    continue
                self._run_frame(reasons)
            except Exception:
                logger.exception("认知 worker 异常")
                self._system_event("worker_error", traceback.format_exc()[:1000])
                self.control.sleep(1.0)

    def _run_frame(self, reasons: list[WakeReason]) -> None:
        now = self.now_fn()
        self._last_frame_at = now
        self._last_idle_notify = 0.0
        self._frame_counter += 1
        caller = f"frame_{self._frame_counter}"

        with self._lock:
            perception = Perception.from_dict(self._perception.to_dict())
            body = BodyState.from_dict(self._body.to_dict())
            self_state = SelfState.from_dict(self._self_state.to_dict())
            turn = TurnState.from_dict(self._turn.to_dict())
        unhandled = self.events.unhandled()
        active = self.activities.active()
        paused = self.activities.paused()
        logger.info(
            "帧 %s 开始：唤醒=%s；未处理事件=%s；回合第 %s 帧%s",
            caller,
            _describe_reasons(reasons),
            len(unhandled),
            turn.frames_used + 1,
            f"；活动={active.id}({active.kind})" if active is not None else "",
        )
        try:
            situation = self.situation_builder.build(
                perception=perception,
                body=body,
                self_state=self_state,
                events=unhandled,
                capabilities=self.registry.names(),
                wake_reasons=reasons,
                active_activity=active,
                paused_activity=paused,
                turn_state=turn,
                delivery=self.effector.last_delivery,
                now=now,
                notes=_degraded_notes(self.body),
            )
            result = self.cognition.run(
                situation,
                self.registry.as_dicts(),
                inbox_ids={event.id for event in unhandled},
            )
        except Exception:
            logger.exception("认知帧异常")
            result = None
            error = traceback.format_exc()[:2000]
        else:
            error = result.error if result is not None else "未知错误"

        if result is None or result.decision is None:
            self._system_event(
                "frame_failed",
                error or "Decision 不可用",
            )
            turn.frames_used += 1
            self._turn = turn
            self.alarms.clear_source("llm:failure_fallback")
            self.alarms.schedule_time(
                self.clock.max_idle(body.mode),
                source="llm:failure_fallback",
                note="帧失败后的兜底唤醒",
            )
            logger.warning(
                "帧 %s 失败：%s%s",
                caller,
                error,
                f"；原始输出={_summarize(result.raw, 1000)}"
                if result is not None and result.raw
                else "",
            )
            return

        decision = result.decision
        logger.info(
            "帧 %s 决策：%s (%.0f ms%s)",
            caller,
            _describe_decision(decision),
            result.duration_ms,
            "，已修复" if result.repaired else "",
        )

        for index, call in enumerate(decision.calls):
            if not call.call_id:
                call.call_id = f"{caller}_call_{index + 1}"

        if turn.frames_used == 0:
            turn.intent = _intent(decision)
        turn.frames_used += 1

        step_limit_reached = False
        if active is not None and (
            decision.action is Action.TOOL_CALL
            or decision.action is Action.ACTIVITY_RESUME
            or decision.activity_update is not None
        ):
            step_limit_reached = self.activities.record_step(active.id)

        speaker, conversation = _speaker_for(decision, unhandled)
        _fill_reply_context(decision, unhandled)
        with self._lock:
            body_now = self._body
            perception_now = self._perception
        effect = self.effector.apply(
            decision,
            speaker=speaker,
            conversation=conversation,
            caller=caller,
            at=now,
        )
        if effect.errors:
            self._system_event("effect_error", "；".join(effect.errors))
        for call, value in zip(effect.capability_calls, effect.capability_results):
            self._emit_capability_result(call, value)

        if (
            decision.action is Action.ACTIVITY_PAUSE
            and effect.activity is not None
            and effect.activity.status is ActivityStatus.PAUSED
            and effect.activity.resume_when
        ):
            baseline = self.resume_watcher.prepare_baseline(
                effect.activity, body_now, perception_now, now
            )
            self.activities.set_resume_baseline(effect.activity.id, baseline)

        self.events.mark_handled(decision.handled)
        self._record_turns(decision, effect, unhandled, speaker, now)

        if decision.is_terminal:
            self.alarms.schedule_decision(decision, now)
            self._turn = TurnState()
            self._record_activity_end(decision, effect.activity)
        else:
            turn.pending_tool_results = list(effect.capability_results)
            self._turn = turn

        if step_limit_reached and not decision.is_terminal:
            current = self.activities.active()
            if current is not None:
                checkpoint = self.activities.step_checkpoint(current.id)
                if checkpoint is not None:
                    self._emit_checkpoint(checkpoint)

        if not decision.is_terminal and effect.pending_capabilities == 0 and not effect.capability_results:
            # 工具帧没有产生任何结果：避免停在无唤醒状态
            self._system_event("tool_call_empty", "tool.call 没有派发任何能力")
            self.alarms.clear_source("llm:tool_empty")
            self.alarms.schedule_time(
                0, source="llm:tool_empty", note="工具帧无结果"
            )

    # ------------------------------------------------------------------
    # 能力结果与检查点
    # ------------------------------------------------------------------
    def on_capability_result(
        self, call: CapabilityCall, result: CapabilityResult
    ) -> None:
        """能力 executor 的回调：任意线程。"""
        self._emit_capability_result(call, result)

    def _emit_capability_result(
        self, call: CapabilityCall, result: CapabilityResult
    ) -> None:
        payload: dict[str, Any] = {
            "call_id": call.call_id,
            "capability": call.capability,
            "ok": result.ok,
        }
        if result.ok:
            summary = _summarize(result.result)
            payload["result_summary"] = summary
            payload["truncated"] = result.truncated
            if result.result_ref:
                payload["result_ref"] = result.result_ref
            if not result.truncated:
                payload["result"] = result.result
        else:
            payload.update(
                {
                    "error": result.error,
                    "reason": result.reason,
                    "detail": result.detail,
                }
            )
        kind = (
            EventKind.CAPABILITY_DONE if result.ok else EventKind.CAPABILITY_FAILED
        )
        if result.ok:
            logger.debug(
                "能力完成 %s（%s）：%s",
                call.capability,
                call.call_id or "-",
                _summarize(result.result, 300),
            )
        else:
            logger.warning(
                "能力失败 %s（%s）：%s",
                call.capability,
                call.call_id or "-",
                result.detail,
            )
        event = self.events.add(kind, payload=payload)
        self.wake.notify(
            WakeReason(
                kind=WakeReasonKind.EVENT,
                detail=f"{kind.value}: {call.capability}",
                event_id=event.id,
            )
        )
        activity = self.activities.active()
        if activity is not None:
            if result.ok:
                self.activities.record_success(activity.id)
            else:
                updated = self.activities.record_failure(activity.id)
                if updated is not None and updated.status is ActivityStatus.FAILED:
                    self._system_event(
                        "activity_failed",
                        f"活动 {updated.id} 连续失败，已标记 failed",
                    )

    def _emit_checkpoint(self, checkpoint) -> None:
        event = self.events.add(
            EventKind.ACTIVITY_CHECKPOINT,
            payload=checkpoint.to_payload(),
        )
        self._last_checkpoint_id = checkpoint.activity_id
        self.wake.notify(
            WakeReason(
                kind=WakeReasonKind.EVENT,
                detail=f"activity_checkpoint: {checkpoint.activity_id}",
                event_id=event.id,
            )
        )

    def _record_turns(
        self,
        decision,
        effect,
        events: list[Event],
        speaker: str | None,
        now: datetime,
    ) -> None:
        """把本帧真正消费的来信与回复写进 L1。"""
        handled = set(decision.handled or [])
        messages = [
            event
            for event in events
            if event.kind is EventKind.MESSAGE and event.id in handled
        ]
        reply = ""
        if decision.action in (Action.CHAT_REPLY, Action.CHAT_REACH_OUT):
            reply = str(decision.args.get("text") or "").strip()
            if effect.delivery is not None and not effect.delivery.ok:
                reply = ""
        if decision.action is Action.CHAT_REPLY and reply:
            latest = _latest_message(events, decision.target.conversation_id if decision.target else None)
            try:
                self.memory.record_turn(
                    speaker,
                    latest.payload.get("text") if latest is not None else None,
                    reply,
                    at=now,
                )
            except Exception as exc:
                logger.debug("写入 L1 失败：%s", exc)
            messages = [
                event for event in messages if event is not latest
            ]
        elif decision.action is Action.CHAT_REACH_OUT and reply:
            try:
                self.memory.record_turn(speaker, None, reply, at=now)
            except Exception as exc:
                logger.debug("写入 L1 失败：%s", exc)
        for event in messages:
            try:
                self.memory.record_turn(
                    event.source.friend_id,
                    event.payload.get("text"),
                    None,
                    at=now,
                )
            except Exception as exc:
                logger.debug("写入 L1 失败：%s", exc)

    def _record_activity_end(
        self, decision, activity: Activity | None
    ) -> None:
        if activity is None:
            return
        if activity.status not in (
            ActivityStatus.COMPLETED,
            ActivityStatus.ABANDONED,
            ActivityStatus.FAILED,
        ):
            return
        if decision.remember is not None:
            return
        try:
            summary = (
                decision.args.get("summary")
                or decision.args.get("note")
                or activity.last_result
                or activity.goal
            )
            self.memory.remember_episode(
                f"活动{activity.status.value}：{activity.goal}",
                f"活动 {activity.kind}（{activity.id}）{activity.status.value}。\n"
                f"目标：{activity.goal}\n结果：{summary}",
                valence=-0.2 if activity.status is ActivityStatus.FAILED else 0.1,
                emotion="平静",
            )
        except Exception as exc:
            logger.debug("活动结束记忆写入失败：%s", exc)

    # ------------------------------------------------------------------
    # 巩固 worker
    # ------------------------------------------------------------------
    def _consolidation_loop(self) -> None:
        while self.control.running:
            try:
                if self.consolidator is not None and self.consolidator.due():
                    ops = self.consolidator.run()
                    if ops:
                        self._system_event(
                            "consolidation_done",
                            f"整理了 {len(ops)} 条长期记忆",
                            wake=True,
                        )
            except Exception:
                logger.exception("巩固任务异常")
            self.control.sleep(60.0)

    # ------------------------------------------------------------------
    def _system_event(
        self, event: str, detail: str = "", *, wake: bool = False
    ) -> Event:
        logger.info("系统事件 %s：%s", event, _summarize(detail, 500))
        created = self.events.add(
            EventKind.SYSTEM, payload={"event": event, "detail": detail}
        )
        if wake:
            self.wake.notify(
                WakeReason(
                    kind=WakeReasonKind.EVENT,
                    detail=f"system: {event}",
                    event_id=created.id,
                )
            )
        return created

    def _refresh_state(self, now: datetime, *, initial: bool = False) -> None:
        snapshot = self.snapshot_getter()
        perception = self.perception.tick(
            snapshot, bool(self.qq_connected_getter())
        )
        body = self.body.tick(
            perception,
            interactions=0,
            dt=0.0,
            bunny_snapshot=_as_bunny(snapshot),
        )
        with self._lock:
            self._perception = perception
            self._body = body
            self._self_state = self.self_deriver.derive(
                body, None, self._episodes(now), now,
                at_night=perception.at_night,
            )
            self._last_state_tick = now if not initial else None
        self.clock.mark(now)

    # ------------------------------------------------------------------
    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "perception": self._perception.to_dict(),
                "body": self._body.to_dict(),
                "self_state": self._self_state.to_dict(),
                "turn": self._turn.to_dict(),
                "last_frame_at": to_iso(self._last_frame_at),
            }


# ----------------------------------------------------------------------
def _describe_event(event: Event) -> str:
    if event.kind is EventKind.MESSAGE:
        friend = event.source.friend_id or "?"
        return f"message from {friend}"
    if event.kind in (EventKind.CAPABILITY_DONE, EventKind.CAPABILITY_FAILED):
        return f"{event.kind.value}: {event.payload.get('capability', '?')}"
    if event.kind is EventKind.ALARM:
        return f"alarm: {event.payload.get('alarm_id', '?')}"
    if event.kind is EventKind.SIGNAL:
        return f"signal: {event.payload.get('signal', '?')}"
    return f"{event.kind.value}: {event.payload.get('event', event.id)}"


def _intent(decision) -> str:
    if decision.action is Action.ACTIVITY_START:
        return str(decision.args.get("goal") or "开始活动")
    if decision.target is not None:
        return f"{decision.action.value} -> {decision.target.friend_id}"
    return decision.action.value


def _fill_reply_context(decision, events: list[Event]) -> None:
    """把来信的 msg_id / 会话类型补进 target，保证被动回复命中窗口。"""
    target = decision.target
    if target is None or decision.action not in (
        Action.CHAT_REPLY,
        Action.CHAT_REACH_OUT,
    ):
        return
    message = _latest_message(events, target.conversation_id or target.friend_id)
    if message is None:
        return
    if target.msg_id is None:
        target.msg_id = message.payload.get("msg_id")
    raw_type = message.payload.get("conversation_type")
    if raw_type:
        try:
            target.conversation_type = ConversationType(raw_type)
        except (ValueError, TypeError):
            pass


def _latest_message(
    events: list[Event], conversation_id: str | None
) -> Event | None:
    for event in reversed(events):
        if event.kind is not EventKind.MESSAGE:
            continue
        if conversation_id and (
            event.source.conversation_id != conversation_id
            and event.source.friend_id != conversation_id
        ):
            continue
        return event
    return None


def _speaker_for(decision, events: list[Event]) -> tuple[str | None, str | None]:
    if decision.target is not None:
        return decision.target.friend_id, decision.target.conversation_id
    for event in reversed(events):
        if event.kind is EventKind.MESSAGE and event.source.friend_id:
            return event.source.friend_id, event.source.conversation_id
    return None, None


def _degraded_notes(body: Body) -> list[str]:
    notes: list[str] = []
    if getattr(body, "satiety_stale", False):
        notes.append("饱食度快照缺失，沿用上一次的值")
    return notes


def _as_bunny(
    snapshot: BunnySnapshot | dict[str, Any] | None,
) -> BunnySnapshot | None:
    if snapshot is None or isinstance(snapshot, BunnySnapshot):
        return snapshot
    if not isinstance(snapshot, dict) or "satiety" not in snapshot:
        return None
    return BunnySnapshot.from_dict(snapshot)


def _describe_reasons(reasons: list[WakeReason]) -> str:
    text = "、".join(
        f"{reason.kind.value}:{reason.detail}"
        if reason.detail
        else reason.kind.value
        for reason in reasons
    )
    return _summarize(text, 300) or "无"


def _describe_decision(decision) -> str:
    """把一帧决策压成一行，完整覆盖 Decision 的所有字段。"""
    parts = [decision.action.value]
    target = decision.target
    if target is not None:
        parts.append(
            f"target={target.friend_id}/{target.conversation_id}"
            f"({target.conversation_type.value})"
        )
    if decision.args:
        parts.append(f"args={_summarize(decision.args, 300)}")
    if decision.calls:
        calls = ", ".join(
            f"{call.capability}({_summarize(call.args, 120)})"
            for call in decision.calls
        )
        parts.append(f"calls=[{calls}]")
    if decision.activity_update is not None:
        parts.append(
            f"activity_update={_summarize(decision.activity_update.to_dict(), 200)}"
        )
    if decision.handled:
        parts.append(f"handled=[{','.join(decision.handled)}]")
    if decision.remember is not None:
        parts.append(f"remember={_summarize(decision.remember.to_dict(), 200)}")
    if decision.next_wake_in is not None:
        parts.append(f"next_wake_in={decision.next_wake_in}")
    if decision.wake_when:
        parts.append(
            "wake_when=["
            + ",".join(predicate.type.value for predicate in decision.wake_when)
            + "]"
        )
    return " | ".join(parts)


def _summarize(value: Any, limit: int = 1000) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    return text[:limit] + ("…" if len(text) > limit else "")


__all__ = ["Mechanism"]
