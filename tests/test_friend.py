"""朋友档案：首次建档、last_seen 刷新，以及情境里的 first_seen/last_seen 透出。"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

from manager.memory.memory import Memory
from manager.memory.store import MemoryStore, slugify
from manager.shared.config import BrainConfig, BrainPaths

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def make_store() -> MemoryStore:
    tmp = tempfile.mkdtemp()
    return MemoryStore(BrainPaths(root=tmp), BrainConfig(paths=BrainPaths(root=tmp)))


def make_memory() -> Memory:
    tmp = tempfile.mkdtemp()
    paths = BrainPaths(root=tmp)
    return Memory(paths, BrainConfig(paths=paths), now_fn=lambda: NOW)


# ----------------------------------------------------------------------
# touch_friend
# ----------------------------------------------------------------------
def test_touch_friend_creates_skeleton():
    store = make_store()
    target = store.touch_friend("u_123", now=NOW)
    assert target == f"friends/{slugify('u_123', 64)}.md"
    profile = store.read_friend("u_123")
    assert profile.first_seen == "2026-10-21"
    assert profile.last_seen == "2026-10-21"
    assert "还不了解" in profile.impression


def test_touch_friend_refreshes_last_seen_only():
    store = make_store()
    store.touch_friend("u_123", now=NOW)
    store.touch_friend("u_123", now=NOW + timedelta(days=2))
    profile = store.read_friend("u_123")
    assert profile.first_seen == "2026-10-21"
    assert profile.last_seen == "2026-10-23"


def test_touch_friend_is_idempotent_same_day():
    store = make_store()
    store.touch_friend("u_123", now=NOW)
    store.touch_friend("u_123", now=NOW + timedelta(hours=3))
    assert store.read_friend("u_123").last_seen == "2026-10-21"


# ----------------------------------------------------------------------
# Memory.view 透出
# ----------------------------------------------------------------------
def test_view_surfaces_first_and_last_seen():
    memory = make_memory()
    memory.ensure_friend("u_123")
    view = memory.view("u_123")
    assert view.speaker_first_seen == "2026-10-21"
    assert view.speaker_last_seen == "2026-10-21"
    assert "还不了解" in view.about_speaker


def test_view_without_profile_still_signals_stranger():
    memory = make_memory()
    view = memory.view("nobody")
    assert view.speaker_first_seen is None
    assert view.speaker_last_seen is None
    assert "还不了解" in view.about_speaker
