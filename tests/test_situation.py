"""SituationBuilder 单元测试：纯装配，不启线程、不碰磁盘。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from manager.body.self_state import SelfStateDeriver
from manager.cognition.situation import SituationBuilder
from manager.shared.config import BrainConfig
from manager.shared.timeutil import to_iso
from manager.shared.types import BodyMode, BodyState, EventKind, Perception


NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
AT = to_iso(NOW)


class FakeMemory:
    """只实现 SituationBuilder 用到的两个入口。"""

    def __init__(self):
        self.seen_speakers = []

    def view(self, speaker):
        self.seen_speakers.append(speaker)
        return SimpleNamespace(
            self_summary='# 我是谁',
            goals_summary='# 长期目标',
            preferences_summary='# 偏好',
            about_speaker=f'about:{speaker}',
            index=[],
        )

    def recent_context(self):
        return '[最近对话]'


def make_body(**overrides) -> BodyState:
    values = dict(
        satiety=63.0,
        energy=0.5,
        alertness=0.3,
        social_battery=0.99,
        circadian=0.8,
        presence=False,
        mode=BodyMode.AWAKE,
    )
    values.update(overrides)
    return BodyState(**values)


def make_perception(**overrides) -> Perception:
    values = dict(
        now=NOW,
        someone_present=False,
        foreground_app='other',
        at_night=False,
        qq_connected=True,
        idle_seconds=12234.1,
    )
    values.update(overrides)
    return Perception(**values)


def make_self_state(body: BodyState):
    return SelfStateDeriver(BrainConfig()).derive(body, None, [], NOW)


def make_builder():
    memory = FakeMemory()
    return SituationBuilder(BrainConfig(), memory, now_fn=lambda: NOW), memory


def make_message_event(**overrides):
    values = dict(
        id='ev_1',
        kind=EventKind.MESSAGE,
        at=AT,
        status=SimpleNamespace(value='unhandled'),
        source=SimpleNamespace(friend_id='u1', conversation_id='c1'),
        payload={'text': 'hi', 'msg_id': 'm1', 'conversation_type': 'c2c'},
        is_handled=False,
        arrived_during=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_wake_reason(detail='闹钟', event_id=None):
    return SimpleNamespace(
        kind=SimpleNamespace(value='event'), detail=detail, event_id=event_id
    )


def build(builder, **kwargs):
    body = kwargs.pop('body', None) or make_body()
    perception = kwargs.pop('perception', None) or make_perception()
    defaults = dict(
        perception=perception,
        body=body,
        self_state=make_self_state(body),
        events=[],
        capabilities=['fs.read'],
        wake_reasons=[],
    )
    defaults.update(kwargs)
    return builder.build(**defaults)


# ----------------------------------------------------------------------
# 骨架
# ----------------------------------------------------------------------

def test_build_skeleton():
    builder, memory = make_builder()
    situation = build(builder)
    assert situation['now'].startswith('2026-10-21T12:00:00')
    assert situation['self']['identity'] == 'Alice'
    assert situation['activity'] is None
    assert situation['inbox'] == []
    assert situation['capabilities'] == ['fs.read']
    assert memory.seen_speakers == [None]


def test_body_values_rounded():
    builder, _ = make_builder()
    situation = build(builder, body=make_body(satiety=63.04, energy=0.4444))
    assert situation['body']['satiety'] == 63.0
    assert situation['body']['energy'] == 0.444
    assert situation['body']['mode'] == 'awake'


def test_environment_reflects_perception():
    builder, _ = make_builder()
    situation = build(
        builder,
        perception=make_perception(someone_present=True, idle_seconds=5.54),
    )
    assert situation['environment']['someone_present'] is True
    assert situation['environment']['idle_seconds'] == 5.5


def test_turn_and_delivery_defaults():
    builder, _ = make_builder()
    situation = build(builder)
    assert situation['turn'] == {
        'intent': None,
        'frames_used': 0,
        'pending_tool_results': [],
    }
    assert situation['delivery'] == {'last_ok': None, 'detail': None}


def test_constraints_come_from_config():
    builder, _ = make_builder()
    situation = build(builder)
    config = BrainConfig()
    assert situation['constraints']['max_idle_seconds'] == config.max_idle_seconds
    assert situation['constraints']['frame_timeout_seconds'] == config.frame_timeout_seconds


def test_notes_are_kept():
    builder, _ = make_builder()
    situation = build(builder, notes=['记得喝水'])
    assert situation['notes'] == ['记得喝水']


# ----------------------------------------------------------------------
# wake_reasons / inbox
# ----------------------------------------------------------------------

def test_wake_reason_includes_event_id():
    builder, _ = make_builder()
    situation = build(
        builder,
        wake_reasons=[make_wake_reason('闹钟叫醒'), make_wake_reason('事件', 'ev_9')],
    )
    assert situation['wake_reasons'][0] == {'kind': 'event', 'detail': '闹钟叫醒'}
    assert situation['wake_reasons'][1]['event_id'] == 'ev_9'


def test_inbox_skips_handled_events():
    builder, _ = make_builder()
    situation = build(
        builder,
        events=[make_message_event(), make_message_event(id='ev_2', is_handled=True)],
    )
    assert [event['id'] for event in situation['inbox']] == ['ev_1']


def test_message_event_fields():
    builder, _ = make_builder()
    situation = build(builder, events=[make_message_event()])
    event = situation['inbox'][0]
    assert event['kind'] == 'message'
    assert event['friend_id'] == 'u1'
    assert event['conversation_id'] == 'c1'
    assert event['text'] == 'hi'
    assert event['msg_id'] == 'm1'
    assert event['conversation_type'] == 'c2c'


def test_non_message_event_keeps_payload():
    builder, _ = make_builder()
    event = make_message_event(kind='capability_done', payload={'call_id': 'x'})
    situation = build(builder, events=[event])
    assert situation['inbox'][0]['payload'] == {'call_id': 'x'}


def test_current_speaker_goes_to_memory_view():
    builder, memory = make_builder()
    events = [
        make_message_event(id='ev_1', source=SimpleNamespace(friend_id='u1', conversation_id='c1')),
        make_message_event(id='ev_2', source=SimpleNamespace(friend_id='u2', conversation_id='c2')),
    ]
    situation = build(builder, events=events)
    assert memory.seen_speakers == ['u2']
    assert situation['memories']['about_speaker'] == 'about:u2'
