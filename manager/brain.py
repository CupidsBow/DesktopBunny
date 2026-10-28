"""Brain 组合根：装配所有子模块，跑认知 worker 与状态时钟。

``main.py`` 与 ``bot/`` 只依赖这里的五个方法：``start / stop / tick /
submit_event / snapshot``。

从旧 ``manager/brain.py`` + ``manager/mechanism/mechanism.py`` 合并而来，按
new_brain.md 收缩：WakeController / StateClock / Control / EventLedger /
PersistenceWorker / PerceptionService 都已被 ``lifecycle`` / ``event_queue``
吸收，这里只负责装配、线程与把唤醒理由转成事件、把 Decision 交给 executor。
"""

from __future__ import annotations

import json
import logging
import threading
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

from manager.activity.activity import ActivityService
from manager.activity.store import ActivityStore
from manager.body.body import Body
from manager.body.self_state import SelfStateDeriver
from manager.capabilities.artifacts import ArtifactStore
from manager.capabilities.audit import AuditLog
from manager.capabilities.executor import CapabilityExecutor
from manager.capabilities.registry import CapabilityRegistry
from manager.environment.environment import Environment
from manager.event_queue.event_queue import EventQueue
from manager.executor.chat_sender import ChatSender
from manager.executor.executor import Executor
from manager.frame_generator.frame_generator import FrameGenerator
from manager.lifecycle.lifecycle import Lifecycle
from manager.memory.consolidation import Consolidator
from manager.memory.frame_episode import compose_episode
from manager.memory.memory import Memory
from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.llm_client import LLMClient
from manager.shared.logging_setup import setup_logging
from manager.shared.timeutil import now_local, parse_time_arg, to_iso
from manager.shared.types import (
    Action,
    Activity,
    ActivityStatus,
    ArrivedDuring,
    AlarmPredicate,
    BodyState,
    CapabilityCall,
    CapabilityErrorCode,
    CapabilityResult,
    EnvironmentState,
    Event,
    EventKind,
    EventSource,
    TurnState,
    WakePredicateType,
    WakeReason,
    WakeReasonKind,
)
from manager.thinker.thinker import Thinker

logger = logging.getLogger(__name__)

#: 认知 worker 在没有外部唤醒时的轮询间隔（秒）。
_POLL_INTERVAL = 0.5

#: 帧失败后的重试延迟（秒）。用尽后不再重试，退回定期心跳兜底。
_FRAME_RETRY_DELAYS = (5, 15, 60, 180)

#: 已经写进 L1 的事件 id 上限；超出就整体清空，只用于去重。
_RECORDED_MESSAGE_LIMIT = 512


class _ResumeWatcher:
    """暂停活动的 ``resume_when``：只产生闹钟事件，不自动恢复。

    从旧 ``mechanism/alarms.py`` 的 ResumeWatcher 移植，感知类型由
    ``Perception`` 换成 ``EnvironmentState``。
    """

    def __init__(self, config: BrainConfig) -> None:
        self.config = config

    def prepare_baseline(
        self,
        activity: Activity,
        body: BodyState,
        environment: EnvironmentState,
        now: datetime,
    ) -> dict[str, Any]:
        at_time: dict[str, float] = {}
        for index, predicate in enumerate(activity.resume_when):
            if predicate.type is not WakePredicateType.AT_TIME:
                continue
            target = parse_time_arg(predicate.args, now)
            if target is None:
                continue
            if target <= now:
                target += timedelta(days=1)
            at_time[str(index)] = target.timestamp()
        return {
            "satiety": float(body.satiety),
            "energy": float(body.energy),
            "presence": environment.someone_present,
            "at_time": at_time,
        }

    def check(
        self,
        activities: ActivityService,
        body: BodyState,
        environment: EnvironmentState,
        now: datetime,
    ) -> list[dict[str, Any]]:
        fired: list[dict[str, Any]] = []
        for activity in activities.all():
            if activity.status is not ActivityStatus.PAUSED:
                continue
            if not activity.resume_when:
                continue
            baseline = activity.checkpoint.get("resume_baseline")
            if not isinstance(baseline, dict):
                # 旧数据没有基线：以当前状态为基线，避免刚加载就触发
                baseline = self.prepare_baseline(
                    activity, body, environment, now
                )
                activities.set_resume_baseline(activity.id, baseline)
                continue
            predicate = self._first_satisfied(
                activity.resume_when, baseline, body, environment, now
            )
            if predicate is None:
                continue
            activities.clear_resume_when(activity.id)
            fired.append(
                {
                    "alarm_id": f"resume:{activity.id}",
                    "type": "predicate",
                    "source": "activity_resume",
                    "activity_id": activity.id,
                    "predicate": {
                        "type": predicate.type.value,
                        "args": predicate.args,
                    },
                    "fired_at": to_iso(now),
                }
            )
        return fired

    def _first_satisfied(
        self,
        predicates: list[AlarmPredicate],
        baseline: dict[str, Any],
        body: BodyState,
        environment: EnvironmentState,
        now: datetime,
    ) -> AlarmPredicate | None:
        for index, predicate in enumerate(predicates):
            args = predicate.args or {}
            if predicate.type is WakePredicateType.SATIETY_ABOVE:
                threshold = float(args.get("value", 100))
                if float(baseline.get("satiety", 0)) < threshold and body.satiety >= threshold:
                    return predicate
            elif predicate.type is WakePredicateType.ENERGY_ABOVE:
                threshold = float(args.get("value", 1))
                if float(baseline.get("energy", 0)) < threshold and body.energy >= threshold:
                    return predicate
            elif predicate.type is WakePredicateType.PRESENCE_CHANGED:
                if environment.someone_present != baseline.get("presence"):
                    return predicate
            elif predicate.type is WakePredicateType.AT_TIME:
                targets = baseline.get("at_time") or {}
                target = targets.get(str(index))
                if target is not None and now.timestamp() >= float(target):
                    return predicate
        return None


