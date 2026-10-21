"""PerceptionService 单测：闲置秒数换算在场、前台粗分类、昼夜、QQ 连接。

不启动线程；idle_fn / now_fn 全用桩，ForegroundTracker 用真实实现。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from manager.perception.perception import PerceptionService
from manager.shared.config import BrainConfig


NOW = datetime(2026, 10, 21, 11, 0, 0, tzinfo=timezone(timedelta(hours=8)))
TZ = timezone(timedelta(hours=8))


def make_config(**overrides):
    cfg = BrainConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_service(idle_seconds, cfg=None, now=NOW):
    cfg = cfg or make_config()
    return PerceptionService(
        cfg,
        idle_fn=lambda: idle_seconds,
        now_fn=lambda: now,
    )


def test_idle_none_means_presence_unknown():
    svc = make_service(None)
    p = svc.tick(qq_connected=True)
    assert p.someone_present is None
    assert p.idle_seconds is None


def test_idle_below_threshold_is_present():
    cfg = make_config()
    svc = make_service(cfg.presence_idle_threshold - 1, cfg=cfg)
    p = svc.tick()
    assert p.someone_present is True
    assert p.idle_seconds == round(cfg.presence_idle_threshold - 1, 1)


def test_idle_above_threshold_is_absent():
    cfg = make_config()
    svc = make_service(cfg.presence_idle_threshold + 100, cfg=cfg)
    assert svc.tick().someone_present is False


def test_idle_exactly_on_threshold_is_absent():
    cfg = make_config()
    svc = make_service(cfg.presence_idle_threshold, cfg=cfg)
    assert svc.tick().someone_present is False


def test_qq_connected_is_coerced_to_bool():
    svc = make_service(1)
    assert svc.tick(qq_connected=None).qq_connected is False
    assert svc.tick(qq_connected=1).qq_connected is True


def test_foreground_is_classified_to_a_string():
    svc = make_service(1)
    app = svc.tick().foreground_app
    assert isinstance(app, str)
    assert app


def test_at_night_flips_with_time():
    cfg = make_config()
    day = PerceptionService(
        cfg, idle_fn=lambda: 1,
        now_fn=lambda: datetime(2026, 10, 21, 12, 0, tzinfo=TZ),
    )
    night = PerceptionService(
        cfg, idle_fn=lambda: 1,
        now_fn=lambda: datetime(2026, 10, 21, 3, 0, tzinfo=TZ),
    )
    assert day.tick().at_night is False
    assert night.tick().at_night is True


def test_system_state_marks_missing_snapshot():
    p = make_service(1).tick(bunny_snapshot=None)
    assert p.system_state.get("bunny_snapshot") == "missing"
    assert "foreground_dwell" in p.system_state


def test_system_state_accepts_snapshot_dict():
    from manager.shared.types import BunnySnapshot

    p = make_service(1).tick(bunny_snapshot={"satiety": 50.0})
    assert p.system_state.get("bunny_snapshot") != "missing"


def test_system_state_ignores_junk_snapshot():
    p = make_service(1).tick(bunny_snapshot="nonsense")
    assert p.system_state.get("bunny_snapshot") == "missing"


def test_last_perception_is_cached():
    svc = make_service(1)
    p = svc.tick()
    assert svc.last is p
