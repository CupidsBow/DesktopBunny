"""L2/L3 文件读写：frontmatter、命名、原子写。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Mapping

from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.timeutil import now_local, parse_iso, to_iso
from manager.shared.types import Episode, FriendProfile, MemoryMetadata

logger = logging.getLogger(__name__)

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            return text
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~"):
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text or text != text.strip() or any(ch in text for ch in ':#"\n'):
        return json.dumps(text, ensure_ascii=False)
    return text


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 ``---`` 包围的简单 YAML；只支持两层键值。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text

    data: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):
            key, _, value = raw.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                data[key] = _parse_scalar(value)
                current = None
            else:
                current = {}
                data[key] = current
        elif current is not None:
            key, _, value = raw.strip().partition(":")
            current[key.strip()] = _parse_scalar(value)
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return data, body


def render_frontmatter(data: Mapping[str, Any]) -> str:
    if not data:
        return ""
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, Mapping):
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"  {sub_key}: {_dump_scalar(sub_value)}")
        else:
            lines.append(f"{key}: {_dump_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def slugify(text: str, limit: int = 40) -> str:
    text = _INVALID_FILENAME.sub("", text or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = text.strip("-.")
    return (text or "memory")[:limit]


class MemoryStore:
    """Alice 记忆目录的底层读写。"""

    def __init__(self, paths: BrainPaths, config: BrainConfig) -> None:
        self.paths = paths
        self.config = config

    def ensure_layout(self) -> None:
        self.paths.ensure_directories()
        defaults = {
            self.paths.self_path: "# 我是谁\n\n（这里会随着经历慢慢长出来。）\n",
            self.paths.goals_path: "# 长期目标\n\n（还没有写下的目标。）\n",
            self.paths.preferences_path: "# 偏好\n\n（还没有总结出稳定的偏好。）\n",
            self.paths.index_path: "# Alice 的记忆索引\n\n",
        }
        for path, content in defaults.items():
            if not os.path.exists(path):
                self._atomic_write(path, content)

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------
    def normalize_target(self, target: str) -> str:
        """把写操作 target 归一化为记忆目录下的相对路径。"""
        text = str(target or "").replace("\\", "/").strip().lstrip("/")
        if text.startswith("memory/"):
            text = text[len("memory/") :]
        if not text:
            return ""
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise ValueError(f"非法记忆路径: {target!r}")
        return "/".join(parts)

    def path_for(self, target: str) -> str:
        relative = self.normalize_target(target)
        if not relative:
            raise ValueError("记忆路径为空")
        path = os.path.abspath(os.path.join(self.paths.memory_dir, relative))
        root = os.path.abspath(self.paths.memory_dir)
        if os.path.commonpath([root, path]) != root:
            raise ValueError(f"记忆路径越界: {target!r}")
        return path

    # ------------------------------------------------------------------
    # 情景
    # ------------------------------------------------------------------
    def write_episode(
        self,
        *,
        description: str,
        content: str,
        metadata: MemoryMetadata,
        name_hint: str | None = None,
    ) -> Episode:
        description = (description or content or "一段经历").strip().splitlines()[0]
        when = parse_iso(metadata.time) or now_local()
        metadata.time = metadata.time or to_iso(when)
        speaker = metadata.speaker or "alice"
        stem = slugify(name_hint or description)
        name = f"{when:%Y-%m-%d}-{slugify(speaker, 24)}-{stem}"
        name = self._unique_episode_name(name)
        relative = f"episodes/{name}.md"
        path = self.path_for(relative)

        front = {
            "name": name,
            "description": description,
            "metadata": {
                "type": "episodic",
                "speaker": metadata.speaker,
                "conversation": metadata.conversation,
                "time": metadata.time,
                "valence": metadata.valence,
                "emotion": metadata.emotion,
                "arousal": metadata.arousal,
            },
        }
        text = render_frontmatter(front) + "\n" + (content or description).strip() + "\n"
        self._atomic_write(path, text)
        return Episode(
            name=name,
            description=description,
            metadata=MemoryMetadata.from_dict(front["metadata"]),
            body=(content or description).strip(),
            path=relative,
        )

    def _unique_episode_name(self, name: str) -> str:
        candidate = name
        counter = 2
        while os.path.exists(self.path_for(f"episodes/{candidate}.md")):
            candidate = f"{name}-{counter}"
            counter += 1
        return candidate

    def read_episode(self, path: str) -> Episode:
        absolute = self.path_for(path)
        with open(absolute, "r", encoding="utf-8") as handle:
            text = handle.read()
        data, body = parse_frontmatter(text)
        metadata = data.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        return Episode(
            name=str(data.get("name") or os.path.splitext(os.path.basename(path))[0]),
            description=str(data.get("description") or ""),
            metadata=MemoryMetadata.from_dict(metadata),
            body=body.strip(),
            path=self.normalize_target(path),
        )

    def list_episodes(self) -> list[Episode]:
        episodes: list[Episode] = []
        directory = self.paths.episodes_dir
        if not os.path.isdir(directory):
            return episodes
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".md"):
                continue
            relative = f"episodes/{filename}"
            try:
                episodes.append(self.read_episode(relative))
            except (OSError, ValueError) as exc:
                logger.warning("跳过损坏的情景记忆 %s：%s", relative, exc)
        episodes.sort(key=lambda item: item.metadata.time or "")
        return episodes

    # ------------------------------------------------------------------
    # L3
    # ------------------------------------------------------------------
    def read_text(self, target: str) -> str:
        path = self.path_for(target)
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def write_semantic(
        self,
        target: str,
        content: str,
        metadata: MemoryMetadata | None = None,
        description: str | None = None,
    ) -> str:
        relative = self.normalize_target(target)
        if not relative:
            raise ValueError("语义记忆 target 为空")
        path = self.path_for(relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        content = (content or "").strip()

        if relative.startswith("friends/"):
            if not content.startswith("---"):
                friend_id = os.path.splitext(os.path.basename(relative))[0]
                summary = description or _first_line(content) or friend_id
                front = {
                    "name": friend_id,
                    "description": summary,
                    "metadata": {
                        "type": "friend",
                        "first_seen": to_iso(now_local())[:10],
                        "last_seen": to_iso(now_local())[:10],
                    },
                }
                content = render_frontmatter(front) + "\n" + content + "\n"
        else:
            content = content + "\n"

        self._atomic_write(path, content)
        return relative

    def read_friend(self, friend_id: str) -> FriendProfile:
        relative = f"friends/{slugify(friend_id, 64)}.md"
        text = self.read_text(relative)
        if not text:
            return FriendProfile(friend_id=friend_id)
        data, body = parse_frontmatter(text)
        metadata = data.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        impression = _section(body, "印象")
        history = _section(body, "共同经历")
        return FriendProfile(
            friend_id=friend_id,
            description=str(data.get("description") or _first_line(body)),
            first_seen=_as_optional_str(metadata.get("first_seen")),
            last_seen=_as_optional_str(metadata.get("last_seen")),
            impression=impression.strip(),
            shared_history=history.strip(),
        )

    # ------------------------------------------------------------------
    def _atomic_write(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_path, path)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


def _section(body: str, title: str) -> str:
    match = re.search(rf"^##\s*{re.escape(title)}\s*$", body, re.MULTILINE)
    if not match:
        return ""
    rest = body[match.end() :]
    next_heading = re.search(r"^##\s+", rest, re.MULTILINE)
    return rest[: next_heading.start()] if next_heading else rest


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def timestamp_of_episode(episode: Episode) -> datetime | None:
    return parse_iso(episode.metadata.time)
