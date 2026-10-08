"""Free/local network tools: HTTP, web search/extraction, RSS and URL metadata."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from server.src.config import Settings
from server.src.tools.verification.values import get_path

logger = logging.getLogger(__name__)


class NetworkTools:
    def __init__(self, settings: Settings) -> None:
        self._cfg = settings.tools.web
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._cfg.request_timeout_seconds,
            headers={"User-Agent": self._cfg.user_agent},
        )
        # Alternative sources come from untrusted search results, so this
        # client rejects any hop (redirects included) that resolves to a
        # non-public address instead of letting a search hit turn into SSRF.
        self._alt_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._cfg.request_timeout_seconds,
            headers={"User-Agent": self._cfg.user_agent},
            event_hooks={"request": [self._assert_public_request]},
        )

    async def close(self) -> None:
        await self._client.aclose()
        await self._alt_client.aclose()

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http:// and https:// URLs are supported")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname")
        return url

    @staticmethod
    def _normalize_page_url(url: str) -> str:
        """Support both plain URLs and Markdown-formatted links."""
        url = url.strip()
        match = re.fullmatch(
            r"\[[^\]]+\]\((https?://.+)\)",
            url,
            flags=re.I,
        )
        return match.group(1) if match else url

    @staticmethod
    def _fallback_query(url: str) -> str:
        """Create a search query from the failed URL."""
        parsed = urlparse(url)
        slug = unquote(parsed.path.rstrip("/").split("/")[-1])
        terms = re.sub(r"[^\w]+", " ", slug).strip()
        if len(terms) >= 12 and not terms.isdigit():
            return terms[:160]
        return f"{parsed.hostname or ''} {terms}".strip()[:160]

    @staticmethod
    async def _host_is_public(host: str) -> bool:
        """Resolve a host and require every returned address to be public."""
        if not host:
            return False
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            return literal.is_global
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except OSError:
            return False
        addresses = {info[4][0] for info in infos if info and len(info) > 4 and info[4]}
        if not addresses:
            return False
        try:
            return all(ipaddress.ip_address(a).is_global for a in addresses)
        except ValueError:
            return False

    @staticmethod
    async def _safe_alternative_url(
        url: str,
        original_host: str | None,
    ) -> bool:
        """Filter obvious unsafe or duplicate search targets."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host:
            return False
        if host.lower() == (original_host or "").lower():
            return False
        if host.lower() == "localhost" or host.lower().endswith(
            (".localhost", ".local")
        ):
            return False
        return await NetworkTools._host_is_public(host)

    async def _assert_public_request(self, request: httpx.Request) -> None:
        """Reject a fetch whose target does not resolve to a public address."""
        host = request.url.host or ""
        if not await self._host_is_public(host):
            raise httpx.RequestError(
                f"blocked non-public host {host!r}",
                request=request,
            )

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

    async def verified_http_mutation(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: Any | None = None,
        text_body: str | None = None,
        readback_url: str,
        readback_headers: dict[str, str] | None = None,
        expected_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform HTTP mutation, then GET the resource independently.

        The caller must provide an appropriate readback endpoint.
        """
        method = method.upper()

        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("verified_http_mutation requires a mutating HTTP method")

        action = await self.http_request(
            method=method,
            url=url,
            headers=headers,
            json_body=json_body,
            text_body=text_body,
        )

        if not (200 <= int(action["status"]) < 300):
            return {
                "ok": False,
                "verification_status": "action_failed",
                "action": action,
            }

        readback = await self.http_request(
            method="GET",
            url=readback_url,
            headers=readback_headers or headers,
        )

        if not (200 <= int(readback["status"]) < 300):
            return {
                "ok": False,
                "verification_status": "verification_failed",
                "action": action,
                "readback": readback,
            }

        comparisons: dict[str, bool] = {}

        if expected_json:
            actual_json = readback.get("json")

            if not isinstance(actual_json, dict):
                comparisons["json_object"] = False
            else:
                for key, expected in expected_json.items():
                    actual = get_path(actual_json, key)
                    comparisons[key] = actual == expected

        verified = all(comparisons.values()) if comparisons else True

        return {
            "ok": verified,
            "verification_status": "verified" if verified else "verification_failed",
            "action": action,
            "readback": readback,
            "comparisons": comparisons,
        }

    async def web_search(self, query: str, *, max_results: int = 8) -> list[dict[str, str]]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(max_results), 20))

        if self._cfg.searxng_url:
            endpoint = urljoin(self._cfg.searxng_url.rstrip("/") + "/", "search")
            try:
                response = await self._client.get(
                    endpoint,
                    params={"q": query, "format": "json", "language": "auto", "safesearch": 1},
                )
                response.raise_for_status()
                payload = response.json()
                return [
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("url") or ""),
                        "snippet": str(item.get("content") or ""),
                        "engine": str(item.get("engine") or "searxng"),
                    }
                    for item in payload.get("results", [])[:limit]
                ]
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                # A dead or rate-limited local instance must not make search
                # unavailable; the public fallback below still runs.
                logger.warning(
                    "SearXNG unavailable (%s); trying public search",
                    type(exc).__name__,
                )

        # No-key best-effort fallback. This may be rate-limited by DuckDuckGo;
        # a local SearXNG instance is the preferred production option, so this
        # path only runs when the configured instance is unavailable.
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

    @staticmethod
    def _extract_page(response: httpx.Response, max_chars: int) -> dict[str, Any]:
        """Turn a successful response into readable text plus its links."""
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

    async def web_extract(self, url: str, *, max_chars: int = 40_000) -> dict[str, Any]:
        """
        Fetch a web page.

        On a recoverable HTTP/network failure:
        - Search for alternative sources.
        - Try extracting an accessible result.
        - Preserve original failure information.
        - Return a nonfatal result if recovery fails.
        """
        url = self._validate_url(self._normalize_page_url(url))
        limit = max(1, min(max_chars, 40_000))
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return {"ok": True, **self._extract_page(response, limit)}
        except httpx.HTTPStatusError as exc:
            reason = f"HTTP {exc.response.status_code}"
        except httpx.TimeoutException:
            reason = "request_timeout"
        except httpx.RequestError:
            reason = "connection_error"

        # The original source failed. Find another.
        query = self._fallback_query(url)
        alternatives: list[dict[str, str]] = []
        search_error: str | None = None
        try:
            alternatives = await self.web_search(query, max_results=8)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            search_error = type(exc).__name__
            logger.warning("Fallback search for %s failed (%s)", url, search_error)

        original_host = urlparse(url).hostname
        visited: set[str] = set()
        attempted = 0
        for item in alternatives:
            # Bounded recovery: at most a few distinct hosts, and never the
            # page that just failed. This is not a retry loop.
            if attempted >= 3:
                break
            candidate = self._normalize_page_url(str(item.get("url") or ""))
            if not candidate or candidate in visited:
                continue
            visited.add(candidate)
            if not await self._safe_alternative_url(candidate, original_host):
                continue
            attempted += 1
            try:
                alternative = await self._alt_client.get(candidate)
                alternative.raise_for_status()
            except httpx.HTTPError:
                continue
            return {
                "ok": True,
                "recovered": True,
                "reason": reason,
                "original_url": url,
                "search_query": query,
                **self._extract_page(alternative, limit),
            }

        return {
            "ok": False,
            "recovered": False,
            "reason": reason,
            "url": url,
            "search_query": query,
            "search_error": search_error,
            "alternatives_attempted": attempted,
            "message": (
                f"{url} failed ({reason}) and no accessible alternative "
                "source was found. Say what could not be verified instead "
                "of guessing at the page's contents."
            ),
        }

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
