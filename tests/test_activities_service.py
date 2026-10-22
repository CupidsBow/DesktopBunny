"""ActivityService 的生命周期、推进与检查点。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from manager.activities.activities import ActivityService
from manager.activities.store import ActivityStore
from manager.shared.types import ActivityError, ActivityStatus, ActivityUpdate


TS = 1792548000.0  # 2026-10-21T10:00:00+08:00


class Clock:
    def __init__(self, start: float = TS) -> None:
        self.value = start

    def __call__(self):
        return datetime.fromtimestamp(self.value)

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def service(tmp_path, clock) -> ActivityService:
    store = ActivityStore(str(tmp_path / "activities.json"))
    store.load()
    config = SimpleNamespace(
        activity_max_consecutive_failures=3,
        activity_checkpoint_interval=60,
    )
    return ActivityService(store, config, now_fn=clock)


def test_start_creates_active_activity(service):
    activity = service.start("read", "读一读 activities.py")

    assert activity.id == "act_1"
    assert activity.status is ActivityStatus.ACTIVE
    assert activity.steps_used == 0
    assert activity.steps_limit == 8
    assert activity.progress == 0.0
    assert service.active().id == activity.id


def test_start_defaults_and_state(service):
    activity = service.start(
        "read", "读源码", steps_limit=3, note="先看 start", state={"phase": "a"}
    )

    assert activity.steps_limit == 3
    assert activity.state["phase"] == "a"
    assert activity.state["note"] == "先看 start"


def test_start_clamps_steps_limit(service):
    assert service.start("read", "多步", steps_limit=0).steps_limit == 1
    service.complete()
    assert service.start("read", "多步", steps_limit=999).steps_limit == 100


def test_start_requires_kind_and_goal(service):
    with pytest.raises(ActivityError):
        service.start("", "有目标")
    with pytest.raises(ActivityError):
        service.start("read", "   ")


def test_start_refuses_when_another_is_active(service):
    service.start("read", "第一个")
    with pytest.raises(ActivityError):
        service.start("read", "第二个")


def test_pause_and_resume(service, clock):
    activity = service.start("read", "读源码", note="开始")
    clock.advance(10)
    paused = service.pause(note="先歇会儿")

    assert paused.status is ActivityStatus.PAUSED
    assert paused.state["note"] == "先歇会儿"
    assert service.active() is None
    assert service.paused().id == activity.id

    resumed = service.resume()
    assert resumed.status is ActivityStatus.ACTIVE
    assert resumed.resume_when == []
    assert service.paused() is None


def test_pause_with_resume_when(service):
    service.start("read", "读源码")
    paused = service.pause(
        note="等人", resume_when=[{"type": "presence_changed", "args": {}}]
    )
    assert len(paused.resume_when) == 1

    resumed = service.resume()
    assert resumed.resume_when == []


def test_resume_requires_paused(service):
    with pytest.raises(ActivityError):
        service.resume()


def test_complete_sets_status_and_summary(service):
    service.start("read", "读源码")
    done = service.complete(summary="读完了")

    assert done.status is ActivityStatus.COMPLETED
    assert done.last_result == "读完了"
    assert done.checkpoint["outcome"] == "completed"
    assert service.active() is None


def test_complete_outcome_abandoned(service):
    service.start("read", "读源码")
    done = service.complete(outcome="abandoned")
    assert done.status is ActivityStatus.ABANDONED


def test_abandon_completed_outcome_maps_to_complete(service):
    service.start("read", "读源码")
    done = service.abandon(outcome="completed", summary="算了但算完成")
    assert done.status is ActivityStatus.COMPLETED


def test_fail_marks_failed_and_records_reason(service):
    activity = service.start("read", "读源码")
    failed = service.fail(activity.id, "读崩了")

    assert failed.status is ActivityStatus.FAILED
    assert failed.checkpoint["failure_reason"] == "读崩了"
    assert service.active() is None


def test_operate_without_activity_raises(service):
    with pytest.raises(ActivityError):
        service.complete()


def test_update_clamps_progress_and_keeps_result(service):
    service.start("read", "读源码")
    updated = service.update(
        ActivityUpdate(progress=1.7, last_result="读到一半", note="继续")
    )

    assert updated.progress == 1.0
    assert updated.last_result == "读到一半"
    assert updated.state["note"] == "继续"

    lowered = service.update(ActivityUpdate(progress=-0.5))
    assert lowered.progress == 0.0


def test_update_with_none_returns_active(service):
    service.start("read", "读源码")
    assert service.update(None).id == "act_1"


def test_record_step_returns_true_at_limit(service):
    service.start("read", "读源码", steps_limit=2)

    assert service.record_step("act_1") is False
    assert service.record_step("act_1") is True
    assert service.record_step("act_1") is False


def test_record_step_ignores_missing(service):
    assert service.record_step("act_404") is False


def test_record_failure_fails_after_threshold(service):
    service.start("read", "读源码")

    service.record_failure("act_1")
    service.record_failure("act_1")
    activity = service.record_failure("act_1")

    assert activity.consecutive_failures == 3
    assert activity.status is ActivityStatus.FAILED
    assert service.active() is None


def test_record_success_clears_failures(service):
    service.start("read", "读源码")
    service.record_failure("act_1")
    activity = service.record_success("act_1")

    assert activity.consecutive_failures == 0
    assert activity.status is ActivityStatus.ACTIVE


def test_due_checkpoint_respects_interval(service, clock):
    activity = service.start("read", "读源码")
    assert service.due_checkpoint() is None

    clock.advance(61)
    checkpoint = service.due_checkpoint()

    assert checkpoint is not None
    assert checkpoint.activity_id == activity.id
    assert checkpoint.reason == "interval"
    assert service.due_checkpoint() is None


def test_step_checkpoint_only_once_at_limit(service):
    service.start("read", "读源码", steps_limit=1)
    service.record_step("act_1")

    first = service.step_checkpoint("act_1")
    assert first is not None
    assert first.reason == "steps_limit"
    assert service.step_checkpoint("act_1") is None


def test_step_checkpoint_before_limit_is_none(service):
    service.start("read", "读源码", steps_limit=3)
    service.record_step("act_1")
    assert service.step_checkpoint("act_1") is None


def test_open_activities_and_get(service):
    service.start("read", "读源码")
    service.pause()

    assert [a.id for a in service.open_activities()] == ["act_1"]
    assert service.get("act_1") is not None
    assert service.get("act_404") is None


def test_is_restored_after_reload(tmp_path, clock):
    path = str(tmp_path / "activities.json")
    store = ActivityStore(path)
    store.load()
    config = SimpleNamespace(
        activity_max_consecutive_failures=3,
        activity_checkpoint_interval=60,
    )
    ActivityService(store, config, now_fn=clock).start("read", "读源码")

    restarted_store = ActivityStore(path)
    restarted_store.load()
    restarted = ActivityService(restarted_store, config, now_fn=clock)

    activity = restarted.get("act_1")
    assert activity.status is ActivityStatus.PAUSED
    assert restarted.is_restored(activity) is True
    assert restarted.is_restored(restarted.get("act_404") or activity) is True
