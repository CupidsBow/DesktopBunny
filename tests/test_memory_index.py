"""memory/index.py：MEMORY.md 索引的解析、增删改与体积约束。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from manager.memory.index import MemoryIndex, parse_line, render_line
from manager.shared.types import MemoryIndexEntry


def _config(max_lines: int = 100, max_bytes: int = 100000) -> SimpleNamespace:
    return SimpleNamespace(
        memory_index_max_lines=max_lines,
        memory_index_max_bytes=max_bytes,
    )


def _entry(name: str = "a", path: str = "ep/a.md", description: str = "hello world", **kw) -> MemoryIndexEntry:
    return MemoryIndexEntry(name=name, path=path, description=description, **kw)


# ---- parse_line / render_line ----


def test_parse_line_with_trailer():
    entry = parse_line("- [a](ep/a.md) — hello world · v=0.5 a=0.2")
    assert entry is not None
    assert entry.name == "a"
    assert entry.path == "ep/a.md"
    assert entry.description == "hello world"
    assert entry.valence == pytest.approx(0.5)
    assert entry.arousal == pytest.approx(0.2)


def test_parse_line_without_trailer_leaves_scores_none():
    entry = parse_line("- [b](ep/b.md) — 只是描述")
    assert entry is not None
    assert entry.description == "只是描述"
    assert entry.valence is None
    assert entry.arousal is None


def test_parse_line_negative_scores():
    entry = parse_line("- [c](ep/c.md) — x · v=-0.3 a=0.7")
    assert entry is not None
    assert entry.valence == pytest.approx(-0.3)
    assert entry.arousal == pytest.approx(0.7)


def test_parse_line_rejects_garbage():
    assert parse_line("随便一行") is None
    assert parse_line("# 标题") is None
    assert parse_line("") is None


def test_render_line_round_trips():
    entry = _entry(description="多  个   空格", valence=0.25, arousal=0.5)
    line = render_line(entry)
    assert line == "- [a](ep/a.md) — 多 个 空格 · v=0.25 a=0.5"
    parsed = parse_line(line)
    assert parsed is not None
    assert parsed.path == entry.path
    assert parsed.description == "多 个 空格"


def test_render_line_omits_trailer_without_scores():
    assert render_line(_entry()) == "- [a](ep/a.md) — hello world"


# ---- MemoryIndex ----


def test_add_persists_and_loads(tmp_path):
    path = str(tmp_path / "MEMORY.md")
    index = MemoryIndex(path, _config())
    index.add(_entry())
    assert (tmp_path / "MEMORY.md").exists()

    reloaded = MemoryIndex(path, _config())
    entries = reloaded.entries()
    assert len(entries) == 1
    assert entries[0].path == "ep/a.md"


def test_add_same_path_replaces(tmp_path):
    index = MemoryIndex(str(tmp_path / "MEMORY.md"), _config())
    index.add(_entry(description="旧"))
    index.add(_entry(description="新"))
    entries = index.entries()
    assert len(entries) == 1
    assert entries[0].description == "新"


def test_update_description_existing(tmp_path):
    index = MemoryIndex(str(tmp_path / "MEMORY.md"), _config())
    index.add(_entry())
    index.update_description("ep/a.md", "改过了", valence=0.4, arousal=0.1)
    entry = index.entries()[0]
    assert entry.description == "改过了"
    assert entry.valence == pytest.approx(0.4)
    assert entry.arousal == pytest.approx(0.1)


def test_update_description_missing_creates(tmp_path):
    index = MemoryIndex(str(tmp_path / "MEMORY.md"), _config())
    index.update_description("ep/new.md", "新建的")
    entries = index.entries()
    assert len(entries) == 1
    assert entries[0].path == "ep/new.md"
    assert entries[0].name == "new"


def test_remove(tmp_path):
    index = MemoryIndex(str(tmp_path / "MEMORY.md"), _config())
    index.add(_entry())
    index.add(_entry(name="b", path="ep/b.md"))
    index.remove("ep/a.md")
    assert [e.path for e in index.entries()] == ["ep/b.md"]


def test_rebuild_drops_oldest_when_over_max_lines(tmp_path):
    index = MemoryIndex(str(tmp_path / "MEMORY.md"), _config(max_lines=2))
    index.rebuild([_entry(path="ep/1.md"), _entry(path="ep/2.md"), _entry(path="ep/3.md")])
    assert [e.path for e in index.entries()] == ["ep/2.md", "ep/3.md"]


def test_max_bytes_trims_from_front(tmp_path):
    index = MemoryIndex(str(tmp_path / "MEMORY.md"), _config(max_bytes=120))
    index.rebuild([
        _entry(name=str(i), path=f"ep/{i}.md", description="x" * 20) for i in range(6)
    ])
    entries = index.entries()
    assert entries, "至少要留下一条"
    assert MemoryIndex._size(entries) <= 120
    assert entries[-1].path == "ep/5.md"


def test_saved_file_has_header(tmp_path):
    path = tmp_path / "MEMORY.md"
    index = MemoryIndex(str(path), _config())
    index.add(_entry())
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Alice 的记忆索引")
    assert "- [a](ep/a.md) — hello world" in text
