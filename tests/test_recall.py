"""``memory.recall`` 的关键词召回测试。"""

from __future__ import annotations

from types import SimpleNamespace

from manager.memory.recall import Recall, _tokens


class FakeIndex:
    def __init__(self, entries=None):
        self._entries = list(entries or [])

    def entries(self):
        return list(self._entries)


class FakeStore:
    def __init__(self, bodies=None, episodes=None, fail_paths=None):
        self.bodies = dict(bodies or {})
        self.episodes = list(episodes or [])
        self.fail_paths = set(fail_paths or [])

    def read_text(self, path):
        if path in self.fail_paths:
            raise OSError("boom")
        return self.bodies.get(path, "")

    def list_episodes(self):
        return list(self.episodes)


def make_recall(entries=None, bodies=None, episodes=None, limit=10, fail_paths=None):
    config = SimpleNamespace(recall_max_episodes=limit)
    return Recall(
        store=FakeStore(bodies, episodes, fail_paths),
        index=FakeIndex(entries),
        config=config,
    )


def make_entry(
    name="ep", path="episodes/ep.md", description="", valence=None, arousal=None
):
    return SimpleNamespace(
        name=name,
        path=path,
        description=description,
        valence=valence,
        arousal=arousal,
    )


def make_episode(
    name="ep", path="episodes/ep.md", description="", valence=None, arousal=None
):
    return SimpleNamespace(
        name=name,
        path=path,
        description=description,
        metadata=SimpleNamespace(valence=valence, arousal=arousal),
    )


# ----------------------------------------------------------------------
# 输入校验
# ----------------------------------------------------------------------
def test_empty_query_returns_empty():
    recall = make_recall(entries=[make_entry(description="兔子")])
    assert recall.recall("") == []


def test_blank_query_returns_empty():
    recall = make_recall(entries=[make_entry(description="兔子")])
    assert recall.recall("   \n\t ") == []


def test_query_without_tokens_returns_empty():
    recall = make_recall(entries=[make_entry(description="兔子")])
    assert recall.recall("!!! ??? ...") == []


# ----------------------------------------------------------------------
# 命中与打分
# ----------------------------------------------------------------------
def test_description_hit_returns_candidate():
    recall = make_recall(
        entries=[
            make_entry(name="carrot", path="episodes/carrot.md", description="在厨房找到胡萝卜")
        ]
    )
    result = recall.recall("胡萝卜")
    assert len(result) == 1
    assert result[0]["ref"] == "episodes/carrot.md"
    assert result[0]["name"] == "carrot"
    assert result[0]["summary"] == "在厨房找到胡萝卜"
    assert result[0]["score"] > 0


def test_path_hit_counts_too():
    recall = make_recall(
        entries=[make_entry(name="ep", path="episodes/carrot-note.md", description="")]
    )
    assert len(recall.recall("carrot")) == 1


def test_full_query_substring_bonus_beats_partial():
    recall = make_recall(
        entries=[
            make_entry(name="a", path="a.md", description="memory recall"),
            make_entry(name="b", path="b.md", description="memory only"),
        ]
    )
    result = recall.recall("memory recall")
    assert [item["name"] for item in result] == ["a", "b"]
    assert result[0]["score"] > result[1]["score"] + 2.0


def test_body_hit_adds_weighted_score():
    with_body = make_recall(
        entries=[make_entry(name="hit", path="hit.md", description="carrot")],
        bodies={"hit.md": "the carrot is here"},
    )
    without = make_recall(
        entries=[make_entry(name="hit", path="hit.md", description="carrot")]
    )
    assert with_body.recall("carrot")[0]["score"] > without.recall("carrot")[0]["score"]


def test_summary_falls_back_to_body_first_line():
    recall = make_recall(
        entries=[make_entry(name="e", path="carrot.md", description="")],
        bodies={"carrot.md": "第一行摘要\n第二行内容"},
    )
    result = recall.recall("carrot")
    assert result and result[0]["summary"] == "第一行摘要"


def test_read_body_error_is_swallowed():
    recall = make_recall(
        entries=[make_entry(name="e", path="e.md", description="carrot")],
        fail_paths={"e.md"},
    )
    result = recall.recall("carrot")
    assert len(result) == 1
    assert result[0]["score"] > 0


def test_body_not_read_when_no_hit():
    recall = make_recall(
        entries=[make_entry(name="e", path="e.md", description="兔子")],
        fail_paths={"e.md"},
    )
    assert recall.recall("carrot") == []


def test_salience_bonus_uses_valence_and_arousal():
    recall = make_recall(
        entries=[
            make_entry(name="plain", path="plain.md", description="carrot"),
            make_entry(
                name="vivid", path="vivid.md", description="carrot", valence=-1.0, arousal=1.0
            ),
        ]
    )
    result = recall.recall("carrot")
    assert [item["name"] for item in result] == ["vivid", "plain"]


# ----------------------------------------------------------------------
# 候选数量与退化路径
# ----------------------------------------------------------------------
def test_limit_from_config():
    entries = [
        make_entry(name=f"e{i}", path=f"e{i}.md", description="carrot") for i in range(5)
    ]
    recall = make_recall(entries=entries, limit=2)
    assert len(recall.recall("carrot")) == 2


def test_explicit_limit_overrides_config():
    entries = [
        make_entry(name=f"e{i}", path=f"e{i}.md", description="carrot") for i in range(5)
    ]
    recall = make_recall(entries=entries, limit=10)
    assert len(recall.recall("carrot", limit=3)) == 3


def test_falls_back_to_episodes_when_index_empty():
    recall = make_recall(
        entries=[],
        episodes=[
            make_episode(
                name="old",
                path="episodes/old.md",
                description="在厨房找到胡萝卜",
                valence=0.5,
                arousal=0.5,
            )
        ],
    )
    result = recall.recall("胡萝卜")
    assert [item["name"] for item in result] == ["old"]


# ----------------------------------------------------------------------
# 分词
# ----------------------------------------------------------------------
def test_tokens_include_cjk_bigrams():
    tokens = _tokens("胡萝卜")
    assert "胡" in tokens and "萝" in tokens and "卜" in tokens
    assert "胡萝" in tokens and "萝卜" in tokens
    assert "胡萝卜" not in tokens


def test_tokens_lowercase_words():
    assert _tokens("Carrot Bunny") == {"carrot", "bunny"}
