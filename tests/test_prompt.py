"""manager/cognition/prompt.py 的单测：L0 核心与 build_messages 的消息装配。"""

from __future__ import annotations

import json

from manager.cognition.prompt import L0_CORE, build_messages, _dump


def _situation():
    return {
        "now": "2026-10-21T12:55:29+08:00",
        "wake_reasons": [{"kind": "event", "detail": "capability_done: fs.read"}],
        "self": {"identity": "Alice", "mood": 0.25},
        "inbox": [],
    }


def _capabilities():
    return [
        {"name": "fs.read", "description": "读取文本文件"},
        {"name": "web.search", "description": "搜索网页"},
    ]


def test_returns_system_and_user_messages():
    messages = build_messages(_situation(), _capabilities())
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_system_message_is_l0_core():
    messages = build_messages(_situation(), _capabilities())
    assert messages[0]["content"] == L0_CORE


def test_l0_core_mentions_alice_and_rabbit():
    assert "Alice" in L0_CORE
    assert "兔子" in L0_CORE


def test_l0_core_mentions_decision_only():
    assert "Decision" in L0_CORE


def test_user_message_contains_situation_json():
    situation = _situation()
    user = build_messages(situation, _capabilities())[1]["content"]
    assert "## 当前情境" in user
    expected = json.dumps(situation, ensure_ascii=False, indent=2, default=str)
    assert expected in user


def test_user_message_contains_capabilities_json():
    capabilities = _capabilities()
    user = build_messages(_situation(), capabilities)[1]["content"]
    assert "## 可用能力" in user
    expected = json.dumps(capabilities, ensure_ascii=False, indent=2, default=str)
    assert expected in user


def test_user_message_contains_constraints_and_protocol():
    user = build_messages(_situation(), _capabilities())[1]["content"]
    assert "## 约束" in user
    assert "## 输出协议" in user
    assert "next_wake_in" in user
    assert "remember" in user


def test_user_message_keeps_chinese_unescaped():
    user = build_messages(_situation(), _capabilities())[1]["content"]
    assert "读取文本文件" in user


def test_empty_capabilities_serializes_as_empty_list():
    user = build_messages(_situation(), [])[1]["content"]
    assert "[]" in user


def test_dump_uses_indent_and_keeps_unicode():
    text = _dump({"名字": "Alice"})
    assert "\n" in text
    assert "  " in text
    assert "名字" in text


def test_dump_falls_back_to_str_for_unknown_objects():
    class Weird:
        def __str__(self):
            return "weird-value"

    text = _dump({"obj": Weird()})
    assert "weird-value" in text


def test_dump_handles_empty_containers():
    assert _dump({}) == "{}"
    assert _dump([]) == "[]"


def test_exports_l0_core_and_build_messages():
    import manager.cognition.prompt as prompt_module

    assert set(prompt_module.__all__) == {"L0_CORE", "build_messages"}
