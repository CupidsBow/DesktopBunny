"""Body / SelfStateDeriver 单元测试：纯状态演化，不启线程。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from manager.body.body import Body
from manager.body.self_state import SelfStateDeriver
from manager.shared.config import BrainConfig
from manager.shared.types import (
    BodyMode,
    BodyState,
    BunnySnapshot,
    Perception,
)


NOW = datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture()
def config(tmp_path):
    return BrainConfig()


def perception_at(now: datetime, *, present: bool = False) -> Perception:
    return Perception(
        now=now,
        someone_present=present,
        foreground_app='other',
        at_night=False,
        qq_connected=True,
        idle_seconds=0.0,
    )


# ----------------------------------------------------------------------
# Body
# ----------------------------------------------------------------------

def test_body_initial_state(config):
    body = Body(config)
    assert 0.0 <= body.state.satiety <= 100.0
    assert 0.0 <= body.state.energy <= 1.0
    assert body.mode is BodyMode.AWAKE


def test_body_tick_updates_circadian_and_presence(config):
    body = Body(config)
    state = body.tick(perception_at(NOW, present=True), dt=0.0)
    assert state.presence is True
    assert 0.0 <= state.circadian <= 1.0


def test_body_satiety_follows_snapshot_and_clamps(config):
    body = Body(config)
    body.tick(perception_at(NOW), dt=0.0, bunny_snapshot=BunnySnapshot(satiety=140.0))
    assert body.state.satiety == 100.0

    body.tick(perception_at(NOW), dt=0.0, bunny_snapshot=BunnySnapshot(satiety=-5.0))
    assert body.state.satiety == 0.0


def test_body_satiety_stale_when_no_snapshot(config):
    body = Body(config)
    body.tick(perception_at(NOW), dt=0.0, bunny_snapshot=BunnySnapshot(satiety=60.0))
    assert getattr(body, 'satiety_stale', False) is False
    body.tick(perception_at(NOW), dt=0.0, bunny_snapshot=None)
    assert getattr(body, 'satiety_stale', True) is True
    assert body.state.satiety == 60.0


def test_body_energy_drains_while_awake(config):
    body = Body(config)
    before = body.state.energy
    body.tick(perception_at(NOW), dt=3600.0)
    assert body.state.energy < before


def test_body_energy_recovers_in_rest(config):
    body = Body(config)
    body.state.energy = 0.3
    body.set_mode(BodyMode.REST)
    body.tick(perception_at(NOW), dt=3600.0)
    assert body.state.energy > 0.3


def test_body_energy_clamped(config):
    body = Body(config)
    body.state.energy = 0.99
    body.set_mode(BodyMode.REST)
    body.tick(perception_at(NOW), dt=10 ** 6)
    assert body.state.energy == 1.0


def test_body_alertness_rises_with_interactions(config):
    body = Body(config)
    body.state.alertness = 0.1
    body.tick(perception_at(NOW), dt=0.0, interactions=2)
    assert body.state.alertness > 0.1


def test_body_social_battery_drops_on_interaction_and_recovers(config):
    body = Body(config)
    body.state.social_battery = 0.5
    body.tick(perception_at(NOW), dt=0.0, interactions=2)
    assert body.state.social_battery < 0.5
    low = body.state.social_battery
    body.tick(perception_at(NOW), dt=3600.0)
    assert body.state.social_battery > low


def test_body_set_mode_ignores_unknown(config):
    body = Body(config)
    before = body.mode
    body.set_mode('nonsense')
    assert body.mode is before
    body.set_mode('rest')
    assert body.mode is BodyMode.REST


def test_body_stimulate_and_note_interaction_clamped(config):
    body = Body(config)
    body.state.alertness = 0.95
    body.stimulate(0.5)
    assert body.state.alertness == 1.0

    body.state.social_battery = 0.01
    body.note_interaction(5)
    assert body.state.social_battery == 0.0


def test_body_snapshot_is_copy(config):
    body = Body(config)
    snap = body.snapshot()
    original = body.state.satiety
    snap.satiety = 1.0
    assert body.state.satiety == original


# ----------------------------------------------------------------------
# SelfStateDeriver
# ----------------------------------------------------------------------

def make_episode(speaker: str, valence: float, *, minutes_ago: float = 0.0):
    when = NOW - timedelta(minutes=minutes_ago)
    return SimpleNamespace(
        metadata=SimpleNamespace(
            time=when.isoformat(),
            speaker=speaker,
            valence=valence,
            arousal=0.5,
        ),
        content='...',
    )


def base_body(**overrides) -> BodyState:
    values = dict(
        satiety=80.0,
        energy=0.9,
        alertness=0.3,
        social_battery=1.0,
        circadian=1.0,
        presence=False,
        mode=BodyMode.AWAKE,
    )
    values.update(overrides)
    return BodyState(**values)


def test_derive_returns_self_state(config):
    deriver = SelfStateDeriver(config)
    state = deriver.derive(base_body(), None, [], NOW)
    assert state.mood == 0.0
    names = {u.name for u in state.urges}
    assert {'饥饿', '孤独', '睡意', '日常'} <= names


def test_mood_layers_valence(config):
    deriver = SelfStateDeriver(config)
    happy = deriver.derive(base_body(), None, [make_episode('u1', 0.8)], NOW)
    sad = deriver.derive(base_body(), None, [make_episode('u1', -0.8)], NOW)
    assert happy.mood > 0 > sad.mood


def test_hunger_and_lonely_urges_from_body(config):
    deriver = SelfStateDeriver(config)
    state = deriver.derive(base_body(satiety=0.0, social_battery=0.0), None, [], NOW)
    urges = {u.name: u for u in state.urges}
    assert urges['饥饿'].level == 1.0
    assert urges['孤独'].level == 1.0


def test_attitude_and_relation_tracked_per_speaker(config):
    deriver = SelfStateDeriver(config)
    episodes = [make_episode('u1', 0.9, minutes_ago=1) for _ in range(5)]
    state = deriver.derive(base_body(), None, episodes, NOW)
    assert 'u1' in state.attitudes
    assert state.attitudes['u1'].value > 0


@pytest.mark.parametrize(
    'value,label',
    [(0.9, '兴奋'), (0.2, '轻快'), (0.0, '平静'), (-0.3, '低落'), (-0.9, '烦躁')],
)
def test_mood_label_bands(value, label):
    assert SelfStateDeriver._mood_label(value) == label
