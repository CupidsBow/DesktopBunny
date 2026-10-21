"""ActivityStore 的读写与重启恢复。"""

from __future__ import annotations

import json

import pytest

from manager.activities.store import ActivityStore
from manager.shared.types import Activity, ActivityStatus


TS = 1792548000.0  # 2026-10-21T10:00:00+08:00


def make_activity(**overrides) -> Activity:
    data = {
        "id": "act_1",
        "kind": "read",
        "goal": "读一读源码",
        "state": {"phase": "reading"},
        "created_at": TS,
        "updated_at": TS,
        "steps_used": 0,
        "steps_limit": 10,
        "status": ActivityStatus.ACTIVE.value,
        "checkpoint": {},
        "last_result": "",
        "resume_when": [],
        "consecutive_failures": 0,
    }
    data.update(overrides)
    return Activity.from_dict(data)


@pytest.fixture()
def store_path(tmp_path) -> str:
    return str(tmp_path / "activities.json")


def test_load_missing_file_returns_empty(store_path):
    store = ActivityStore(store_path)
    assert store.load() == []
    assert store.all() == []
    assert store.restored_ids == set()


def test_upsert_and_get_roundtrip(store_path):
    store = ActivityStore(store_path)
    activity = make_activity()
    store.upsert(activity)

    got = store.get("act_1")
    assert got is not None
    assert got.id == "act_1"
    assert got.goal == "读一读源码"
    assert got.status is ActivityStatus.ACTIVE

    assert json.loads(open(store_path, encoding="utf-8").read())["activities"][0]["id"] == "act_1"


def test_get_missing_returns_none(store_path):
    store = ActivityStore(store_path)
    assert store.get("act_404") is None


def test_upsert_replaces_existing(store_path):
    store = ActivityStore(store_path)
    store.upsert(make_activity())
    store.upsert(make_activity(goal="换个目标", steps_used=3))

    all_items = store.all()
    assert len(all_items) == 1
    assert all_items[0].goal == "换个目标"
    assert all_items[0].steps_used == 3


def test_all_returns_copies(store_path):
    store = ActivityStore(store_path)
    store.upsert(make_activity())

    snapshot = store.all()[0]
    snapshot.goal = "被外部改掉了"

    assert store.get("act_1").goal == "读一读源码"


def test_next_id_increments(store_path):
    store = ActivityStore(store_path)
    assert store.next_id() == "act_1"

    store.upsert(make_activity(id="act_3"))
    store.upsert(make_activity(id="act_7"))
    assert store.next_id() == "act_8"

    store.upsert(make_activity(id="act_8"))
    assert store.next_id() == "act_9"


def test_next_id_ignores_non_numeric_suffix(store_path):
    store = ActivityStore(store_path)
    store.upsert(make_activity(id="act_x"))
    assert store.next_id() == "act_1"


def test_load_turns_active_into_paused_and_writes_back(store_path):
    store = ActivityStore(store_path)
    store.upsert(make_activity())

    restarted = ActivityStore(store_path)
    loaded = restarted.load()

    assert len(loaded) == 1
    assert loaded[0].status is ActivityStatus.PAUSED
    assert loaded[0].checkpoint.get("restored_from_active") is True
    assert restarted.restored_ids == {"act_1"}

    on_disk = json.loads(open(store_path, encoding="utf-8").read())["activities"][0]
    assert on_disk["status"] == ActivityStatus.PAUSED.value
    assert on_disk["checkpoint"]["restored_from_active"] is True


def test_load_leaves_finished_activities_alone(store_path):
    store = ActivityStore(store_path)
    store.upsert(make_activity(status=ActivityStatus.COMPLETED.value))

    restarted = ActivityStore(store_path)
    loaded = restarted.load()

    assert loaded[0].status is ActivityStatus.COMPLETED
    assert restarted.restored_ids == set()


def test_load_broken_json_returns_empty(store_path):
    with open(store_path, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    store = ActivityStore(store_path)
    assert store.load() == []


def test_load_accepts_dict_wrapper_and_skips_bad_records(store_path):
    good = make_activity().to_dict()
    payload = {"activities": ["oops", 42, good]}
    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)

    store = ActivityStore(store_path)
    loaded = store.load()

    assert [item.id for item in loaded] == ["act_1"]


def test_load_is_idempotent(store_path):
    store = ActivityStore(store_path)
    store.upsert(make_activity())

    first = store.load()
    second = store.load()

    assert [item.id for item in first] == [item.id for item in second]


def test_restart_after_restore_left_nothing_active(store_path):
    ActivityStore(store_path).upsert(make_activity())

    first = ActivityStore(store_path)
    first.load()

    again = ActivityStore(store_path)
    again.load()

    assert again.restored_ids == set()
