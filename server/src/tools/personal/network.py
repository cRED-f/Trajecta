"""Free/local network tools: HTTP, web search/extraction, RSS and URL metadata."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from server.src.config import Settings


class NetworkTools:
    def __init__(self, settings: Settings) -> None:
        self._cfg = settings.tools.web
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._cfg.request_timeout_seconds,
            headers={"User-Agent": self._cfg.user_agent},
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http:// and https:// URLs are supported")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname")
        return url

    async def http_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        json_body: Any | None = None,
        text_body: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = self._validate_url(url)
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            raise ValueError("Unsupported HTTP method")
        if json_body is not None and text_body is not None:
            raise ValueError("Provide json_body or text_body, not both")

        timeout = timeout_seconds or self._cfg.request_timeout_seconds
        async with self._client.stream(
            method,
            url,
            headers=headers,
            params=query,
            json=json_body,
            content=text_body,
            timeout=timeout,
        ) as response:
            chunks: list[bytes] = []
            size = 0
            truncated = False
            async for chunk in response.aiter_bytes():
                remaining = self._cfg.max_response_bytes - size
                if remaining <= 0:
                    truncated = True
                    break
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    size += remaining
                    truncated = True
                    break
                chunks.append(chunk)
                size += len(chunk)

            raw = b"".join(chunks)
            content_type = response.headers.get("content-type", "")
            text: str | None = None
            data: Any | None = None
            if "application/json" in content_type:
                try:
                    data = json.loads(raw.decode(response.encoding or "utf-8", errors="replace"))
                except json.JSONDecodeError:
                    text = raw.decode(response.encoding or "utf-8", errors="replace")
            elif content_type.startswith("text/") or "xml" in content_type or "html" in content_type:
                text = raw.decode(response.encoding or "utf-8", errors="replace")
            else:
                text = f"<binary response: {len(raw)} bytes, content-type={content_type or 'unknown'}>"

            safe_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"set-cookie", "authorization", "proxy-authorization"}
            }
            return {
                "status": response.status_code,
                "url": str(response.url),
                "headers": safe_headers,
                "json": data,
                "text": text,
                "bytes_read": len(raw),
                "truncated": truncated,
            }

    async def web_search(self, query: str, *, max_results: int = 8) -> list[dict[str, str]]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(max_results), 20))

        if self._cfg.searxng_url:
            endpoint = urljoin(self._cfg.searxng_url.rstrip("/") + "/", "search")
            response = await self._client.get(
                endpoint,
                params={"q": query, "format": "json", "language": "auto", "safesearch": 1},
            )
            response.raise_for_status()
            payload = response.json()
            results = []
            for item in payload.get("results", [])[:limit]:
                results.append(
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("url") or ""),
                        "snippet": str(item.get("content") or ""),
                        "engine": str(item.get("engine") or "searxng"),
                    }
                )
            return results

        # No-key best-effort fallback. This may be rate-limited by DuckDuckGo;
        # a local SearXNG instance is the preferred production option.
        response = await self._client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "User-Agent": self._cfg.user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        html = response.text
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise RuntimeError("beautifulsoup4 is required for web_search fallback") from exc

        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []
        for result in soup.select(".result"):
            anchor = result.select_one("a.result__a")
            if anchor is None:
                continue
            href = str(anchor.get("href") or "")
            # DDG often wraps the real URL in uddg=.
            if "uddg=" in href:
                parsed = urlparse(href)
                href = unquote(parse_qs(parsed.query).get("uddg", [href])[0])
            snippet_node = result.select_one(".result__snippet")
            results.append(
                {
                    "title": anchor.get_text(" ", strip=True),
                    "url": href,
                    "snippet": snippet_node.get_text(" ", strip=True) if snippet_node else "",
                    "engine": "duckduckgo-html",
                }
            )
            if len(results) >= limit:
                break
        return results

    async def web_extract(self, url: str, *, max_chars: int = 40_000) -> dict[str, Any]:
        url = self._validate_url(url)
        response = await self._client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            text = response.text[:max_chars]
            return {"url": str(response.url), "title": "", "text": text, "links": []}

        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise RuntimeError("beautifulsoup4 is required for web_extract") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "noscript", "svg", "canvas"]):
            node.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        root = soup.find("main") or soup.find("article") or soup.body or soup
        text = "\n".join(
            line.strip() for line in root.get_text("\n").splitlines() if line.strip()
        )
        text = re.sub(r"\n{3,}", "\n\n", unescape(text))[: max(1, max_chars)]

        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for anchor in root.find_all("a", href=True):
            href = urljoin(str(response.url), str(anchor["href"]))
            if href in seen or not href.startswith(("http://", "https://")):
                continue
            seen.add(href)
            links.append({"text": anchor.get_text(" ", strip=True)[:200], "url": href})
            if len(links) >= 100:
                break
        return {"url": str(response.url), "title": title, "text": text, "links": links}

    async def rss_read(self, url: str, *, max_items: int = 20) -> dict[str, Any]:
        url = self._validate_url(url)
        response = await self._client.get(url)
        response.raise_for_status()
        try:
            import feedparser
        except ImportError as exc:
            raise RuntimeError("feedparser is required for rss_read") from exc
        parsed = feedparser.loads(response.content)
        items: list[dict[str, Any]] = []
        for entry in parsed.entries[: max(1, min(max_items, 100))]:
            items.append(
                {
                    "title": str(entry.get("title") or ""),
                    "link": str(entry.get("link") or ""),
                    "published": str(entry.get("published") or entry.get("updated") or ""),
                    "summary": re.sub(r"<[^>]+>", " ", str(entry.get("summary") or ""))[:4_000],
                }
            )
        return {
            "title": str(parsed.feed.get("title") or ""),
            "link": str(parsed.feed.get("link") or url),
            "items": items,
        }

    async def url_metadata(self, url: str) -> dict[str, Any]:
        url = self._validate_url(url)
        response = await self._client.get(url)
        response.raise_for_status()
        title = ""
        description = ""
        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            try:
                from bs4 import BeautifulSoup
            except ImportError as exc:
                raise RuntimeError("beautifulsoup4 is required for url_metadata") from exc
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
                "meta", attrs={"property": "og:description"}
            )
            if meta:
                description = str(meta.get("content") or "")
        return {
            "url": str(response.url),
            "status": response.status_code,
            "content_type": content_type,
            "content_length": response.headers.get("content-length"),
            "title": title,
            "description": description,
        }