class _BusyMeter:
    """把「当前是否忙碌」换算成自上次采样以来的忙碌秒数。

    ``lifecycle.tick`` 每次推进状态调用一次 getter，body 再用 dt 钳制，
    所以这里用两次采样之间的时钟差当作本次间隔，忙碌则整段计入。
    """

    def __init__(self, now_fn: Callable[[], datetime]) -> None:
        self.now_fn = now_fn
        self._last_at: datetime | None = None

    def sample(self, busy: bool) -> float:
        now = self.now_fn()
        dt = 0.0
        if self._last_at is not None:
            dt = max(0.0, (now - self._last_at).total_seconds())
        self._last_at = now
        return dt if busy else 0.0


class Brain:
    def __init__(
        self,
        config: BrainConfig | None = None,
        *,
        paths: BrainPaths | str | None = None,
        llm: LLMClient | None = None,
        transport: Any = None,
        snapshot_getter: Callable[[], dict[str, Any] | None] | None = None,
        busy_seconds_getter: Callable[[], float] | None = None,
        eat_handler: Callable[[str], bool] | None = None,
        now_fn: Callable[[], datetime] = now_local,
        name: str = "Alice",
        bunny_friends: list[str] | None = None,
        mesh: Any = None,
        enable_logging: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        if config is None:
            if isinstance(paths, BrainPaths):
                brain_paths = paths
            elif paths:
                brain_paths = BrainPaths(root=str(paths))
            else:
                brain_paths = BrainPaths()
            config = BrainConfig(paths=brain_paths)
        self.config = config
        self.paths = config.paths
        self.paths.ensure_directories()
        if enable_logging:
            setup_logging(self.paths.logs_dir, log_level)

        self.now_fn = now_fn
        self.name = name
        self.bunny_friends = list(bunny_friends or [])
        self.mesh = mesh
        if self.mesh is not None:
            self.mesh.heartbeat_seconds = config.peer_heartbeat_seconds
            self.mesh.heartbeat_timeout = config.peer_heartbeat_timeout
        self._known_peer_slugs: set[str] = set()
        self._started = False
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._wake_event = threading.Event()
        self._pending_reasons: list[WakeReason] = []
        self._frame_counter = 0
        self._frame_failures = 0
        self._retry_at: datetime | None = None
        self._turn = TurnState()
        #: 本回合用过的工具（"fs.read 刷钱机.txt"），终态帧写情景时带上。
        self._turn_trail: list[str] = []
        #: 已经写进 L1 的事件 id，防止模型漏标 handled 时重复记录。
        self._recorded_messages: set[str] = set()
        self._last_body_save = now_fn()
        self._episodes_cache: list = []
        self._episodes_at: datetime | None = None

        if llm is None:
            llm = LLMClient.from_constants(
                retry_limit=config.llm_retry_limit,
                backoff_seconds=config.llm_retry_backoff_seconds,
                timeout=config.frame_timeout_seconds,
            )
        self.llm = llm

        # --- 记忆 / 活动 / 身体 / 环境 / 生命循环 ---
        self.memory = Memory(self.paths, config, llm=llm, now_fn=now_fn)
        self.activities = ActivityService(
            ActivityStore(self.paths.activities_path), config, now_fn=now_fn
        )
        self.body = Body(config, now_fn=now_fn)
        self.environment = Environment(
            config,
            peers_getter=(self.mesh.discover if self.mesh is not None else None),
            now_fn=now_fn,
        )
        self.self_deriver = SelfStateDeriver(config)

        # 饱食度经 main.py 注入 bridge 快照；若另有组合根，需同样传入 snapshot_getter。
        self._snapshot_getter = snapshot_getter or (lambda: None)
        self._busy_meter = _BusyMeter(now_fn)
        self._busy_seconds_getter = busy_seconds_getter or (
            lambda: self._busy_meter.sample(self._is_busy())
        )
        self.lifecycle = Lifecycle(
            config,
            body=self.body,
            environment=self.environment,
            snapshot_getter=self._snapshot_getter,
            busy_seconds_getter=self._busy_seconds_getter,
            mesh=self.mesh,
            now_fn=now_fn,
        )

        # --- 能力 ---
        self.artifacts = ArtifactStore(self.paths.artifacts_dir)
        self.audit = AuditLog(self.paths.audit_path)
        self.registry = CapabilityRegistry(
            config,
            artifacts=self.artifacts,
            eat_handler=eat_handler,
            now_fn=now_fn,
        )
        self.capabilities = CapabilityExecutor(
            self.registry,
            config,
            artifacts=self.artifacts,
            audit=self.audit,
            on_async_result=self._on_async_result,
            inflight_path=self.paths.inflight_path,
        )

        # --- 认知 / 执行 ---
        self.chat_sender = ChatSender(config, transport, mesh=self.mesh)
        self.executor = Executor(
            config,
            activities=self.activities,
            chat_sender=self.chat_sender,
            lifecycle=self.lifecycle,
            capabilities=self.capabilities,
            now_fn=now_fn,
        )
        self.frame_generator = FrameGenerator(
            self.memory,
            self.registry,
            now_fn=now_fn,
            name=name,
            personality_path=self.paths.personality_path,
        )
        self.thinker = Thinker(config, llm)

        # --- 事件与巩固 ---
        self.events = EventQueue(self.paths.events_path, config, now_fn=now_fn)
        self.consolidator = Consolidator(self.memory, llm, config, now_fn=now_fn)
        self._resume_watcher = _ResumeWatcher(config)

        self._worker: threading.Thread | None = None
        self._consolidation_worker: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        self.memory.start()
        self._preload_bunny_friends()
        self._baseline_peers()
        self.events.load()
        restored = self.activities.open_activities()

        detail = "启动"
        if restored:
            detail = "启动；恢复了上次未完成的活动（已转为暂停）"
        self._system_event("startup", detail)
        self._notify(WakeReason(kind=WakeReasonKind.STARTUP, detail=detail))

        # 上次进程退出时仍在执行的能力，结果不可信，标记后交给 LLM 重新审视。
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
        self._consolidation_worker = threading.Thread(
            target=self._consolidation_loop,
            name="consolidation-worker",
            daemon=True,
        )
        self._consolidation_worker.start()

        self._started = True
        logger.info("Brain 已启动：%s", self.paths.root)

    def _preload_bunny_friends(self) -> None:
        """按 .env 的 BUNNY_FRIENDS 预建本地朋友档案（仅首次，已有则跳过）。

        friend_id 用 `peer:<名字>` 与来信一致；档案里 frontmatter 的 `name` 就是
        这个 friend_id，LLM 从记忆里就能认出"这是老朋友"，无需 prompt 额外教前缀。
        """
        for friend_id in self.bunny_friends:
            try:
                if not self.memory.friend(friend_id).first_seen:
                    self.memory.ensure_friend(friend_id)
            except Exception:
                logger.exception("预建朋友档案失败：%s", friend_id)

    def stop(self, reason: str = "正常关闭") -> None:
        if not self._started:
            return
        logger.info("Brain 停止：%s", reason)
        self._system_event("shutdown", reason or "正常关闭")
        self._stop_event.set()
        self._wake_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(3.0)
        if self._consolidation_worker and self._consolidation_worker.is_alive():
            self._consolidation_worker.join(0.5)
        try:
            self.body.save()
        except Exception:
            logger.exception("保存身体状态失败")
        self.capabilities.close()
        self.memory.flush(3.0)
        self.memory.stop(3.0)
        self._started = False
        logger.info("Brain 已停止：%s", reason)

    def tick(self, delta: float = 0.0) -> None:
        """推进状态时钟；只做轻量推进与事件转换，不调 LLM。"""
        if not self._started:
            return
        woke = False
        changes = self.lifecycle.tick(delta)
        for change in changes:
            self._add_event(EventKind.SIGNAL, change.to_payload())
            woke = True
        for message in self.lifecycle.take_messages():
            self._ingest_peer_message(message)
        self._detect_peer_presence()

        fired = self._resume_watcher.check(
            self.activities,
            self.lifecycle.body_state,
            self.lifecycle.environment_state,
            self.now_fn(),
        )
        for payload in fired:
            event = self._add_event(EventKind.ALARM, payload)
            self._notify(
                WakeReason(
                    kind=WakeReasonKind.ALARM,
                    detail=f"活动 {payload.get('activity_id')} 的恢复条件满足",
                    event_id=event.id,
                )
            )
            woke = True

        checkpoint = self.activities.due_checkpoint()
        if checkpoint is not None:
            self._emit_checkpoint(checkpoint)
            woke = True

        if woke:
            self._wake_event.set()

        now = self.now_fn()
        if (
            now - self._last_body_save
        ).total_seconds() >= self.config.body_save_interval:
            try:
                self.body.save()
            except Exception:
                logger.exception("保存身体状态失败")
            self._last_body_save = now

    def submit_event(self, event: Event) -> Event:
        """外部事件入队并唤醒；MESSAGE 会记互动并刷新好友档案。"""
        if not self._started:
            logger.warning("Brain 未启动，事件被忽略：%s", event.kind)
            return event
        active = self.activities.active()
        if event.arrived_during is None and active is not None:
            event.arrived_during = ArrivedDuring(type="activity", id=active.id)
        event = self.events.add(event)
        if event.kind is EventKind.MESSAGE:
            if event.source.friend_id:
                self.memory.ensure_friend(event.source.friend_id)
            self.lifecycle.note_interactions(1)
        logger.info("事件入队 %s：%s", event.id, _describe_event(event))
        self._notify(
            WakeReason(
                kind=WakeReasonKind.EVENT,
                detail=_describe_event(event),
                event_id=event.id,
            )
        )
        return event

    def _ingest_peer_message(self, message) -> None:
        """本地朋友的 inbox 消息 → MESSAGE 事件（friend_id 用 ``peer:<slug>``）。"""
        try:
            self.submit_event(
                Event(
                    id="",
                    kind=EventKind.MESSAGE,
                    at=to_iso(self.now_fn()),
                    source=EventSource(friend_id=f"peer:{message.from_slug}"),
                    payload={
                        "msg_id": message.msg_id,
                        "text": message.text,
                        "from_name": message.from_name,
                    },
                )
            )
        except Exception:
            logger.exception(
                "本地朋友消息入队失败：%s", getattr(message, "msg_id", "")
            )

    def _baseline_peers(self) -> None:
        """启动时记住当前在线的朋友，避免把"启动时对方已在"当成新上线。"""
        if self.mesh is None:
            self._known_peer_slugs = set()
            return
        try:
            self._known_peer_slugs = {peer.slug for peer in self.mesh.discover()}
        except Exception:
            logger.exception("peer 基线失败")
            self._known_peer_slugs = set()

    def _detect_peer_presence(self) -> None:
        """本地朋友从离线变在线时，发 SIGNAL 事件唤醒 LLM（有机会主动打招呼）。"""
        if self.mesh is None:
            return
        current = {peer.slug for peer in self.lifecycle.environment_state.peers}
        for slug in sorted(current - self._known_peer_slugs):
            self._peer_online(slug)
        self._known_peer_slugs = current

    def _peer_online(self, slug: str) -> None:
        friend_id = f"peer:{slug}"
        # 对方刚上线就发来消息时，两件事会撞进同一帧：模型容易先应"你上线啦"，
        # 把消息晾在一边。既然消息本身就会唤醒，上线信号就让位。
        if any(
            event.kind is EventKind.MESSAGE
            and event.source.friend_id == friend_id
            for event in self.events.unhandled()
        ):
            logger.debug("朋友 %s 上线，但有未读消息，跳过上线信号", slug)
            return

        logger.info("本地朋友上线：%s", slug)
        self._add_event(
            EventKind.SIGNAL,
            {
                "signal": "peer_online",
                "from": "offline",
                "to": "online",
                "slug": slug,
                "detail": f"朋友 {slug} 上线了",
            },
        )
        self._notify(
            WakeReason(kind=WakeReasonKind.EVENT, detail=f"朋友 {slug} 上线")
        )

    def snapshot(self) -> dict[str, Any]:
        body = self.lifecycle.body_state
        environment = self.lifecycle.environment_state
        activity = self.activities.active() or self.activities.paused()
        return {
            "satiety": body.satiety,
            "energy": body.energy,
            "social_battery": body.social_battery,
            "presence": body.presence,
            "someone_present": environment.someone_present,
            "activity": (
                {
                    "id": activity.id,
                    "kind": activity.kind,
                    "goal": activity.goal,
                    "status": activity.status.value,
                    "progress": activity.progress,
                }
                if activity is not None
                else None
            ),
        }

    # ------------------------------------------------------------------
    # 能力结果回调（任意线程）
    # ------------------------------------------------------------------
    def _on_async_result(
        self, call: CapabilityCall, result: CapabilityResult
    ) -> None:
        if not self._started:
            logger.debug("Brain 未运行，异步能力结果被忽略：%s", call.capability)
            return
        try:
            self._emit_capability_result(call, result)
        except Exception:
            logger.exception("处理异步能力结果失败")

    def _emit_capability_result(
        self, call: CapabilityCall, result: CapabilityResult
    ) -> None:
        payload: dict[str, Any] = {
            "call_id": call.call_id,
            "capability": call.capability,
            "ok": result.ok,
        }
        if result.ok:
            payload["result_summary"] = _summarize(result.result, 300)
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
        event = self._add_event(kind, payload)
        self._notify(
            WakeReason(
                kind=WakeReasonKind.EVENT,
                detail=f"{kind.value}: {call.capability}",
                event_id=event.id,
            )
        )
        if result.ok:
            logger.info("能力结果 %s：成功", call.capability)
        else:
            logger.info(
                "能力结果 %s：失败（%s）",
                call.capability,
                result.error or result.reason,
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

    # ------------------------------------------------------------------
    # 认知 worker
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._wake_event.wait(_POLL_INTERVAL)
                self._wake_event.clear()
                if self._stop_event.is_set():
                    break

                reasons = self.lifecycle.due() + self._take_pending_reasons()
                retry = self._take_retry_reason(self.now_fn())
                if retry is not None:
                    reasons.append(retry)
                if not reasons:
                    continue
                unhandled = self.events.unhandled()
                self._run_frame(reasons, unhandled)
            except Exception:
                logger.exception("认知 worker 异常")
                self._system_event(
                    "worker_error", traceback.format_exc()[:1000]
                )
                self._stop_event.wait(1.0)

    def _run_frame(
        self, reasons: list[WakeReason], unhandled: list[Event]
    ) -> None:
        now = self.now_fn()
        self._frame_counter += 1
        caller = f"frame_{self._frame_counter}"
        if self._frame_counter % 20 == 0:
            # 观察协议稳定性：修复率是决定要不要换原生 tool calling 的依据。
            logger.info("认知统计：%s", self.thinker.describe_stats())
        total_unhandled = len(unhandled)
        limit = int(self.config.inbox_max_events or 0)
        if limit > 0 and len(unhandled) > limit:
            unhandled = unhandled[-limit:]
        dropped = total_unhandled - len(unhandled)

        body = BodyState.from_dict(self.lifecycle.body_state.to_dict())
        environment = EnvironmentState.from_dict(
            self.lifecycle.environment_state.to_dict()
        )
        self_state = self.self_deriver.derive(
            body, None, self._episodes(now), now
        )
        turn = TurnState.from_dict(self._turn.to_dict())

        active = self.activities.active()
        paused = self.activities.paused()
        logger.info(
            "帧 %s 开始：唤醒=%s；未处理事件=%s；回合第 %s 帧",
            caller,
            _describe_reasons(reasons),
            len(unhandled),
            turn.frames_used + 1,
        )
        try:
            frame = self.frame_generator.build(
                environment=environment,
                body=body,
                self_state=self_state,
                events=unhandled,
                wake_reasons=reasons,
                active_activity=active,
                paused_activity=paused,
                turn_state=turn,
                delivery=self.executor.last_delivery,
                now=now,
                notes=self._degraded_notes(),
            )
            result = self.thinker.run(
                frame, inbox_ids={event.id for event in unhandled}
            )
        except Exception:
            logger.exception("认知帧异常")
            result = None
            error = traceback.format_exc()[:2000]
        else:
            error = result.error if result is not None else "未知错误"

        if result is None or result.decision is None:
            self._system_event("frame_failed", error or "Decision 不可用")
            turn.frames_used += 1
            self._turn = turn
            logger.warning(
                "帧 %s 失败：%s%s",
                caller,
                error,
                f"；原始输出={_summarize(result.raw, 1000)}"
                if result is not None and result.raw
                else "",
            )
            self._schedule_frame_retry(now)
            return

        decision = result.decision
        self._clear_frame_retry()
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
            self._turn_trail = []
        turn.frames_used += 1

        step_limit_reached = False
        if active is not None and (
            decision.action is Action.TOOL_CALL
            or decision.action is Action.ACTIVITY_RESUME
            or decision.activity_update is not None
        ):
            step_limit_reached = self.activities.record_step(active.id)

        speaker = _speaker_for(decision, unhandled)
        _fill_reply_context(decision, unhandled)
        effect = self.executor.apply(
            decision,
            speaker=speaker,
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
            baseline = self._resume_watcher.prepare_baseline(
                effect.activity, body, environment, now
            )
            self.activities.set_resume_baseline(effect.activity.id, baseline)

        self.events.mark_handled(decision.handled)
        self._record_turns(decision, effect, unhandled, speaker, now)
        self._turn_trail.extend(_trail_steps(decision))

        if decision.is_terminal:
            self._turn = TurnState()
            self._record_frame_episode(
                decision,
                effect,
                self_state,
                unhandled,
                speaker,
                now,
                trail=self._turn_trail,
                startup=any(
                    reason.kind is WakeReasonKind.STARTUP for reason in reasons
                ),
            )
            self._turn_trail = []
        else:
            turn.pending_tool_results = list(effect.capability_results)
            self._turn = turn

        logger.info("帧 %s 结束：%s", caller, _describe_effect(effect, decision))

        if step_limit_reached and not decision.is_terminal:
            current = self.activities.active()
            if current is not None:
                checkpoint = self.activities.step_checkpoint(current.id)
                if checkpoint is not None:
                    self._emit_checkpoint(checkpoint)

        if (
            not decision.is_terminal
            and effect.pending_capabilities == 0
            and not effect.capability_results
        ):
            # 工具帧没有产生任何结果：避免停在无唤醒状态
            self._system_event(
                "tool_call_empty", "tool.call 没有派发任何能力", wake=True
            )

        if (
            dropped > 0
            and decision.is_terminal
            and decision.handled
            and not self._is_busy()
        ):
            # 本帧因 inbox 上限只展示了最近一部分事件，且 LLM 已空闲：再唤醒一次继续消化剩余积压。
            self._notify(
                WakeReason(
                    kind=WakeReasonKind.EVENT,
                    detail="还有未处理事件，继续处理",
                )
            )

    # ------------------------------------------------------------------
    # 巩固 worker
    # ------------------------------------------------------------------
    def _consolidation_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.consolidator.due():
                    ops = self.consolidator.run()
                    if ops:
                        self._system_event(
                            "consolidation_done",
                            f"整理了 {len(ops)} 条长期记忆",
                            wake=True,
                        )
            except Exception:
                logger.exception("巩固任务异常")
            self._stop_event.wait(60.0)

    # ------------------------------------------------------------------
    # 事件与记忆回写
    # ------------------------------------------------------------------
    def _add_event(self, kind: EventKind, payload: dict[str, Any]) -> Event:
        return self.events.add(
            Event(id="", kind=kind, at=to_iso(self.now_fn()), payload=payload)
        )

    def _system_event(
        self, event: str, detail: str = "", *, wake: bool = False
    ) -> Event:
        logger.info("系统事件 %s：%s", event, _summarize(detail, 500))
        created = self._add_event(
            EventKind.SYSTEM, {"event": event, "detail": detail}
        )
        if wake:
            self._notify(
                WakeReason(
                    kind=WakeReasonKind.EVENT,
                    detail=f"system: {event}",
                    event_id=created.id,
                )
            )
        return created

    def _emit_checkpoint(self, checkpoint) -> Event:
        event = self._add_event(
            EventKind.ACTIVITY_CHECKPOINT, checkpoint.to_payload()
        )
        self._notify(
            WakeReason(
                kind=WakeReasonKind.EVENT,
                detail=f"activity_checkpoint: {checkpoint.activity_id}",
                event_id=event.id,
            )
        )
        return event

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
            latest = _latest_message(
                events,
                decision.target.friend_id if decision.target else None,
            )
            try:
                self.memory.record_turn(
                    speaker,
                    latest.payload.get("text") if latest is not None else None,
                    reply,
                    at=now,
                )
            except Exception as exc:
                logger.debug("写入 L1 失败：%s", exc)
            if latest is not None:
                self._recorded_messages.add(latest.id)
            messages = [event for event in messages if event is not latest]
        elif decision.action is Action.CHAT_REACH_OUT and reply:
            try:
                self.memory.record_turn(speaker, None, reply, at=now)
            except Exception as exc:
                logger.debug("写入 L1 失败：%s", exc)
        for event in messages:
            # 模型没把某条来信标成 handled 时，它会一直留在 inbox 里；
            # 每帧都重记一遍会让 L1 出现重复的同一句话。按事件 id 去重。
            if event.id in self._recorded_messages:
                continue
            self._recorded_messages.add(event.id)
            if len(self._recorded_messages) > _RECORDED_MESSAGE_LIMIT:
                self._recorded_messages.clear()
                self._recorded_messages.add(event.id)
            try:
                self.memory.record_turn(
                    event.source.friend_id,
                    event.payload.get("text"),
                    None,
                    at=now,
                )
            except Exception as exc:
                logger.debug("写入 L1 失败：%s", exc)

    def _record_frame_episode(
        self,
        decision,
        effect,
        self_state,
        events,
        speaker: str | None,
        now: datetime,
        *,
        trail: Sequence[str] = (),
        startup: bool = False,
    ) -> None:
        """终态帧结束后由机制写一条 L2 情景。

        事实由 :func:`compose_episode` 从这一帧的决策与事件里拼；
        情绪取当帧派生出的 ``SelfState``，不由模型自报。

        ``trail`` 是本回合用过的工具；没有它，一整轮"翻翻看看然后 idle"
        会什么都不留下。

        启动帧不写：每次重启都留一条"开机说了句话"，只会污染记忆。
        """
        if startup:
            return
        composed = compose_episode(
            decision,
            speaker=speaker,
            events=events,
            activity=effect.activity,
            errors=effect.errors,
            trail=trail,
        )
        if composed is None:
            return
        description, content = composed
        urges = getattr(self_state, "urges", None) or []
        arousal = max((float(getattr(urge, "level", 0.0)) for urge in urges), default=0.0)
        try:
            self.memory.remember_episode(
                description,
                content,
                speaker=(
                    speaker
                    if decision.action
                    in (Action.CHAT_REPLY, Action.CHAT_REACH_OUT)
                    else None
                ),
                valence=float(getattr(self_state, "mood", 0.0)),
                emotion=str(getattr(self_state, "mood_label", "平静") or "平静"),
                arousal=arousal,
                at=now,
            )
        except Exception as exc:
            logger.debug("情景写入失败：%s", exc)

    # ------------------------------------------------------------------
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

    def _degraded_notes(self) -> list[str]:
        notes: list[str] = []
        if getattr(self.body, "satiety_stale", False):
            notes.append("饱食度快照缺失，沿用上一次的值")
        return notes

    def _is_busy(self) -> bool:
        """Alice 正在忙碌：有进行中的活动，或还有在途的异步能力。"""
        return (
            self.activities.active() is not None
            or self.capabilities.inflight_count() > 0
        )

    # ------------------------------------------------------------------
    def _notify(self, reason: WakeReason) -> None:
        with self._lock:
            self._pending_reasons.append(reason)
        self._wake_event.set()

    def _take_pending_reasons(self) -> list[WakeReason]:
        with self._lock:
            reasons, self._pending_reasons = self._pending_reasons, []
        return reasons

    # ------------------------------------------------------------------
    def _schedule_frame_retry(self, now: datetime) -> None:
        """帧失败后安排一次重试唤醒。

        失败时唤醒理由已被消耗：不补一次，消息就得等满 1800 秒的定期心跳。
        指数退避，用尽 ``_FRAME_RETRY_DELAYS`` 后放弃，交回心跳兜底，
        避免持续故障时变成烧钱循环。
        """
        attempt = self._frame_failures
        self._frame_failures += 1
        if attempt >= len(_FRAME_RETRY_DELAYS):
            self._retry_at = None
            logger.warning(
                "帧连续失败 %s 次，暂停重试，等待下次事件或定期心跳",
                self._frame_failures,
            )
            return
        delay = _FRAME_RETRY_DELAYS[attempt]
        self._retry_at = now + timedelta(seconds=delay)
        logger.info("帧失败，%s 秒后重试（第 %s 次）", delay, attempt + 1)

    def _clear_frame_retry(self) -> None:
        self._frame_failures = 0
        self._retry_at = None

    def _take_retry_reason(self, now: datetime) -> WakeReason | None:
        with self._lock:
            if self._retry_at is None or now < self._retry_at:
                return None
            self._retry_at = None
        return WakeReason(
            kind=WakeReasonKind.ALARM,
            detail="帧失败重试",
        )

    @property
    def started(self) -> bool:
        return self._started


# ----------------------------------------------------------------------
def _describe_event(event: Event) -> str:
    if event.kind is EventKind.MESSAGE:
        return f"message from {event.source.friend_id or '?'}"
    if event.kind in (EventKind.CAPABILITY_DONE, EventKind.CAPABILITY_FAILED):
        return f"{event.kind.value}: {event.payload.get('capability', '?')}"
    if event.kind is EventKind.ALARM:
        return f"alarm: {event.payload.get('alarm_id', '?')}"
    if event.kind is EventKind.SIGNAL:
        return f"signal: {event.payload.get('signal', '?')}"
    return f"{event.kind.value}: {event.payload.get('event', event.id)}"


def _trail_steps(decision) -> list[str]:
    """把一帧的能力调用压成短标签，供情景的"过程"行使用。"""
    steps: list[str] = []
    for call in decision.calls:
        detail = ""
        for value in (call.args or {}).values():
            text = str(value).strip()
            if text:
                detail = text.replace("\\", "/").rsplit("/", 1)[-1][:30]
                break
        steps.append(f"{call.capability} {detail}".strip())
    return steps


def _intent(decision) -> str:
    if decision.action is Action.ACTIVITY_START:
        return str(decision.args.get("goal") or "开始活动")
    if decision.target is not None:
        return f"{decision.action.value} -> {decision.target.friend_id}"
    return decision.action.value


def _fill_reply_context(decision, events: list[Event]) -> None:
    """把来信的 msg_id 补进 target，保证被动回复命中窗口。"""
    target = decision.target
    if target is None or decision.action not in (
        Action.CHAT_REPLY,
        Action.CHAT_REACH_OUT,
    ):
        return
    message = _latest_message(events, target.friend_id)
    if message is None:
        return
    if target.msg_id is None:
        target.msg_id = message.payload.get("msg_id")


def _latest_message(
    events: list[Event], friend_id: str | None
) -> Event | None:
    for event in reversed(events):
        if event.kind is not EventKind.MESSAGE:
            continue
        if friend_id and event.source.friend_id != friend_id:
            continue
        return event
    return None


def _speaker_for(decision, events: list[Event]) -> str | None:
    if decision.target is not None:
        return decision.target.friend_id
    for event in reversed(events):
        if event.kind is EventKind.MESSAGE and event.source.friend_id:
            return event.source.friend_id
    return None


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
        parts.append(f"target={target.friend_id}")
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
    if decision.next_wake_in is not None:
        parts.append(f"next_wake_in={decision.next_wake_in}")
    if decision.wake_when:
        parts.append(
            "wake_when=["
            + ",".join(predicate.type.value for predicate in decision.wake_when)
            + "]"
        )
    return " | ".join(parts)


def _describe_effect(effect, decision) -> str:
    """把一帧的实际效果压成一行，补全「唤醒 → 决策 → 落地」的日志闭环。"""
    parts: list[str] = []
    if decision.action is Action.IDLE:
        parts.append("静默")
    if effect.delivery is not None:
        parts.append(
            "已发送" if effect.delivery.ok else f"发送失败（{effect.delivery.detail}）"
        )
    if effect.activity is not None:
        parts.append(f"活动 {effect.activity.id}→{effect.activity.status.value}")
    if effect.capability_results:
        parts.append(f"能力结果 {len(effect.capability_results)} 个")
    if effect.pending_capabilities:
        parts.append(f"在途能力 {effect.pending_capabilities} 个")
    if effect.alarms:
        parts.append(
            "闹钟="
            + "、".join(f"{alarm.id}({alarm.kind.value})" for alarm in effect.alarms)
        )
    if effect.errors:
        parts.append("错误=" + "；".join(effect.errors))
    parts.append("终态" if decision.is_terminal else "待能力结果")
    return " | ".join(parts) or "无效果"


def _summarize(value: Any, limit: int = 1000) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    return text[:limit] + ("…" if len(text) > limit else "")


__all__ = ["Brain"]
