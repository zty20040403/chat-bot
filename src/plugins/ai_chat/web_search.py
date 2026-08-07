from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchError(RuntimeError):
    pass


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._capture_title = False
        self._capture_snippet = False
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []
        self._current_url = ""
        self._pending_title = ""
        self._pending_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value or "" for name, value in attrs}
        classes = set(attr_map.get("class", "").split())

        if tag == "a" and "result__a" in classes:
            self._push_pending_result("")
            self._capture_title = True
            self._current_title = []
            self._current_url = _clean_duckduckgo_url(attr_map.get("href", ""))
            return

        if "result__snippet" in classes or "result-snippet" in classes:
            self._capture_snippet = True
            self._current_snippet = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title.append(data)
        if self._capture_snippet:
            self._current_snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
            self._pending_title = _clean_text("".join(self._current_title))
            self._pending_url = self._current_url
            return

        if self._capture_snippet and tag in {"a", "div", "td"}:
            self._capture_snippet = False
            snippet = _clean_text("".join(self._current_snippet))
            self._push_pending_result(snippet)

    def close(self) -> None:
        super().close()
        self._push_pending_result("")

    def _push_pending_result(self, snippet: str) -> None:
        if not self._pending_title or not self._pending_url:
            return

        self.results.append(
            SearchResult(
                title=self._pending_title,
                url=self._pending_url,
                snippet=snippet,
            )
        )
        self._pending_title = ""
        self._pending_url = ""


def should_search(text: str) -> bool:
    normalized = text.strip().lower()
    return any(
        keyword in normalized
        for keyword in (
            "搜索",
            "搜一下",
            "查一下",
            "查查",
            "联网",
            "网上",
            "最新",
            "今天",
            "现在",
            "刚刚",
            "新闻",
            "价格",
            "官网",
            "发布",
            "更新",
        )
    )


async def search_web(
    query: str,
    max_results: int = 5,
    timeout_seconds: int = 10,
    freshness: str | None = None,
) -> list[SearchResult]:
    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=True, headers=headers
    ) as client:
        try:
            params = {"q": cleaned_query}
            if freshness in {"d", "w", "m", "y"}:
                params["df"] = freshness
            response = await client.get(
                "https://duckduckgo.com/html/",
                params=params,
            )
            response.raise_for_status()
            duckduckgo_results = _parse_duckduckgo_results(response.text)
        except httpx.HTTPError:
            duckduckgo_results = []

        if duckduckgo_results:
            return _unique_results(duckduckgo_results, max_results)

        try:
            response = await client.get(
                "https://www.bing.com/search",
                params={
                    "q": cleaned_query,
                    "format": "rss",
                    "setlang": "zh-hans",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError("联网搜索失败。") from exc

    return _unique_results(
        _parse_bing_rss_results(response.text),
        max_results,
    )


def _parse_duckduckgo_results(html: str) -> list[SearchResult]:
    parser = _DuckDuckGoHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.results


def _parse_bing_rss_results(xml: str) -> list[SearchResult]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    results: list[SearchResult] = []
    for item in root.findall("./channel/item"):
        title = _clean_text(item.findtext("title", ""))
        url = item.findtext("link", "").strip()
        snippet = _clean_text(item.findtext("description", ""))
        if title and url:
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                )
            )
    return results


def _unique_results(
    results: list[SearchResult],
    max_results: int,
) -> list[SearchResult]:
    unique_results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for result in results:
        if not result.title or not result.url or result.url in seen_urls:
            continue
        seen_urls.add(result.url)
        unique_results.append(result)
        if len(unique_results) >= max_results:
            break

    return unique_results


def render_search_context(results: list[SearchResult]) -> str:
    if not results:
        return ""

    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(
            f"[{index}] {result.title}\n"
            f"URL: {result.url}\n"
            f"摘要: {result.snippet or '无摘要'}"
        )
    return "\n\n".join(lines)


def render_search_sources(results: list[SearchResult], max_sources: int = 3) -> str:
    if not results:
        return ""

    lines = ["来源："]
    for index, result in enumerate(results[:max_sources], start=1):
        lines.append(f"{index}. {result.title}\n{result.url}")
    return "\n".join(lines)


def render_direct_search_results(
    results: list[SearchResult],
    max_results: int = 5,
) -> str:
    if not results:
        return ""

    lines = ["搜索结果："]
    for index, result in enumerate(results[:max_results], start=1):
        lines.append(
            f"{index}. {result.title}\n"
            f"{result.snippet or '无摘要'}\n"
            f"{result.url}"
        )
    return "\n\n".join(lines)


def search_freshness(text: str) -> str | None:
    normalized = text.strip().lower()
    if any(
        keyword in normalized
        for keyword in (
            "今天",
            "今日",
            "刚刚",
            "实时",
            "现在",
            "目前",
            "此刻",
            "天气",
            "比分",
            "价格",
            "行情",
        )
    ):
        return "d"
    if any(
        keyword in normalized
        for keyword in (
            "最新",
            "新闻",
            "近期",
            "最近",
            "本周",
            "发布",
            "更新",
            "新版",
            "新版本",
        )
    ):
        return "w"
    return None


def _clean_duckduckgo_url(href: str) -> str:
    if not href:
        return ""

    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://duckduckgo.com" + href

    parsed = urlsplit(href)
    query = parse_qs(parsed.query)
    redirected_url = query.get("uddg", [""])[0]
    if redirected_url:
        return unquote(redirected_url)

    return href


def _clean_text(text: str) -> str:
    return " ".join(text.split())
