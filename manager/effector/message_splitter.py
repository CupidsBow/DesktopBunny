"""多消息拆分：只在自然边界切，不撕开 Markdown 结构。"""

from __future__ import annotations

import re

_SENTENCE_RE = re.compile(r"(?<=[。！？!?…；;\.])\s*|\n+")


def split_message(
    text: str,
    max_chars: int = 500,
    max_parts: int = 4,
) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    max_chars = max(1, int(max_chars or 500))
    max_parts = max(1, int(max_parts or 4))
    if len(text) <= max_chars or _has_structure(text):
        return [text]

    pieces = _sentence_pieces(text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(piece, max_chars))
            continue
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)

    if not chunks:
        return [text]
    if len(chunks) > max_parts:
        head = chunks[: max_parts - 1]
        tail = "\n".join(chunks[max_parts - 1 :])
        chunks = head + [tail]
    return [chunk for chunk in chunks if chunk]


def _has_structure(text: str) -> bool:
    if "```" in text:
        return True
    if "\n|" in text or text.startswith("|"):
        return True
    lines = text.splitlines()
    bullet = sum(
        1
        for line in lines
        if re.match(r"^\s*(?:[-*+] |\d+[.)]\s+)", line)
    )
    return bullet >= 2 and len(lines) > 3


def _sentence_pieces(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_RE.finditer(text):
        end = match.end()
        if end > start:
            pieces.append(text[start:end])
        start = end
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[index : index + limit] for index in range(0, len(text), limit)]


__all__ = ["split_message"]
