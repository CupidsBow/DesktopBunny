"""网页搜索与抓取（纯函数，不导入 manager）。"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'(?P<rest>.*?)(?=<a[^>]+class="result__a"|</body>)',
    re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def _strip_tags(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _clean_url(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        query = parse_qs(urlparse(href).query)
        if query.get("uddg"):
            return unquote(query["uddg"][0])
    return href


def search_web(query: str, limit: int = 5, timeout: float = 15) -> dict[str, Any]:
    """用 DuckDuckGo 的 HTML 页面做搜索，不需要 API key。"""
    query = (query or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    limit = max(1, min(int(limit or 5), 10))
    response = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "wt-wt"},
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    text = response.text
    results: list[dict[str, str]] = []
    for match in _RESULT_RE.finditer(text):
        if len(results) >= limit:
            break
        rest = match.group("rest") or ""
        snippet_match = _SNIPPET_RE.search(rest)
        results.append(
            {
                "title": _strip_tags(match.group("title")),
                "url": _clean_url(match.group("href")),
                "snippet": (
                    _strip_tags(snippet_match.group("snippet"))
                    if snippet_match
                    else ""
                ),
            }
        )
    return {"query": query, "results": results}


def fetch_page(
    url: str,
    timeout: float = 20,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """抓取网页并抽取可读正文。"""
    url = (url or "").strip()
    if not url:
        raise ValueError("url 不能为空")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    response = requests.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.lower() and "<html" not in response.text[:200].lower():
        text = response.text
    else:
        parser = _TextExtractor()
        try:
            parser.feed(response.text)
            parser.close()
        except Exception:
            text = _strip_tags(response.text)
        else:
            text = "\n".join(parser.parts)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    max_chars = max(500, int(max_chars or 20000))
    truncated = len(text) > max_chars
    return {
        "url": response.url,
        "status": response.status_code,
        "text": text[:max_chars],
        "truncated": truncated,
        "bytes": len(response.content),
    }


__all__ = ["fetch_page", "search_web"]
