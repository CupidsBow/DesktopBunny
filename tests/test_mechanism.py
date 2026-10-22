"""mechanism 单元测试：WakeController 与 StateClock。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from manager.mechanism.clocks import StateClock
from manager.mechanism.wake import WakeController
from manager.shared.types import BodyMode, WakeReason, WakeReasonKind


class StubConfig:
    settle_window = 1.5
    state_tick_interval = 10.0
    max_idle_seconds = 600.0
    max_idle_seconds_rest = 3600.0


# ----------------------------------------------------------------------
# WakeController
# ----------------------------------------------------------------------
def test_notify_appends_and_take_drains():
    wc = WakeController(StubConfig())
    wc.notify(WakeReason(kind=WakeReasonKind.EVENT, detail="state_tick"))
    wc.notify(WakeReason(kind=WakeReasonKind.ALARM, detail="alarm"))

    assert wc.has_pending()
    reasons = wc.take()
    assert [r.detail for r in reasons] == ["state_tick", "alarm"]
    assert not wc.has_pending()
    assert wc.take() == []


def test_notify_many_notifies_each():
    wc = WakeController(StubConfig())
    wc.notify_many(
        [
            WakeReason(kind=WakeReasonKind.EVENT, detail="a"),
            WakeReason(kind=WakeReasonKind.EVENT, detail="b"),
        ]
    )
    assert [r.detail for r in wc.peek()] == ["a", "b"]


def test_stopped_controller_ignores_notify():
    wc = WakeController(StubConfig())
    wc.stop()
    wc.notify(WakeReason(kind=WakeReasonKind.EVENT, detail="state_tick"))
    assert not wc.has_pending()

    wc.reset()
    wc.notify(WakeReason(kind=WakeReasonKind.EVENT, detail="state_tick"))
    assert wc.has_pending()


def test_settle_remaining_zero_for_non_message_reasons():
    wc = WakeController(StubConfig())
    wc.notify(WakeReason(kind=WakeReasonKind.EVENT, detail="state_tick"))
    assert wc.settle_remaining() == 0.0


def test_settle_remaining_shrinks_after_message():
    wc = WakeController(StubConfig())
    wc.notify(WakeReason(kind=WakeReasonKind.EVENT, detail="message: hi"))
    remaining = wc.settle_remaining()
    assert 0.0 < remaining <= StubConfig.settle_window


def test_wait_returns_false_on_timeout():
    wc = WakeController(StubConfig())
    assert wc.wait(0.01) is False


def test_wait_returns_true_when_pending():
    wc = WakeController(StubConfig())
    wc.notify(WakeReason(kind=WakeReasonKind.EVENT, detail="state_tick"))
    assert wc.wait(0.0) is True


# ----------------------------------------------------------------------
# StateClock
# ----------------------------------------------------------------------
def _now():
    return datetime(2026, 10, 21, 11, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def test_clock_due_true_when_never_ticked():
    clock = StateClock(StubConfig(), now_fn=_now)
    assert clock.due(BodyMode.AWAKE) is True
    assert clock.last_tick is None


def test_clock_interval_differs_by_mode():
    clock = StateClock(StubConfig(), now_fn=_now)
    assert clock.interval(BodyMode.AWAKE) == pytest.approx(10.0)
    assert clock.interval(BodyMode.REST) == pytest.approx(25.0)


def test_clock_max_idle_differs_by_mode():
    clock = StateClock(StubConfig(), now_fn=_now)
    assert clock.max_idle(BodyMode.AWAKE) == pytest.approx(600.0)
    assert clock.max_idle(BodyMode.REST) == pytest.approx(3600.0)


def test_clock_due_after_interval_elapses():
    t = [_now()]
    clock = StateClock(StubConfig(), now_fn=lambda: t[0])
    clock.mark()

    assert clock.due(BodyMode.AWAKE) is False
    t[0] += timedelta(seconds=9.9)
    assert clock.due(BodyMode.AWAKE) is False
    t[0] += timedelta(seconds=0.2)
    assert clock.due(BodyMode.AWAKE) is True


def test_clock_rest_mode_needs_longer_interval():
    t = [_now()]
    clock = StateClock(StubConfig(), now_fn=lambda: t[0])
    clock.mark()
    t[0] += timedelta(seconds=12)
    assert clock.due(BodyMode.AWAKE) is True
    assert clock.due(BodyMode.REST) is False


def test_clock_accepts_explicit_now():
    clock = StateClock(StubConfig(), now_fn=_now)
    clock.mark()
    later = _now() + timedelta(seconds=11)
    assert clock.due(BodyMode.AWAKE, now=later) is True
    assert clock.last_tick == _now()
